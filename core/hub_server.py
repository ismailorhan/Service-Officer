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
    GET    /api/v1/clients   who is paired: name, when issued, when last used
    POST   /api/v1/clients   {name, description} -> 201 {token, url, ...} — once
    DELETE /api/v1/clients/<name>   revoke, effective immediately
    GET  /                   one page, for a browser (core/hub_pages/index.html)

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
import os
import socket
import ssl
import sys
import threading
import time
import urllib.parse
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


def _pages_dir() -> str:
    """Where index.html is. Frozen, PyInstaller unpacks data next to `_MEIPASS`; from
    source it is beside this file."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, "core", "hub_pages")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "hub_pages")


def redacted(line: str) -> str:
    """A request line with any `token=...` value taken out, and the rest of the line
    left intact — "GET /api/v1/events?token=[redacted] HTTP/1.1" is still useful, and
    the token is a credential that must not reach a log people paste into tickets."""
    head, marker, rest = line.partition("token=")
    if not marker:
        return line
    _value, space, tail = rest.partition(" ")
    return f"{head}token=[redacted]{space}{tail}"


def page(name: str = "index.html") -> bytes:
    """The page, read fresh. Not cached: it is asked for once per browser tab, and a
    cached copy is one more thing to be wrong after an upgrade."""
    try:
        with open(os.path.join(_pages_dir(), name), "rb") as fh:
            return fh.read()
    except OSError as exc:
        log.warning("cannot read the hub page: %s", exc)
        return b""


def _can_bind(host: str, port: int) -> tuple:
    """(True, "") if that port is free, else (False, why) — asked before anything moves.

    A probe rather than an attempt, because the attempt is what cannot be undone: the old
    socket has to be closed before the new one can be opened on the same address family, and
    a failure after that is a hub nobody can reach.
    """
    import socket as socket_mod

    family = socket_mod.AF_INET6 if ":" in (host or "") or host in ("", "::")         else socket_mod.AF_INET
    probe = socket_mod.socket(family, socket_mod.SOCK_STREAM)
    try:
        if family == socket_mod.AF_INET6:
            try:
                probe.setsockopt(socket_mod.IPPROTO_IPV6, socket_mod.IPV6_V6ONLY, 0)
            except OSError:
                pass
        probe.bind((host or "::", int(port)))
        probe.listen(1)
        return True, ""
    except OSError as exc:
        return False, f"could not listen on {port}: {exc}"
    finally:
        probe.close()


def _open_firewall(port: int, was: int = 0) -> None:
    """Move the inbound rule to the new port.

    A port nothing can reach is the same as a hub that did not come back, and the installer's
    rule names the old one. Best effort and never fatal: a machine with the firewall off, or
    managed by policy, is somebody else's arrangement — the log says what was attempted.

    The same name and profile the installer uses, deleted before adding, because two rules
    for one thing was a real bug here once.
    """
    import subprocess

    name = "Service Officer Hub"
    for arguments in (
            ["advfirewall", "firewall", "delete", "rule", f"name={name}"],
            ["advfirewall", "firewall", "add", "rule", f"name={name}", "dir=in",
             "action=allow", "protocol=TCP", f"localport={int(port)}", "profile=domain"]):
        try:
            done = subprocess.run(["netsh", *arguments], capture_output=True, text=True,
                                  timeout=20,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if done.returncode != 0 and arguments[2] == "add":
                log.warning("could not open port %s in the firewall: %s", port,
                            (done.stdout or done.stderr).strip()[:200])
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("could not reach netsh to open port %s: %s", port, exc)
            return
    log.info("firewall: port %s open%s", port, f", {was} closed" if was else "")


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
            dropping = len(self.events) >= self.LIMIT
            if dropping:
                self.dropped += 1
            else:
                self.events.append(payload)
        # Woken either way. A client far enough behind to fill this queue keeps every
        # further event dropping, so returning quietly here left nothing to wake the writer
        # and the gap marker was never sent at all — the client stayed silently wrong, which
        # is precisely what the marker exists to prevent.
        self.wake.set()

    def take(self) -> list:
        """Everything waiting, and a marker if anything was lost making room for it.

        The marker goes *last*: the events still queued are true, and a client should apply
        them before being told that what it holds is unreliable. Cleared here, so one gap
        produces one marker however many events it swallowed — a client reading a fresh
        snapshot for each of five hundred would be worse than the gap itself.
        """
        with self.lock:
            found, self.events = self.events, []
            missed, self.dropped = self.dropped, 0
            self.wake.clear()
        if missed:
            found.append(wire.gap_event(missed))
        return found


class _DualStack(ThreadingHTTPServer):
    """One socket answering on IPv6 *and* IPv4.

    Measured 2026-07-28 against the installed hub: `https://CTL052:8797` took **2073 ms
    to connect** while `https://10.77.3.50:8797` took 0 ms. The name resolves to a
    link-local IPv6 address first — this machine has eight addresses, and five of them are
    IPv6 — so every connection waited for that attempt to give up before falling back to
    IPv4. Two connections are made before the first frame is drawn, so a client took four
    seconds to show anything.

    Nothing was misconfigured; the hub simply listened on 0.0.0.0. Python's own
    `http.server` does exactly this for the same reason.
    """

    address_family = socket.AF_INET6

    def server_bind(self):
        # Off, not on: with V6ONLY set, an IPv4 client of a v6 socket is refused.
        try:
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        except OSError:
            pass          # a host with IPv6 disabled: the IPv4 path is all there is
        return super().server_bind()


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
        # Every address means both families — see _DualStack. A specific address is taken
        # literally: an administrator who named one meant that one.
        any_address = self.host in ("", "0.0.0.0", "::")
        if any_address:
            try:
                self._server = _DualStack(("::", self.port), handler)
            except OSError as exc:
                log.warning("no IPv6 on this host (%s); listening on IPv4 only", exc)
                self._server = ThreadingHTTPServer(("0.0.0.0", self.port), handler)
        else:
            self._server = ThreadingHTTPServer((self.host, self.port), handler)
        self._server.daemon_threads = True
        if not self.insecure:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(self.certfile)
            self._server.socket = context.wrap_socket(self._server.socket,
                                                      server_side=True)
        self.port = self._server.server_address[1]
        self.engine.store.subscribe(self._on_state)
        # Reachability is not a store subscription: the store publishes service state, and
        # a machine going quiet is not a service changing. Without this the chip on every
        # remote machine was frozen at whatever the client's first snapshot said.
        self.engine.also_on("machine", self._on_machine)
        # Health is not a status change — the service manager still says Running — but it is
        # what st.effective() turns into the chip's colour. Unpublished, a connected panel
        # showed green for a service whose checks had been failing for hours.
        self.engine.also_on("health", self._on_health)
        # And when one finishes. The hub answers 202 to an action — accepted, not done — so
        # without this a client never learned the outcome: a busy label that never cleared,
        # and a refusal nobody saw.
        self.engine.also_on("action_done", self._on_action_done)
        # And when the landscape is edited, by whoever. Without this two panels on one hub
        # disagreed about what services exist until one of them was restarted.
        self.engine.also_on("config_saved", self._on_config_saved)
        # And the four nobody was listening to at all. A stack run by the hub's own
        # scheduler was invisible to every panel, and an engine error was reported nowhere:
        # a Windows service has no tray to put a notification in.
        self.engine.also_on("stack_step", self._on_stack_step)
        self.engine.also_on("stack_done", self._on_stack_done)
        self.engine.also_on("trigger", self._on_trigger)
        self.engine.also_on("error", self._on_error)
        # Somebody disabling a service in services.msc. Nothing pushes it, so the engine
        # re-reads it on a timer — and without this a client heard about it only when it
        # happened to take a fresh snapshot.
        self.engine.also_on("start_type", self._on_start_type)
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

    def public_url(self) -> str:
        """The address a *client* should use, which is not `url`.

        `url` says 127.0.0.1 when the hub listens on every address, because that is what is
        true for whoever is asking locally. A client on another computer needs this
        computer's name — and the certificate is issued for that name, so a client pinning
        anything else would fail its own check.
        """
        return f"https://{socket.gethostname()}:{self.port}"

    def fingerprint(self) -> str:
        """What a client should expect to see. Empty when serving plain HTTP, which only
        the tests do."""
        if not self.certfile:
            return ""
        try:
            return hub_auth.fingerprint_of(self.certfile)
        except Exception as exc:
            log.warning("could not read the certificate's fingerprint: %s", exc)
            return ""

    def listeners(self) -> int:
        """How many event streams are open. For the tests, and for a log line when
        somebody wonders whether a client is really connected."""
        with self._listeners_lock:
            return len(self._listeners)

    def rebind(self, port: int) -> tuple:
        """Listen on `port` instead. Returns (ok, what to say).

        The socket is closed and another opened — the service is not restarted. Stopping a
        LocalSystem service from inside itself leaves nothing to start it again, and the panel
        that would otherwise have to do it has no administrator rights by design. Nothing else
        about the hub is disturbed: the engine, the poller and the watchdog never stop.

        If the new port cannot be bound the old one is taken again, so a mistyped number
        costs a message rather than a hub nobody can reach.
        """
        port = int(port)
        if not 1 <= port <= 65535:
            return False, f"a port has to be between 1 and 65535, not {port}"
        if port == self.port:
            return True, f"already listening on {port}"
        was = self.port
        free, why = _can_bind(self.host, port)
        if not free:
            # Refused before anything is announced. Announcing first and failing afterwards
            # left every client retrying an address nothing was on, with the correction
            # unable to reach them because they had already left — the hub was up and
            # unreachable, which is worse than a refusal.
            log.info("not moving to %s: %s", port, why)
            return False, why

        # Now it is safe to say so, while the old socket is still open to carry it.
        self.publish(wire.hub_port_event(port))
        time.sleep(0.4)          # long enough for a queued event to be written out

        self.stop()
        self.port = port
        try:
            self.start()
        except OSError as exc:
            log.error("could not listen on %s (%s); taking %s again", port, exc, was)
            self.port = was
            try:
                self.start()
            except OSError as fatal:
                # Both gone. Said as loudly as a log can, because there is now nothing
                # listening and the only way back is the command line and a restart.
                log.critical("could not listen on %s either (%s) — this hub is not "
                             "answering anything", was, fatal)
                return False, (f"could not listen on {port} ({exc}), and {was} could not be "
                               f"taken back either — restart the Hub service")
            self.publish(wire.hub_port_event(was))
            return False, f"could not listen on {port}: {exc}"
        _open_firewall(port, was)
        log.info("now listening on %s (was %s)", port, was)
        return True, f"listening on {port}"

    # -- events ------------------------------------------------------------
    def _on_state(self, state_event) -> None:
        """A status changed: fan it out to every open stream. Called on whatever
        thread the change happened on, which is why the queues are locked."""
        self.publish(wire.event_from_state(state_event))

    def _on_start_type(self, service="", machine="", start_type="", disabled=False,
                       **_rest) -> None:
        self.publish(wire.start_type_event(service, machine, start_type, disabled))

    def _on_stack_step(self, index=0, total=0, service="", action="", phase="",
                       **_rest) -> None:
        self.publish(wire.stack_step_event(index, total, service, action, phase))

    def _on_stack_done(self, result=None, **_rest) -> None:
        self.publish(wire.stack_done_event(result))

    def _on_trigger(self, trigger=None, outcome="", detail="", **_rest) -> None:
        self.publish(wire.trigger_event(trigger, outcome, detail))

    def _on_error(self, kind="", text="", **_rest) -> None:
        self.publish(wire.error_event(kind, text))

    def _on_health(self, service="", machine="", verdict="", detail="", **_rest) -> None:
        """A verdict changed: tell every open stream."""
        self.publish(wire.health_event(service, machine, verdict, detail))

    def _on_machine(self, machine="", reachable=False, detail="") -> None:
        """A machine started or stopped answering: tell every open stream."""
        self.publish(wire.machine_event(machine, reachable, detail))

    def _on_config_saved(self, config=None, actor="", **_rest) -> None:
        """The landscape changed: tell every open stream so they come and read it."""
        self.publish(wire.config_event(actor, wire.etag(config)
                                       if config is not None else ""))
        # And say so if somebody has changed the port this thing listens on. It is read once,
        # when the socket is bound, so a new value in the config is a silent no-op until the
        # service is restarted — the config and the reality then disagree, and the only clue
        # is that clients pointed at the new number cannot connect.
        wanted = getattr(getattr(config, "hub", None), "port", 0)
        if wanted and wanted != self.port:
            log.warning("the configured port is now %s but this hub is listening on %s — "
                        "restart %s for the change to take effect", wanted, self.port,
                        "the Service Officer Hub service")

    def _on_action_done(self, service="", machine="", action="", error="", status="",
                        actor="", **_rest) -> None:
        """An action finished. `**_rest` swallows `bulk` and anything added later: a
        listener that raises on an unexpected fact would take out the engine's callback."""
        self.publish(wire.action_event(service, machine, action, error, status, actor))

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
            #
            # The event stream carries its token on the query string (EventSource
            # cannot send a header), so the line is redacted before it is written:
            # a token in a log file is a credential in a log file, and the log is the
            # thing people paste into tickets.
            log.debug("%s %s", self.address_string(), redacted(fmt % args))

        def _host(self) -> str:
            """Which machine the request came from, as it says itself. A label, not a
            credential: the token is what authenticates. Bounded and stripped of anything
            that is not a host name, because it is read off the network and ends up in a
            store and on a screen."""
            said = (self.headers.get("X-Client-Host") or "").strip()[:64]
            return "".join(c for c in said if c.isalnum() or c in "-._")

        def _client(self, query: str = "") -> str:
            header = self.headers.get("Authorization", "")
            token = header[7:].strip() if header.lower().startswith("bearer ") else ""
            if not token and query:
                # Only the event stream passes `query`. A browser's EventSource has no
                # way to set a header, so the one endpoint a browser must reach without
                # JavaScript's fetch() accepts it here — over TLS, and redacted out of
                # the log above.
                token = urllib.parse.parse_qs(query).get("token", [""])[0].strip()
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
            if path in ("/", "/index.html"):
                self._page()
                return
            if not path.startswith("/api/v1/"):
                # One page and one API, not a web server. Answered before the token
                # check on purpose: "no such path" tells a stranger nothing, and a 401
                # here would imply there is something behind it.
                self._refuse(404, f"no such path: {path}")
                return
            if path == "/api/v1/ping":
                self._send(200, {"protocol": wire.PROTOCOL,
                                 "version": version.short(),
                                 "name": socket.gethostname()})
                return
            who = self._client(query if path == "/api/v1/events" else "")
            if not who:
                self._refuse(401, "a valid token is needed")
                return
            hub_auth.note_seen(who, self._host())
            if path == "/api/v1/snapshot":
                self._send(200, hub.engine.snapshot())
            elif path == "/api/v1/config":
                self._send(200, wire.config_payload(hub.engine.config()))
            elif path == "/api/v1/clients":
                self._send(200, {"clients": hub_auth.clients(),
                                 "url": hub.public_url(),
                                 "fingerprint": hub.fingerprint()})
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
            hub_auth.note_seen(who, self._host())
            try:
                body = self._body()
            except ValueError as exc:
                self._refuse(400, f"the body is not JSON: {exc}")
                return
            path = self.path.partition("?")[0]
            if path == "/api/v1/hub/port":
                self._hub_port(body, who)
            elif path == "/api/v1/actions":
                self._action(body, who)
            elif path == "/api/v1/stacks/run":
                self._run("stack", body, who)
            elif path == "/api/v1/triggers/run":
                self._run("trigger", body, who)
            elif path == "/api/v1/refresh":
                hub.engine.refresh(body.get("machine") or None)
                self._send(204)
            elif path == "/api/v1/clients":
                self._issue_token(body, who)
            elif path in ("/api/v1/snapshot", "/api/v1/config", "/api/v1/events"):
                self._refuse(405, "that path takes a GET")
            else:
                self._refuse(404, f"no such path: {path}")

        def do_PUT(self):
            who = self._client()
            if not who:
                self._refuse(401, "a valid token is needed")
                return
            hub_auth.note_seen(who, self._host())
            try:
                body = self._body()
            except ValueError as exc:
                self._refuse(400, f"the body is not JSON: {exc}")
                return
            if self.path.partition("?")[0] != "/api/v1/config":
                self._refuse(404, f"no such path: {self.path}")
                return
            self._save_config(body, who)

        def do_DELETE(self):
            who = self._client()
            if not who:
                self._refuse(401, "a valid token is needed")
                return
            hub_auth.note_seen(who, self._host())
            path = self.path.partition("?")[0]
            if not path.startswith("/api/v1/clients/"):
                self._refuse(404, f"no such path: {path}")
                return
            name = urllib.parse.unquote(path[len("/api/v1/clients/"):])
            if not name:
                self._refuse(400, "which client?")
                return
            gone = hub_auth.revoke(name)
            log.info("%s revoked %s%s", who, name, "" if gone else " (not paired)")
            if gone:
                self._send(204)
            else:
                self._refuse(404, f"no client called {name!r}")

        # -- handlers --------------------------------------------------
        def _issue_token(self, body: dict, who: str) -> None:
            """A new token, returned once and never again.

            Adding a name that already exists replaces its token, which is how a lost one
            is dealt with — so it is not an error, but it is worth a line in the log
            saying whose access just changed.
            """
            name = str(body.get("name") or "").strip()
            description = str(body.get("description") or "").strip()[:200]
            if not name:
                self._refuse(400, "a client needs a name")
                return
            if len(name) > 64:
                self._refuse(400, "that name is too long to be useful (64 characters)")
                return
            existed = any(c["name"] == name for c in hub_auth.clients())
            token = hub_auth.add_client(name, description)
            if not token:
                self._refuse(500, "the client list could not be written")
                return
            log.info("%s %s a token for %s", who,
                     "replaced" if existed else "issued", name)
            url = hub.public_url()
            self._send(201, {
                "name": name,
                "token": token,
                "url": url,
                "fingerprint": hub.fingerprint(),
                "replaced": existed,
                # The whole thing to run on that machine, so nobody has to assemble it
                # from three fields.
                "command": f'ServiceOfficer.exe --connect {url} --token {token}',
            })

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
                # Through the engine, which is the half that acts. This used to poke
                # `_on_trigger` — None on a hub — and then answer that it had started.
                started = hub.engine.run_trigger(name, actor=actor)
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

        def _hub_port(self, body: dict, who: str) -> None:
            """Change the port this hub listens on, and move to it.

            Answered *before* the move, or the answer would go down with the socket it is
            being written to. The rebinding happens on its own thread a moment later, by
            which time this reply and the hub_port event are both out.
            """
            try:
                wanted = int(body.get("port") or 0)
            except (TypeError, ValueError):
                self._refuse(400, f"not a port number: {body.get('port')!r}")
                return
            if not 1 <= wanted <= 65535:
                self._refuse(400, f"a port has to be between 1 and 65535, not {wanted}")
                return
            cfg = hub.engine.config()
            if wanted != cfg.hub.port:
                cfg.hub.port = wanted
                try:
                    hub.engine.save_config(cfg)
                except Exception as exc:
                    self._refuse(500, f"could not store the port: {exc}")
                    return
            log.info("%s asked this hub to listen on %s", who, wanted)
            self._send(202, {"port": wanted, "was": hub.port})
            if wanted != hub.port:
                threading.Thread(target=lambda: hub.rebind(wanted), daemon=True,
                                 name="hub-rebind").start()

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
            hours = int(one("hours")) if (one("hours") or "").isdigit() else None
            windows = one("windows") == "1"
            # This one waits on a machine, deliberately — the exception to the rule at the
            # top of this file, and the right place for it: the hub holds the credentials
            # and the WinRM trust for those machines, and a client holds neither. Its own
            # window stays responsive because it asks on a worker thread.
            remote = (history.remote_events_for(cfg, one("service") or "", hours)
                      if windows else {})
            rows = history.query(
                service_names=[s.name for s in cfg.services],
                labels=[s.display() for s in cfg.services],
                local_services=[s.name for s in cfg.services if not s.machine],
                service=one("service"),
                hours=hours,
                include_windows=windows,
                remote_events=remote,
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

        def _page(self) -> None:
            """The one page a browser gets without a token.

            The HTML is not a secret — it contains no data, only the URLs it will ask
            for, and those need a token like everything else. no-store because an
            upgraded hub serving last month's page from a cache would look like a
            missing feature.
            """
            body = page()
            if not body:
                self._refuse(404, "no page is installed")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

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
                # Close the socket rather than keeping it alive. This is HTTP/1.1 and
                # the stream has no Content-Length, so a client cannot tell that the
                # body has ended: when the hub stopped, the handler returned, the
                # connection was held open for a next request that never came, and the
                # client sat blocked on a socket that would never say anything again —
                # for ever, through restarts. Measured: it never noticed in fifteen
                # seconds. Closing gives it the EOF it reconnects on.
                self.close_connection = True
                if listener.dropped:
                    log.info("%s missed %d event(s) — it was not reading",
                             who, listener.dropped)

    return Handler
