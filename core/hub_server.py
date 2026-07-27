"""The hub's HTTPS API: one port, JSON, and an event stream.

The standard library's `http.server`, not a framework. Five clients on a LAN do not
need an async stack, and every dependency is something to license-check, ship inside a
PyInstaller bundle and patch on somebody's server. `ThreadingHTTPServer` gives a thread
per request, which is what an event stream needs anyway.

The shape:

    GET  /api/v1/ping        no token — "is the hub there", for the Connection page
    GET  /api/v1/snapshot    everything needed to draw a first frame
    GET  /api/v1/events      text/event-stream, one `data:` line per wire event
    POST /api/v1/actions     {action, service, machine, actor} -> 202 {id}
    POST /api/v1/stacks/run  {name, actor} -> 202
    POST /api/v1/triggers/run{name, actor} -> 202
    POST /api/v1/refresh     {machine} or {} -> 204
    GET  /api/v1/config      the config and its etag
    PUT  /api/v1/config      the config, its etag, and an actor -> 204 or 409
    GET  /api/v1/history     rows, filtered by the usual query parameters
    GET  /api/v1/machines/<name>/services   what that machine has installed

Three rules learned elsewhere and applied here:

* **Every mutating request is logged with who asked.** With one operator the history was
  enough; with five, a line in the log is what answers "who restarted this at 03:00" when
  history is switched off.
* **A refusal says what to do.** 404 and 405 are different answers, a stale etag comes
  back *with* the current one, and Busy comes back with the name of whoever holds the
  service. A person meets these with curl.
* **Nothing here waits on a machine.** The engine's calls return immediately; the work
  happens on its threads. A handler that blocked would hold a request thread and, for a
  remote Windows machine, hold it for twenty seconds.
"""

from __future__ import annotations

import json
import socket
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import applog, hub_auth, history, version, wire
from . import engine as engine_mod

log = applog.get("hub")

#: Sent every 20 s down an idle event stream. A proxy or a Windows firewall will close a
#: connection that says nothing, and a client that reconnects every idle minute is a
#: client that misses events during every reconnect.
KEEPALIVE_SECONDS = 20
#: What a client may ask for in one history page. Bounded because the query is theirs
#: and the memory is ours.
HISTORY_LIMIT = 2000
ACTIONS = ("start", "stop", "restart", "kill")


class _Listener:
    """One open event stream. Its queue is bounded: a client that has stopped reading
    must not grow the hub's memory until something dies — better to drop events and let
    it notice the gap on its next snapshot."""

    LIMIT = 500

    def __init__(self):
        self.events: list = []
        self.wake = threading.Event()
        self.lock = threading.Lock()
        self.dropped = 0

    def put(self, payload: dict) -> None:
        with self.lock:
            if len(self.events) >= self.LIMIT:
                self.dropped += 1
                return
            self.events.append(payload)
        self.wake.set()

    def take(self) -> list:
        with self.lock:
            found, self.events = self.events, []
            self.wake.clear()
        return found


class HubServer:
    """The API in front of one engine."""

    def __init__(self, engine, host: str = "0.0.0.0", port: int = 8797,
                 certfile: str = None, insecure: bool = False):
        self.engine = engine
        self.host = host
        self.port = port
        self.certfile = certfile
        self.insecure = bool(insecure)
        self._listeners: set = set()
        self._listeners_lock = threading.RLock()
        self._server = None
        self._thread = None

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        if self._server is not None:
            return
        if self.insecure:
            log.warning("the hub is serving plain HTTP — for tests only, never a "
                        "network anybody else is on")
        elif not self.certfile:
            raise RuntimeError("the hub needs a certificate; see hub_auth."
                               "ensure_certificate")

        handler = _make_handler(self)
        self._server = ThreadingHTTPServer((self.host, self.port), handler)
        self._server.daemon_threads = True
        if not self.insecure:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(self.certfile)
            self._server.socket = context.wrap_socket(self._server.socket,
                                                      server_side=True)
        self.port = self._server.server_address[1]
        self.engine.store.subscribe(self._on_state)
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        daemon=True, name="hub-http")
        self._thread.start()
        log.info("hub listening on %s", self.url)

    def stop(self) -> None:
        try:
            self.engine.store.unsubscribe(self._on_state)
        except Exception:
            pass
        with self._listeners_lock:
            for listener in list(self._listeners):
                listener.wake.set()             # let the writers notice and leave
            self._listeners.clear()
        server, self._server = self._server, None
        if server is not None:
            server.shutdown()
            # Closed, not merely stopped: the port has to be free for the next start,
            # which is what an upgrade and the reconnect test both do.
            server.server_close()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=5)
        log.info("hub stopped")

    @property
    def url(self) -> str:
        scheme = "http" if self.insecure else "https"
        host = "127.0.0.1" if self.host in ("", "0.0.0.0") else self.host
        return f"{scheme}://{host}:{self.port}"

    def listeners(self) -> int:
        """How many event streams are open. For the tests, and for a log line when
        somebody wonders whether a client is really connected."""
        with self._listeners_lock:
            return len(self._listeners)

    # -- events ------------------------------------------------------------
    def _on_state(self, state_event) -> None:
        """A status changed: fan it out to every open stream. Called on whatever
        thread the change happened on, which is why the queues are locked."""
        self.publish(wire.event_from_state(state_event))

    def publish(self, payload: dict) -> None:
        with self._listeners_lock:
            listeners = list(self._listeners)
        for listener in listeners:
            listener.put(payload)

    def _add_listener(self) -> _Listener:
        listener = _Listener()
        with self._listeners_lock:
            self._listeners.add(listener)
        return listener

    def _drop_listener(self, listener) -> None:
        with self._listeners_lock:
            self._listeners.discard(listener)


def _make_handler(hub: HubServer):
    """The request handler, closed over its server rather than reaching for a global —
    so two hubs in one process (which the tests do) cannot answer for each other."""

    class Handler(BaseHTTPRequestHandler):
        # HTTP/1.1 because an event stream needs a connection that stays open.
        protocol_version = "HTTP/1.1"
        server_version = f"ServiceOfficerHub/{version.short()}"

        # -- plumbing --------------------------------------------------
        def log_message(self, fmt, *args):
            # Every request in the app's own log rather than on stderr, at debug: the
            # interesting ones are logged explicitly with their actor.
            log.debug("%s %s", self.address_string(), fmt % args)

        def _client(self) -> str:
            header = self.headers.get("Authorization", "")
            token = header[7:].strip() if header.lower().startswith("bearer ") else ""
            return hub_auth.check(token)

        def _send(self, status: int, payload=None) -> None:
            body = b"" if payload is None else json.dumps(payload).encode("utf-8")
            self.send_response(status)
            if body:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _refuse(self, status: int, text: str, **facts) -> None:
            self._send(status, {"error": text, **facts})

        def _body(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            if not raw:
                return {}
            return json.loads(raw.decode("utf-8"))

        # -- routing ---------------------------------------------------
        def do_GET(self):
            path, _, query = self.path.partition("?")
            if path == "/api/v1/ping":
                self._send(200, {"protocol": wire.PROTOCOL,
                                 "version": version.short(),
                                 "name": socket.gethostname()})
                return
            who = self._client()
            if not who:
                self._refuse(401, "a valid token is needed")
                return
            hub_auth.note_seen(who)
            if path == "/api/v1/snapshot":
                self._send(200, hub.engine.snapshot())
            elif path == "/api/v1/config":
                self._send(200, wire.config_payload(hub.engine.config()))
            elif path == "/api/v1/history":
                self._history(query)
            elif path == "/api/v1/events":
                self._events(who)
            elif path.startswith("/api/v1/machines/") \
                    and path.endswith("/services"):
                self._machine_services(path.split("/")[4])
            elif path in ("/api/v1/actions", "/api/v1/refresh",
                          "/api/v1/stacks/run", "/api/v1/triggers/run"):
                self._refuse(405, "that path takes a POST")
            else:
                self._refuse(404, f"no such path: {path}")

        def do_POST(self):
            who = self._client()
            if not who:
                self._refuse(401, "a valid token is needed")
                return
            hub_auth.note_seen(who)
            try:
                body = self._body()
            except ValueError as exc:
                self._refuse(400, f"the body is not JSON: {exc}")
                return
            path = self.path.partition("?")[0]
            if path == "/api/v1/actions":
                self._action(body, who)
            elif path == "/api/v1/stacks/run":
                self._run("stack", body, who)
            elif path == "/api/v1/triggers/run":
                self._run("trigger", body, who)
            elif path == "/api/v1/refresh":
                hub.engine.refresh(body.get("machine") or None)
                self._send(204)
            elif path in ("/api/v1/snapshot", "/api/v1/config", "/api/v1/events"):
                self._refuse(405, "that path takes a GET")
            else:
                self._refuse(404, f"no such path: {path}")

        def do_PUT(self):
            who = self._client()
            if not who:
                self._refuse(401, "a valid token is needed")
                return
            hub_auth.note_seen(who)
            try:
                body = self._body()
            except ValueError as exc:
                self._refuse(400, f"the body is not JSON: {exc}")
                return
            if self.path.partition("?")[0] != "/api/v1/config":
                self._refuse(404, f"no such path: {self.path}")
                return
            self._save_config(body, who)

        # -- handlers --------------------------------------------------
        def _action(self, body: dict, who: str) -> None:
            action = str(body.get("action") or "")
            service = str(body.get("service") or "")
            machine = str(body.get("machine") or "")
            actor = str(body.get("actor") or who)
            if action not in ACTIONS:
                # Refused here rather than in the engine: `action` reaches a getattr
                # on the control module, and a name off the network is not something
                # to discover by AttributeError.
                self._refuse(400, f"unknown action: {action!r}")
                return
            if not service:
                self._refuse(400, "which service?")
                return
            try:
                action_id = hub.engine.act(action, service, machine, actor=actor)
            except engine_mod.Busy as clash:
                self._refuse(409, str(clash), actor=clash.actor,
                             action=clash.action, since=clash.since)
                return
            log.info("%s asked for %s on %s%s", actor, action, service,
                     f" ({machine})" if machine else "")
            self._send(202, {"id": action_id})

        def _run(self, kind: str, body: dict, who: str) -> None:
            name = str(body.get("name") or "")
            actor = str(body.get("actor") or who)
            if not name:
                self._refuse(400, f"which {kind}?")
                return
            if kind == "stack":
                started = hub.engine.run_stack(name, actor=actor)
            else:
                trigger = hub.engine.config().trigger(name) \
                    if hasattr(hub.engine.config(), "trigger") else None
                started = bool(trigger)
                if started:
                    hub.engine._call(hub.engine._on_trigger, trigger=trigger)
            if not started:
                self._refuse(409, f"the {kind} could not be started — it may be "
                                  f"running already, empty, or gone")
                return
            log.info("%s ran the %s %s", actor, kind, name)
            self._send(202, {"started": name})

        def _save_config(self, body: dict, who: str) -> None:
            actor = str(body.get("actor") or who)
            try:
                new_cfg, sent = wire.config_from_payload(body)
            except Exception as exc:
                self._refuse(400, f"the config could not be read: {exc}")
                return
            current = wire.etag(hub.engine.config())
            if sent != current:
                # With the current etag, so the client can fetch, merge and retry
                # rather than guess what it missed.
                self._refuse(409, "somebody else has saved since you loaded this",
                             etag=current)
                return
            try:
                hub.engine.save_config(new_cfg)
            except Exception as exc:
                log.exception("saving the config failed")
                self._refuse(500, f"the config could not be saved: {exc}")
                return
            log.info("%s saved the config", actor)
            self._send(204)

        def _history(self, query: str) -> None:
            from urllib.parse import parse_qs
            asked = parse_qs(query)

            def one(name, default=None):
                found = asked.get(name, [])
                return found[0] if found else default

            cfg = hub.engine.config()
            try:
                limit = min(int(one("limit", 200)), HISTORY_LIMIT)
            except ValueError:
                limit = 200
            rows = history.query(
                service_names=[s.name for s in cfg.services],
                labels=[s.display() for s in cfg.services],
                local_services=[s.name for s in cfg.services if not s.machine],
                service=one("service"),
                hours=int(one("hours")) if (one("hours") or "").isdigit() else None,
                include_windows=one("windows") == "1",
                full=one("full") == "1",
                limit=limit)
            self._send(200, {"rows": rows})

        def _machine_services(self, name: str) -> None:
            """What that machine has installed — the picker's list.

            This one *does* wait on a machine: enumerating is the request. It is a
            request thread rather than the UI's, which is the whole point of the split,
            and the client shows "Reading the services on …" while it waits.
            """
            from . import control
            cfg = hub.engine.config()
            record = cfg.machine(name)
            try:
                found = control.list_all_services(name, record)
            except Exception as exc:
                self._refuse(502, f"{name} could not be asked: {exc}")
                return
            self._send(200, {"services": found})

        def _gone(self) -> bool:
            """Has the client closed the connection?

            Asked rather than waited for. A write to a closed socket does not reliably
            fail on the first attempt — Windows accepts one into the buffer and only
            raises on the next — so relying on the write meant a client that closed
            quietly stayed subscribed until the next keepalive twenty seconds later.
            Measured: three closed streams still counted as open.

            A closed socket becomes readable and reads empty, which is the signal. A
            client that sent real bytes (it should not) is treated as still there.
            """
            import select
            try:
                readable, _w, _x = select.select([self.connection], [], [], 0)
                if not readable:
                    return False
                return self.connection.recv(1, socket.MSG_PEEK) == b""
            except (OSError, ValueError):
                return True
            except Exception:
                return False        # a TLS socket with nothing to give yet

        def _events(self, who: str) -> None:
            """An SSE stream: one `data:` line per event, a comment as a keepalive.

            Held open until the client goes away, which is checked for on every turn of
            the loop rather than discovered by a failing write — see _gone. Otherwise a
            laptop lid closing leaves the hub fanning events into a dead socket.
            """
            listener = hub._add_listener()
            log.info("%s opened an event stream (%d open)", who, hub.listeners())
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                # One comment straight away, so a client knows the stream is open
                # rather than waiting for the first thing to happen.
                self.wfile.write(b": open\n\n")
                self.wfile.flush()
                last = time.monotonic()
                while hub._server is not None:
                    if self._gone():
                        break
                    if listener.wake.wait(0.25):
                        for payload in listener.take():
                            self.wfile.write(
                                b"data: " + json.dumps(payload).encode("utf-8")
                                + b"\n\n")
                        self.wfile.flush()
                        last = time.monotonic()
                    elif time.monotonic() - last >= KEEPALIVE_SECONDS:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                        last = time.monotonic()
            except (BrokenPipeError, ConnectionResetError, OSError, ValueError):
                pass                    # the client went away; that is not an error
            finally:
                hub._drop_listener(listener)
                if listener.dropped:
                    log.info("%s missed %d event(s) — it was not reading",
                             who, listener.dropped)

    return Handler
