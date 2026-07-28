"""The hub over a real socket, with a real client, on loopback.

Not mocked. The framing of an event stream, the shape of a 409 and the moment a token
is rejected are exactly the things a mock will agree to and a client will not — and this
is the seam where a mistake shows up as "the panel is empty" with nothing in any log.
"""

import json
import threading
import urllib.error
import urllib.request

import pytest

from core import config as cfg_mod
from core import engine as engine_mod
from core import hub_server
from core import state as st
from core import wire


@pytest.fixture
def hub(monkeypatch):
    """A real server on a real loopback port, in front of a real engine.

    Plain HTTP: TLS is tested where the certificate is (test_hub_auth), and a
    handshake per request here would only slow the thing down. `insecure=True` exists
    for exactly this and warns every time it is used.
    """
    cfg = cfg_mod.Config(
        machines=[cfg_mod.Machine(),
                  cfg_mod.Machine(name="sd", kind="linux", address="hanadev")],
        services=[cfg_mod.Service(name="AppEngine", label="CompuTec AppEngine"),
                  cfg_mod.Service(name="webclient.service", machine="sd")])
    holder = {"cfg": cfg}
    built = engine_mod.Engine(lambda: holder["cfg"], store=st.Store(),
                              on_config_saved=lambda config:
                                  holder.update(cfg=config))
    monkeypatch.setattr(hub_server.hub_auth, "check",
                        lambda token: "tests" if token == "good" else "")
    monkeypatch.setattr(hub_server.hub_auth, "note_seen", lambda name, host="": None)
    server = hub_server.HubServer(built, host="127.0.0.1", port=0, insecure=True)
    server.start()
    yield server, built, holder
    server.stop()


def call(server, path, body=None, token="good", method=None, timeout=10):
    request = urllib.request.Request(
        server.url + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method or ("POST" if body is not None else "GET"),
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as answer:
        raw = answer.read().decode()
        return answer.status, (json.loads(raw) if raw else None)


# ---------------------------------------------------------------------------
# who may ask
# ---------------------------------------------------------------------------
def test_ping_needs_no_token(hub):
    """Something has to answer "is the hub there" before a token is in play — the
    Connection page asks it, and so does whoever is debugging with curl."""
    server, _engine, _holder = hub
    status, said = call(server, "/api/v1/ping", token="")

    assert status == 200
    assert said["protocol"] == wire.PROTOCOL
    assert said["version"] and said["name"]


def test_everything_else_needs_one(hub):
    server, _engine, _holder = hub
    for path in ("/api/v1/snapshot", "/api/v1/config", "/api/v1/history"):
        with pytest.raises(urllib.error.HTTPError) as raised:
            call(server, path, token="wrong")
        assert raised.value.code == 401, path


def test_a_missing_header_is_the_same_no(hub):
    server, _engine, _holder = hub
    request = urllib.request.Request(server.url + "/api/v1/snapshot")
    with pytest.raises(urllib.error.HTTPError) as raised:
        urllib.request.urlopen(request, timeout=10)
    assert raised.value.code == 401


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------
def test_a_snapshot_comes_back_whole(hub):
    server, engine, _holder = hub
    engine.store.update("AppEngine", st.RUNNING)
    engine.store.note_machine("sd", True)

    status, shot = call(server, "/api/v1/snapshot")

    assert status == 200
    rows = {r["name"]: r for r in shot["services"]}
    assert rows["AppEngine"]["status"] == st.RUNNING
    assert rows["AppEngine"]["label"] == "CompuTec AppEngine"
    assert len(shot["machines"]) == 2


def test_the_config_comes_back_with_an_etag_that_matches(hub):
    server, _engine, holder = hub
    status, payload = call(server, "/api/v1/config")

    assert status == 200
    assert payload["etag"] == wire.etag(holder["cfg"])
    back, tag = wire.config_from_payload(payload)
    assert [s.name for s in back.services] == ["AppEngine", "webclient.service"]
    assert tag == payload["etag"]


def test_an_unknown_path_says_so_rather_than_crashing(hub):
    server, _engine, _holder = hub
    with pytest.raises(urllib.error.HTTPError) as raised:
        call(server, "/api/v1/nonsense")
    assert raised.value.code == 404
    assert raised.value.read()          # a sentence, not an empty body


def test_the_wrong_method_is_405_not_404(hub):
    """A person meeting this with curl needs to know the path was right."""
    server, _engine, _holder = hub
    with pytest.raises(urllib.error.HTTPError) as raised:
        call(server, "/api/v1/snapshot", body={}, method="POST")
    assert raised.value.code == 405


def test_malformed_json_is_400_and_the_hub_stays_up(hub):
    server, _engine, _holder = hub
    request = urllib.request.Request(
        server.url + "/api/v1/actions", data=b"{not json",
        headers={"Authorization": "Bearer good",
                 "Content-Type": "application/json"})
    with pytest.raises(urllib.error.HTTPError) as raised:
        urllib.request.urlopen(request, timeout=10)
    assert raised.value.code == 400

    # Still answering afterwards, which is the half that matters.
    assert call(server, "/api/v1/ping", token="")[0] == 200


# ---------------------------------------------------------------------------
# acting
# ---------------------------------------------------------------------------
def test_an_action_is_accepted_with_an_id(hub, monkeypatch):
    server, engine, _holder = hub
    asked = []
    monkeypatch.setattr(engine, "act",
                        lambda action, service, machine="", actor="", bulk=False:
                            asked.append((action, service, machine, actor)) or "id-1")

    status, said = call(server, "/api/v1/actions",
                        {"action": "restart", "service": "AppEngine",
                         "actor": "ismail"})

    assert status == 202 and said["id"] == "id-1"
    assert asked == [("restart", "AppEngine", "", "ismail")]


def test_an_action_on_a_busy_service_is_409_with_who_holds_it(hub, monkeypatch):
    """Five clients, one landscape. The second person needs the name of the first,
    not a blank refusal."""
    server, engine, _holder = hub

    def busy(action, service, machine="", actor="", bulk=False):
        raise engine_mod.Busy("ismail", "restart", 1000.0)
    monkeypatch.setattr(engine, "act", busy)

    with pytest.raises(urllib.error.HTTPError) as raised:
        call(server, "/api/v1/actions",
             {"action": "restart", "service": "AppEngine", "actor": "ayse"})

    assert raised.value.code == 409
    said = json.loads(raised.value.read().decode())
    assert said["actor"] == "ismail" and said["action"] == "restart"
    assert "ismail" in said["error"]


def test_an_action_without_a_service_is_refused(hub):
    server, _engine, _holder = hub
    with pytest.raises(urllib.error.HTTPError) as raised:
        call(server, "/api/v1/actions", {"action": "restart"})
    assert raised.value.code == 400


def test_an_unknown_action_is_refused_before_it_reaches_the_engine(hub):
    """`getattr(control, f"{action}_service")` with a name from the network is not
    something to find out about by AttributeError."""
    server, _engine, _holder = hub
    with pytest.raises(urllib.error.HTTPError) as raised:
        call(server, "/api/v1/actions",
             {"action": "format_disk", "service": "AppEngine"})
    assert raised.value.code == 400


def test_a_stack_run_is_accepted(hub, monkeypatch):
    server, engine, _holder = hub
    asked = []
    monkeypatch.setattr(engine, "run_stack",
                        lambda name, actor="": asked.append((name, actor)) or True)

    status, _said = call(server, "/api/v1/stacks/run",
                         {"name": "SAP stack", "actor": "ismail"})

    assert status == 202
    assert asked == [("SAP stack", "ismail")]


def test_refresh_is_accepted_and_says_nothing(hub, monkeypatch):
    server, engine, _holder = hub
    asked = []
    monkeypatch.setattr(engine, "refresh", lambda machine=None: asked.append(machine))

    status, said = call(server, "/api/v1/refresh", {"machine": "sd"})

    assert status == 204 and said is None
    assert asked == ["sd"]


# ---------------------------------------------------------------------------
# writing the config
# ---------------------------------------------------------------------------
def test_a_stale_config_save_is_refused_with_the_current_etag(hub):
    """Two clients editing at once: the second is told, rather than silently winning
    and losing whatever the first had added."""
    server, _engine, holder = hub
    payload = wire.config_payload(holder["cfg"])
    payload["etag"] = "0000000000000000"
    payload["actor"] = "ismail"

    with pytest.raises(urllib.error.HTTPError) as raised:
        call(server, "/api/v1/config", payload, method="PUT")

    assert raised.value.code == 409
    said = json.loads(raised.value.read().decode())
    assert said["etag"] == wire.etag(holder["cfg"])


def test_a_fresh_config_save_is_applied(hub, monkeypatch):
    server, engine, holder = hub
    saved = []
    monkeypatch.setattr(engine, "save_config",
                        lambda config: saved.append(config))
    payload = wire.config_payload(holder["cfg"])
    payload["config"]["services"].append({"name": "WMSServer"})
    payload["actor"] = "ismail"

    status, _said = call(server, "/api/v1/config", payload, method="PUT")

    assert status == 204
    assert [s.name for s in saved[0].services] == ["AppEngine",
                                                   "webclient.service",
                                                   "WMSServer"]


def test_a_config_the_client_was_just_given_saves_without_a_conflict(hub,
                                                                    monkeypatch):
    """The round trip a client actually makes. It failed before the etag was taken
    over the repaired config, and it is the failure nobody would have guessed."""
    server, engine, _holder = hub
    saved = []
    monkeypatch.setattr(engine, "save_config", lambda config: saved.append(config))

    _status, payload = call(server, "/api/v1/config")
    payload["actor"] = "ismail"
    status, _said = call(server, "/api/v1/config", payload, method="PUT")

    assert status == 204 and saved


# ---------------------------------------------------------------------------
# the event stream
# ---------------------------------------------------------------------------
def test_events_arrive_as_they_happen(hub):
    server, engine, _holder = hub
    lines = []
    ready = threading.Event()

    def listen():
        request = urllib.request.Request(
            server.url + "/api/v1/events",
            headers={"Authorization": "Bearer good"})
        with urllib.request.urlopen(request, timeout=20) as stream:
            ready.set()
            for raw in stream:
                text = raw.decode().strip()
                if text.startswith("data:"):
                    lines.append(json.loads(text[5:]))
                    if len(lines) >= 2:
                        return

    listener = threading.Thread(target=listen, daemon=True)
    listener.start()
    assert ready.wait(10), "the stream never opened"

    engine.store.update("AppEngine", st.RUNNING)
    engine.store.update("AppEngine", st.STOPPED)
    listener.join(15)

    assert [line["kind"] for line in lines] == ["status", "status"]
    assert lines[0]["protocol"] == wire.PROTOCOL
    assert [line["status"] for line in lines] == [st.RUNNING, st.STOPPED]
    # The client rebuilds st.Event from these, so what it needs has to be there.
    assert lines[1]["previous"] == st.RUNNING


def test_the_stream_needs_a_token_too(hub):
    server, _engine, _holder = hub
    request = urllib.request.Request(server.url + "/api/v1/events",
                                     headers={"Authorization": "Bearer wrong"})
    with pytest.raises(urllib.error.HTTPError) as raised:
        urllib.request.urlopen(request, timeout=10)
    assert raised.value.code == 401


def test_a_listener_that_goes_away_is_forgotten(hub):
    """A client closing its laptop must not leave the hub writing into a dead socket
    for ever, or holding a subscription that grows on every reconnect."""
    server, engine, _holder = hub

    for _ in range(3):
        request = urllib.request.Request(
            server.url + "/api/v1/events",
            headers={"Authorization": "Bearer good"})
        stream = urllib.request.urlopen(request, timeout=10)
        stream.read(1)                      # let the stream open
        stream.close()

    # Give the writers a chance to notice the closed sockets.
    engine.store.update("AppEngine", st.RUNNING)
    deadline = threading.Event()
    deadline.wait(1.0)
    assert server.listeners() == 0, f"{server.listeners()} left subscribed"


# ---------------------------------------------------------------------------
# the page
# ---------------------------------------------------------------------------
def test_the_root_page_is_served_and_needs_a_token_for_data(hub):
    """The page itself is not a secret; what it shows is. So the HTML is public and
    every fetch it makes is not — which is also the shape the real UI will need."""
    server, _engine, _holder = hub
    request = urllib.request.Request(server.url + "/")
    with urllib.request.urlopen(request, timeout=5) as answer:
        html = answer.read().decode()

    assert answer.status == 200
    assert "Service Officer" in html
    assert "/api/v1/snapshot" in html


def test_the_page_asks_not_to_be_cached(hub):
    """An upgraded hub serving last month's page from a browser cache would be a bug
    report about a feature that is already there."""
    server, _engine, _holder = hub
    with urllib.request.urlopen(server.url + "/", timeout=5) as answer:
        assert "text/html" in answer.headers["Content-Type"]
        assert "no-store" in (answer.headers.get("Cache-Control") or "")


def test_nothing_else_outside_the_api_is_served(hub):
    """One page, not a web server. A path that walks out of the folder is the first
    thing anybody tries."""
    server, _engine, _holder = hub
    for path in ("/../core/hub_auth.py", "/index.html.bak", "/favicon.png"):
        with pytest.raises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(server.url + path, timeout=5)
        assert raised.value.code == 404, path


def test_a_browser_may_carry_its_token_on_the_stream_url(hub):
    """EventSource cannot set a header, so the one endpoint a browser must open without
    fetch() takes the token as a parameter. Only that endpoint: everywhere else a
    parameter would be a token in a browser history and a proxy log."""
    server, _engine, _holder = hub

    request = urllib.request.Request(server.url + "/api/v1/events?token=good")
    with urllib.request.urlopen(request, timeout=5) as answer:
        assert answer.status == 200
        assert answer.headers["Content-Type"] == "text/event-stream"
        assert answer.readline().startswith(b": open")

    with pytest.raises(urllib.error.HTTPError) as raised:
        urllib.request.urlopen(server.url + "/api/v1/snapshot?token=good", timeout=5)
    assert raised.value.code == 401


def test_a_token_on_a_url_never_reaches_the_log():
    """The log is what people paste into tickets."""
    assert hub_server.redacted("GET /api/v1/events?token=s3cret HTTP/1.1") == \
        "GET /api/v1/events?token=[redacted] HTTP/1.1"
    assert hub_server.redacted("GET /api/v1/snapshot HTTP/1.1") == \
        "GET /api/v1/snapshot HTTP/1.1"


# ---------------------------------------------------------------------------
# which addresses it answers on
# ---------------------------------------------------------------------------
def test_it_answers_on_both_address_families(monkeypatch):
    """Measured 2026-07-28 against the installed hub: connecting to `CTL052:8797` took
    **2073 ms** while `10.77.3.50:8797` took 0 ms. The name resolved to a link-local IPv6
    address first — this machine has five — and the hub was listening on 0.0.0.0, so every
    connection waited for that attempt to give up. Two connections happen before a client
    draws its first frame, so it took four seconds to show anything.

    Nothing was misconfigured. One socket, both families, 2 ms.
    """
    import socket

    from core import engine as engine_mod
    from core import state as st

    monkeypatch.setattr(hub_server.hub_auth, "check", lambda token: "tests")
    monkeypatch.setattr(hub_server.hub_auth, "note_seen", lambda name, host="": None)
    engine = engine_mod.Engine(lambda: cfg_mod.Config(), store=st.Store())
    # host="" is what hub.py passes when the config's `bind` is blank: every address.
    server = hub_server.HubServer(engine, host="", port=0, insecure=True)
    server.start()
    try:
        assert server._server.address_family == socket.AF_INET6
        for family, address in ((socket.AF_INET, ("127.0.0.1", server.port)),
                                (socket.AF_INET6, ("::1", server.port))):
            with socket.socket(family, socket.SOCK_STREAM) as probe:
                probe.settimeout(5)
                probe.connect(address)          # raises if that family is refused
    finally:
        server.stop()


def test_a_named_address_is_taken_literally(monkeypatch):
    """An administrator who put one address in `bind` meant that one — keeping the hub off
    a second network card is the reason the field exists."""
    import socket

    from core import engine as engine_mod
    from core import state as st

    engine = engine_mod.Engine(lambda: cfg_mod.Config(), store=st.Store())
    server = hub_server.HubServer(engine, host="127.0.0.1", port=0, insecure=True)
    server.start()
    try:
        assert server._server.address_family == socket.AF_INET
    finally:
        server.stop()
