"""A hub and a client in one process, doing the thing the product is for.

Every bug worth having a test for in this project has been an integration bug: a signal
never connected, a verdict overwritten a line later, a poller skipping the machine it was
built for, a client left blocked on a socket that would never speak again. Each of those
passed its own unit tests. This is the shape of test that catches them.

The service manager is the one thing not real here — a test that restarts a Windows
service cannot run on a build agent — but everything between the client's request and the
control call is exactly what ships.
"""

import pytest

from core import config as cfg_mod
from core import engine as engine_mod
from core import hub_client, hub_server
from core import state as st


@pytest.fixture
def system(monkeypatch):
    cfg = cfg_mod.Config(
        machines=[cfg_mod.Machine()],
        services=[cfg_mod.Service(name="AppEngine", label="CompuTec AppEngine"),
                  cfg_mod.Service(name="WMSServer", label="CompuTec WMS")])
    holder = {"cfg": cfg}
    states = {"AppEngine": st.RUNNING, "WMSServer": st.RUNNING}

    monkeypatch.setattr(engine_mod.control, "query_status",
                        lambda name, machine="": states.get(name, st.UNKNOWN))
    monkeypatch.setattr(engine_mod.control, "start_type",
                        lambda name, machine="": "Automatic")
    monkeypatch.setattr(engine_mod.control, "stop_service",
                        lambda name, machine="": states.update({name: st.STOPPED}))
    monkeypatch.setattr(engine_mod.control, "start_service",
                        lambda name, machine="": states.update({name: st.RUNNING}))
    monkeypatch.setattr(engine_mod.control, "restart_service",
                        lambda name, machine="": states.update({name: st.RUNNING}))
    monkeypatch.setattr(hub_server.hub_auth, "check", lambda token: "tests")
    monkeypatch.setattr(hub_server.hub_auth, "note_seen", lambda name, host="": None)

    engine = engine_mod.Engine(lambda: holder["cfg"], store=st.Store(),
                               on_config_saved=lambda config:
                                   holder.update(cfg=config))
    engine.prime_states()
    server = hub_server.HubServer(engine, host="127.0.0.1", port=0, insecure=True)
    server.start()
    client = hub_client.HubClient(server.url, "good")
    client.start()
    assert client.wait_for(lambda: client.connected, timeout=10)
    yield client, engine, server, states, holder
    client.stop()
    server.stop()


def test_an_action_from_the_client_reaches_the_service_and_comes_back(system):
    """The whole product, in one assertion each way: it happened, and the client saw
    that it happened without asking."""
    client, _engine, _server, states, _holder = system
    assert client.store.status_of("AppEngine") == st.RUNNING

    client.act("stop", "AppEngine", actor="tests")

    assert client.wait_for(
        lambda: client.store.status_of("AppEngine") == st.STOPPED,
        timeout=10), "the client never saw its own action land"
    assert states["AppEngine"] == st.STOPPED


def test_the_other_client_sees_it_too(system):
    """Five people, one landscape. A second client is not told separately — it is on
    the same stream, which is the point of the stream."""
    client, _engine, server, _states, _holder = system
    second = hub_client.HubClient(server.url, "good")
    second.start()
    try:
        assert second.wait_for(lambda: second.connected, timeout=10)

        client.act("stop", "WMSServer", actor="ismail")

        assert second.wait_for(
            lambda: second.store.status_of("WMSServer") == st.STOPPED, timeout=10)
    finally:
        second.stop()


def test_a_config_change_from_the_client_is_what_the_hub_then_runs(system, tmp_path,
                                                                  monkeypatch):
    """Not just accepted — *applied*, so the engine's next answer includes it."""
    client, engine, _server, _states, holder = system
    monkeypatch.setattr(engine_mod.cfg_mod, "save",
                        lambda cfg, path=None: None)     # do not touch ProgramData

    cfg, etag = client.config()
    cfg.services.append(cfg_mod.Service(name="MSSQLSERVER", label="SQL Server"))
    client.save_config(cfg, etag, actor="tests")

    assert client.wait_for(
        lambda: any(r["name"] == "MSSQLSERVER"
                    for r in client.refresh_now()["services"]), timeout=10)
    assert [s.name for s in engine.config().services][-1] == "MSSQLSERVER"


def test_two_clients_editing_at_once_is_a_conflict_not_a_loss(system, monkeypatch):
    """The reason the etag exists: whoever saves second is told, rather than quietly
    dropping what the first one added."""
    client, engine, server, _states, _holder = system
    monkeypatch.setattr(engine_mod.cfg_mod, "save", lambda cfg, path=None: None)
    second = hub_client.HubClient(server.url, "good")

    mine, my_etag = client.config()
    theirs, their_etag = second.config()          # same etag, both loaded now

    mine.services.append(cfg_mod.Service(name="Mine"))
    client.save_config(mine, my_etag, actor="ismail")

    theirs.services.append(cfg_mod.Service(name="Theirs"))
    with pytest.raises(hub_client.Conflict) as raised:
        second.save_config(theirs, their_etag, actor="ayse")

    # And the refusal carries what they need to try again.
    assert raised.value.etag
    fresh, fresh_etag = second.config()
    assert fresh_etag == raised.value.etag
    assert [s.name for s in fresh.services][-1] == "Mine"


def test_the_client_survives_the_hub_restarting_under_it(system):
    """An upgrade stops the service, replaces it and starts it again. A client that
    needed restarting too is a client somebody has to remember to restart."""
    client, engine, server, _states, _holder = system

    server.stop()
    assert client.wait_for(lambda: client.connected is False, timeout=15)

    server.start()
    assert client.wait_for(lambda: client.connected is True, timeout=45)

    # Whatever changed while it was away has to arrive, which is what the fresh
    # snapshot on reconnect is for — the stream cannot carry what it missed.
    engine.store.update("WMSServer", st.STOPPED)
    assert client.wait_for(
        lambda: client.store.status_of("WMSServer") == st.STOPPED, timeout=15)


def test_a_client_that_cannot_act_still_reads(system, monkeypatch):
    """A refused action must not take the connection down with it: the panel that
    cannot restart something is still the panel that shows what is running."""
    client, engine, _server, _states, _holder = system

    def busy(action, service, machine="", actor="", bulk=False):
        raise engine_mod.Busy("somebody", "restart", 0.0)
    monkeypatch.setattr(engine, "act", busy)

    with pytest.raises(hub_client.Busy):
        client.act("restart", "AppEngine", actor="tests")

    assert client.connected is True
    assert client.store.status_of("AppEngine") == st.RUNNING


def test_the_hubs_history_says_which_person_asked(system, tmp_path, monkeypatch):
    """The reason the column exists. Five clients act on one landscape and the hub is
    the only place that sees all of it — so "who stopped AppEngine at 03:00" has to be
    answerable from the hub's own history, not from asking five people."""
    from core import history

    client, engine, _server, _states, holder = system
    path = str(tmp_path / "history.db")
    monkeypatch.setattr(history, "HISTORY_PATH", path)
    holder["cfg"].history.enabled = True

    client.act("stop", "AppEngine", actor="CT\ismail.orhan")
    assert engine.wait_for_actions(timeout=10)

    asked = [r for r in history.read(path=path) if r.get("action") == "stop"]
    assert asked, "the action was never recorded"
    assert asked[0]["actor"] == "CT\ismail.orhan"


# ---------------------------------------------------------------------------
# issuing a client's token from the panel
# ---------------------------------------------------------------------------
# The hub has to do this rather than the panel: the client list is in a DPAPI store the
# installer leaves writable by administrators only, and the panel does not run elevated.
# A token is returned *once*, with the command to run on the machine it is for; after that
# there is nothing to show, because the store keeps a SHA-256 and nothing else.
def test_a_token_is_issued_once_and_then_only_described(system, monkeypatch):
    from core import hub_auth

    client, _engine, _server, _states, _holder = system
    issued = {}

    def add(name, description=""):
        issued[name] = description
        return "a-real-token"

    monkeypatch.setattr(hub_auth, "add_client", add)
    monkeypatch.setattr(hub_auth, "clients",
                        lambda: [{"name": name, "description": note,
                                  "added": "2026-07-28T09:00:00Z",
                                  "last_seen": "", "host": ""}
                                 for name, note in issued.items()])

    made = client.add_client("ismail-laptop", "the laptop on my desk")

    assert made["token"] == "a-real-token"
    assert made["name"] == "ismail-laptop"
    assert made["url"].startswith("http")
    assert "--connect" in made["command"] and "a-real-token" in made["command"]
    assert issued["ismail-laptop"] == "the laptop on my desk"

    # And afterwards: a label, why it exists, when it was issued, when it was last used,
    # and where from. No token — the hub keeps a SHA-256 of it and nothing else.
    listed = client.clients()
    assert [c["name"] for c in listed["clients"]] == ["ismail-laptop"]
    assert set(listed["clients"][0]) == {"name", "description", "added",
                                        "last_seen", "host"}
    assert "token" not in listed["clients"][0]


def test_a_client_reports_which_machine_it_is(system, monkeypatch):
    """The name on a token is a label somebody typed. The host name arrives with the
    connection — so the list can say what a token was *meant* for and what it is actually
    being used from, which are not always the same sentence.

    It identifies; it does not authenticate. The token does that.
    """
    import socket

    from core import hub_auth

    client, _engine, _server, _states, _holder = system
    seen = []
    monkeypatch.setattr(hub_auth, "note_seen",
                        lambda name, host="": seen.append((name, host)))

    client.refresh_now()

    assert seen, "the hub never recorded the request"
    assert seen[-1][1] == socket.gethostname()


def test_a_nameless_client_is_refused(system):
    """The name is the only thing that will identify it afterwards."""
    client, _engine, _server, _states, _holder = system

    with pytest.raises(RuntimeError) as raised:
        client.add_client("   ")
    assert "name" in str(raised.value)


def test_revoking_says_whether_there_was_anything_to_revoke(system, monkeypatch):
    from core import hub_auth

    client, _engine, _server, _states, _holder = system
    monkeypatch.setattr(hub_auth, "revoke", lambda name: name == "ismail-laptop")

    assert client.revoke_client("ismail-laptop") is True
    assert client.revoke_client("nobody") is False, \
        "a name that was never paired should be an answer, not an exception"


def test_a_machine_that_starts_answering_reaches_the_client(system):
    """The whole point of the hub: what it learns, the panel sees.

    Reachability was the one thing it never sent. The panel connected, took a snapshot in
    which nothing had asked the machine yet — the first OpenSCManager against a machine
    across a forest boundary takes 21 seconds, measured — and then stayed on `waiting` for
    the rest of the session while that machine's services streamed in as Running. Seen on
    2026-07-29, and it read as the hub contradicting itself.
    """
    client, engine, _server, _states, _holder = system

    assert client.store.machine_state("sc-sql") == {}, "nothing has asked it yet"

    engine._call(engine._on_machine, machine="sc-sql", reachable=True, detail="")
    assert client.wait_for(
        lambda: client.store.machine_state("sc-sql").get("reachable") is True, timeout=10)

    engine._call(engine._on_machine, machine="sc-sql", reachable=False,
                 detail="it did not answer")
    assert client.wait_for(
        lambda: client.store.machine_state("sc-sql").get("reachable") is False, timeout=10)
    assert client.store.machine_state("sc-sql")["detail"] == "it did not answer",         "a silent machine has to arrive with the reason, not just a red chip"


def test_the_engines_own_listener_still_hears_it():
    """`also_on_machine` adds one; a hub that replaced it would have taken the tray's."""
    from core import engine as engine_mod

    heard = []
    engine = engine_mod.Engine(lambda: None, on_machine=lambda **f: heard.append(f))
    engine.also_on_machine(lambda **f: heard.append({"second": True}))
    engine._call(engine._on_machine, machine="sc-sql", reachable=True, detail="")

    assert len(heard) == 2, "one of the two listeners was dropped"
    assert heard[0]["machine"] == "sc-sql"


def test_a_client_is_told_when_its_action_finishes(system):
    """The hub answers 202 to an action — accepted, not done. Nothing then closed the loop, so
    a connected panel left the row reading "Stopping…" for the rest of the session, with all
    four buttons disabled, while the counter beside it already said "1 stopped". Seen on
    2026-07-29 on AppEngine, with services.msc confirming it had stopped."""
    client, _engine, _server, states, _holder = system
    heard = []
    client._on_event = heard.append

    client.act("stop", "AppEngine")

    assert client.wait_for(
        lambda: any(e.get("kind") == "action" for e in heard), timeout=15),         "the action finished and the client was never told"
    done = next(e for e in heard if e.get("kind") == "action")
    assert done["service"] == "AppEngine"
    assert done["action"] == "stop"
    assert done["error"] == ""
    assert done["status"], "no resulting status, so nothing can clear a busy label safely"
    assert states["AppEngine"] == st.STOPPED


def test_a_client_is_told_when_its_action_fails(system, monkeypatch):
    """The worse half: a refused action changes no status at all, so a client watching only
    status events hears nothing whatsoever. Press Stop, it is refused, and the panel says
    nothing — which is how somebody concludes the product did it."""
    client, engine, _server, _states, _holder = system
    heard = []
    client._on_event = heard.append

    def refuse(name, machine=""):
        raise OSError("Access is denied")

    monkeypatch.setattr(engine_mod.control, "stop_service", refuse)
    client.act("stop", "AppEngine")

    assert client.wait_for(
        lambda: any(e.get("kind") == "action" for e in heard), timeout=15)
    done = next(e for e in heard if e.get("kind") == "action")
    assert "denied" in done["error"].lower(), f"the reason was lost: {done['error']!r}"
