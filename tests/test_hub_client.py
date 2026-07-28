"""The client half: it must look exactly like a store to everything above it.

`RemoteStore` is deliberately not a subclass of `Store`. It has no data of its own and
its writes would be lies, and inheriting would make every method it forgot return a
plausible empty answer instead of failing where it is written. So it is checked against
`state.READ_API` — the list the interface's own calls were grepped into — and against a
real hub over a real socket.
"""

import json
import threading
import urllib.error

import pytest

from core import config as cfg_mod
from core import engine as engine_mod
from core import hub_client, hub_server
from core import state as st


@pytest.fixture
def pair(monkeypatch):
    """A hub and a client, both real, talking over loopback."""
    cfg = cfg_mod.Config(
        machines=[cfg_mod.Machine(),
                  cfg_mod.Machine(name="sd", kind="linux", address="hanadev")],
        services=[cfg_mod.Service(name="AppEngine", label="CompuTec AppEngine"),
                  cfg_mod.Service(name="webclient.service", machine="sd")])
    holder = {"cfg": cfg}
    engine = engine_mod.Engine(lambda: holder["cfg"], store=st.Store(),
                               on_config_saved=lambda config:
                                   holder.update(cfg=config))
    monkeypatch.setattr(hub_server.hub_auth, "check", lambda token: "tests")
    monkeypatch.setattr(hub_server.hub_auth, "note_seen", lambda name: None)
    server = hub_server.HubServer(engine, host="127.0.0.1", port=0, insecure=True)
    server.start()
    client = hub_client.HubClient(server.url, "good")
    client.start()
    assert client.wait_for(lambda: client.connected, timeout=10), "never connected"
    yield client, engine, server, holder
    client.stop()
    server.stop()


# ---------------------------------------------------------------------------
# the surface
# ---------------------------------------------------------------------------
def test_the_remote_store_satisfies_the_read_api(pair):
    client, _engine, _server, _holder = pair
    missing = [name for name in st.READ_API
               if not callable(getattr(client.store, name, None))]
    assert missing == []


def test_it_is_not_a_subclass(pair):
    """On purpose. Inheriting would hide a forgotten method behind an empty answer
    from the parent, which is the failure that is hardest to notice."""
    client, _engine, _server, _holder = pair
    assert not isinstance(client.store, st.Store)


def test_a_write_is_refused_rather_than_pretended(pair):
    """The hub is the only writer. A client that quietly updated its own copy would
    show something nobody else could see, which is worse than an error."""
    client, _engine, _server, _holder = pair
    with pytest.raises(hub_client.ReadOnly):
        client.store.update("AppEngine", st.STOPPED)


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------
def test_it_reads_what_the_hub_knows(pair):
    client, engine, _server, _holder = pair
    engine.store.update("AppEngine", st.RUNNING)
    client.refresh_now()

    assert client.store.status_of("AppEngine") == st.RUNNING
    assert client.store.counts()[0] >= 1


def test_it_carries_health_start_type_and_machines(pair):
    """Everything a row draws, not only the status — the panel asks for all of it and
    a missing one shows as a blank chip nobody can explain."""
    client, engine, _server, _holder = pair
    engine.store.set_health("webclient.service", "starting", "waiting for it to answer",
                            machine="sd")
    engine.store.set_start_type("AppEngine", "Disabled")
    engine.store.note_machine("sd", False, "TimeoutError: timed out")
    client.refresh_now()

    assert client.store.health_of("webclient.service", "sd") == "starting"
    assert client.store.health_detail("webclient.service", "sd") == \
        "waiting for it to answer"
    assert client.store.start_type("AppEngine") == "Disabled"
    assert client.store.is_disabled("AppEngine") is True
    machine = client.store.machine_state("sd")
    assert machine["reachable"] is False
    assert "timed out" in machine["detail"]


# ---------------------------------------------------------------------------
# the stream
# ---------------------------------------------------------------------------
def test_a_change_on_the_hub_arrives_without_asking(pair):
    client, engine, _server, _holder = pair
    seen = []
    client.store.subscribe(lambda event: seen.append(event.status))

    engine.store.update("AppEngine", st.STOPPED)

    assert client.wait_for(
        lambda: client.store.status_of("AppEngine") == st.STOPPED, timeout=10)
    assert st.STOPPED in seen


def test_the_event_reaches_a_subscriber_as_a_state_event(pair):
    """The app's own handlers take an st.Event, and they are not being rewritten for
    the client — so what arrives has to be one, `previous` included."""
    client, engine, _server, _holder = pair
    engine.store.update("AppEngine", st.RUNNING)
    client.refresh_now()
    caught = []
    client.store.subscribe(caught.append)

    engine.store.update("AppEngine", st.STOPPED, exit_code=1067, pid=0)

    assert client.wait_for(lambda: bool(caught), timeout=10)
    event = caught[0]
    assert isinstance(event, st.Event)
    assert event.name == "AppEngine" and event.status == st.STOPPED
    assert event.previous == st.RUNNING
    assert event.state.exit_code == 1067
    assert event.crashed is True          # the property the watchdog reads


# ---------------------------------------------------------------------------
# acting
# ---------------------------------------------------------------------------
def test_an_action_is_sent_and_answered_with_an_id(pair, monkeypatch):
    client, engine, _server, _holder = pair
    asked = []
    monkeypatch.setattr(engine, "act",
                        lambda action, service, machine="", actor="", bulk=False:
                            asked.append((action, service, actor)) or "id-7")

    assert client.act("restart", "AppEngine", actor="ismail") == "id-7"
    assert asked == [("restart", "AppEngine", "ismail")]


def test_a_busy_service_raises_busy_with_the_name(pair, monkeypatch):
    client, engine, _server, _holder = pair

    def busy(action, service, machine="", actor="", bulk=False):
        raise engine_mod.Busy("ismail", "restart", 1000.0)
    monkeypatch.setattr(engine, "act", busy)

    with pytest.raises(hub_client.Busy) as raised:
        client.act("restart", "AppEngine", actor="ayse")
    assert raised.value.actor == "ismail"
    assert "ismail" in str(raised.value)


# ---------------------------------------------------------------------------
# the config
# ---------------------------------------------------------------------------
def test_a_conflict_is_raised_not_swallowed(pair):
    client, _engine, _server, _holder = pair
    cfg, _etag = client.config()

    with pytest.raises(hub_client.Conflict) as raised:
        client.save_config(cfg, "0000000000000000", actor="tests")
    assert raised.value.etag, "the current etag has to come back for a merge"


def test_a_config_it_was_just_given_saves(pair, monkeypatch):
    client, engine, _server, _holder = pair
    saved = []
    monkeypatch.setattr(engine, "save_config", lambda config: saved.append(config))

    cfg, etag = client.config()
    cfg.services.append(cfg_mod.Service(name="WMSServer"))
    client.save_config(cfg, etag, actor="tests")

    assert [s.name for s in saved[0].services][-1] == "WMSServer"


# ---------------------------------------------------------------------------
# when things are wrong
# ---------------------------------------------------------------------------
def test_an_unreachable_hub_is_its_own_error():
    client = hub_client.HubClient("http://127.0.0.1:9", "t")
    with pytest.raises(hub_client.Unreachable):
        client.ping()


def test_a_refused_token_is_its_own_error(pair, monkeypatch):
    client, _engine, server, _holder = pair
    monkeypatch.setattr(hub_server.hub_auth, "check", lambda token: "")

    with pytest.raises(hub_client.Refused):
        client.refresh_now()


def test_a_changed_certificate_is_refused(monkeypatch):
    """Same rule as the SSH host key: a hub that is suddenly a different hub is not to
    be trusted just because it answers."""
    client = hub_client.HubClient("https://127.0.0.1:9", "t",
                                 fingerprint="SHA256:something-else")
    monkeypatch.setattr(client, "peer_fingerprint", lambda: "SHA256:not-that")

    with pytest.raises(hub_client.WrongHub):
        client.check_identity()


def test_the_first_connection_pins_what_it_finds(monkeypatch):
    """With nothing pinned yet, the first fingerprint is adopted and handed back so the
    caller can store it — the same shape as the Machines page's "Get it"."""
    client = hub_client.HubClient("https://127.0.0.1:9", "t")
    monkeypatch.setattr(client, "peer_fingerprint", lambda: "SHA256:first-time")

    assert client.check_identity() == "SHA256:first-time"
    assert client.fingerprint == "SHA256:first-time"


def test_the_client_survives_the_hub_going_away_and_coming_back(pair):
    """The hub will be restarted — for an upgrade, for a reboot — and a client that
    needs restarting too is a client somebody has to remember to restart."""
    client, engine, server, _holder = pair

    server.stop()
    assert client.wait_for(lambda: client.connected is False, timeout=15)

    server.start()
    assert client.wait_for(lambda: client.connected is True, timeout=45)
    # A fresh snapshot on reconnect, not just a resumed stream: whatever happened while
    # it was away was never in the stream to begin with.
    engine.store.update("AppEngine", st.STOPPED)
    assert client.wait_for(
        lambda: client.store.status_of("AppEngine") == st.STOPPED, timeout=15)


def test_nothing_that_happens_while_it_connects_is_lost(pair):
    """The gap this closes: a change that lands between the snapshot and the event stream
    being opened.

    It was found by the reconnect test above failing under load — the client announced
    itself connected before its stream existed, so a status change in that window went
    nowhere and, because a store only publishes *changes*, the client kept showing the old
    status indefinitely rather than briefly.

    The order is therefore: open the stream, then take the snapshot. The hub queues events
    per listener from the moment the stream opens, so anything that happens during the
    snapshot is delivered after it instead of being missed.
    """
    client, engine, _server, _holder = pair
    assert client.wait_for(lambda: client.connected is True, timeout=15)
    client.stop()

    # A change made at the worst possible moment: after this connection's snapshot has
    # been taken, which is exactly where the old order had no stream yet.
    real_refresh = client.refresh_now

    def refresh_then_change():
        answer = real_refresh()
        engine.store.update("AppEngine", st.STOPPED)
        return answer

    client.refresh_now = refresh_then_change
    client.start()

    assert client.wait_for(
        lambda: client.store.status_of("AppEngine") == st.STOPPED, timeout=15), \
        "a change during the handshake was lost"


def test_stop_actually_stops_so_start_works_again(pair):
    """`stop()` used to return while its reader was still blocked on the socket: `_stop`
    is only looked at between lines, and an idle stream says nothing for twenty seconds.

    That made the *next* `start()` a silent no-op — it refuses to run while the old
    thread is alive — so a client that was stopped and started again went on reading a
    stream nobody was managing. Two seconds, not twenty, and a live thread afterwards is
    the failure.
    """
    import time

    client, _engine, _server, _holder = pair
    assert client.wait_for(lambda: client.connected is True, timeout=15)

    began = time.monotonic()
    client.stop()
    took = time.monotonic() - began

    assert took < 3.0, f"stop() took {took:.1f}s"
    assert not (client._thread and client._thread.is_alive()), \
        "the reader outlived the stop that claimed to have finished"

    client.start()
    assert client.wait_for(lambda: client.connected is True, timeout=15), \
        "start() after stop() did nothing"
