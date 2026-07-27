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
    monkeypatch.setattr(hub_server.hub_auth, "note_seen", lambda name: None)

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
