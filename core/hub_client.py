"""The client half: read a hub as if it were a local store.

`RemoteStore` answers `state.READ_API` from the last snapshot the hub sent, kept current
by an event stream. Everything above it — the flyout, the hover card, the tray, four
pages — asks the same questions it asks the local store and never learns which it has.

**Not a subclass of Store**, deliberately. It holds no data of its own and its writes
would be lies; inheriting would make any method it forgot return a plausible empty answer
from the parent instead of failing where it is written. So the surface is satisfied by
hand and checked against READ_API by a test.

The connection is one thread reading `text/event-stream`, reconnecting with a backoff
capped at half a minute. On every reconnect it fetches a *fresh snapshot before* resuming
the stream, because whatever happened while it was away was never in the stream to begin
with — resuming alone would leave the panel confidently showing a state from before the
outage.
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import applog, wire
from . import state as st

log = applog.get("hubclient")

#: Reconnect delays, in seconds. Capped: a client that backs off for ten minutes is a
#: panel showing yesterday, and the hub coming back is the common case.
BACKOFF = (1, 2, 4, 8, 15, 30)
#: How long a normal request may take. The hub answers in under a millisecond on an open
#: connection; anything near this means the network, not the hub.
TIMEOUT = 15


class _Stream:
    """An open event stream as a context manager. Iterating it yields lines.

    It carries the socket as well as the response because an SSE response has no length
    and no chunked framing, so `HTTPConnection.getresponse()` hands the socket over to
    the response and forgets it — and the socket is the only thing that can interrupt a
    blocked read. See HubClient.stop.
    """

    def __init__(self, answer, sock):
        self._answer, self.sock = answer, sock

    def __iter__(self):
        return iter(self._answer)

    def shutdown(self) -> None:
        """Unblock whoever is reading. Called from another thread, on purpose."""
        try:
            if self.sock is not None:
                self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass          # already gone, which is the outcome we wanted

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        try:
            self._answer.close()
        except Exception:
            pass
        return False


class Unreachable(RuntimeError):
    """The hub did not answer at all."""


class Refused(RuntimeError):
    """The hub answered and would not have us — a wrong or revoked token."""


class Conflict(RuntimeError):
    """Somebody else saved first. Carries the hub's current etag so the caller can
    fetch, merge and try again rather than guess what it missed."""

    def __init__(self, text: str, etag: str = ""):
        super().__init__(text)
        self.etag = etag


class Busy(RuntimeError):
    """Somebody is already acting on that service — possibly on another client."""

    def __init__(self, text: str, actor: str = "", action: str = ""):
        super().__init__(text)
        self.actor, self.action = actor, action


class NotFound(RuntimeError):
    """The hub has no such thing — a client name that is not paired, a path that does not
    exist. Its own class because "there was nothing to remove" is an ordinary answer to a
    revoke and should not have to be recognised from the text of an error."""


class WrongHub(RuntimeError):
    """The certificate is not the one we pinned. Either the hub was rebuilt, or this is
    not the hub."""


class RemoteStore:
    """The read half of a store, answered from the hub's last snapshot."""

    def __init__(self):
        self._lock = threading.RLock()
        self.host = ""                 # the computer the hub runs on
        self._services: dict = {}      # (machine, name) -> row
        self._machines: dict = {}      # machine -> row
        self._subs: list = []

    # -- filling it in (the client's business, not a caller's) -------------
    def apply_snapshot(self, shot: dict) -> None:
        with self._lock:
            #: The computer the hub runs on, so a panel can say which machine that row is
            #: rather than showing its own name for it.
            self.host = shot.get("host", "") or self.host
            self._services = {(r.get("machine", ""), r["name"]): r
                              for r in shot.get("services", [])}
            self._machines = {r["name"]: r for r in shot.get("machines", [])}

    def apply_event(self, raw: dict) -> None:
        """One status change: update the row and tell the subscribers.

        The row may be for a service this client has never seen — a service added on
        another client — in which case the event is kept and the next snapshot fills in
        the rest of the row.
        """
        key = (raw.get("machine", "") or "", raw.get("service", ""))
        with self._lock:
            row = dict(self._services.get(key) or
                       {"name": key[1], "machine": key[0], "label": key[1]})
            row["status"] = raw.get("status", st.UNKNOWN)
            self._services[key] = row
        event = wire.state_from_event(raw)
        for fn in list(self._subs):
            try:
                fn(event)
            except Exception:
                log.exception("a subscriber failed handling a hub event")

    def apply_machine(self, raw: dict) -> None:
        """A machine started or stopped answering.

        Kept even for a machine no snapshot has mentioned — one added on another client —
        so the row is right before the next snapshot rather than after it.
        """
        name = raw.get("machine", "") or ""
        with self._lock:
            row = dict(self._machines.get(name) or {"name": name, "label": name})
            row["reachable"] = bool(raw.get("reachable"))
            row["detail"] = raw.get("detail", "")
            row["at"] = raw.get("at", 0)
            self._machines[name] = row

    def apply_start_type(self, raw: dict) -> None:
        """A service's startup type changed. Kept even for a row no snapshot has mentioned,
        the same way a status event is."""
        key = (raw.get("machine", "") or "", raw.get("service", ""))
        with self._lock:
            row = dict(self._services.get(key) or
                       {"name": key[1], "machine": key[0], "label": key[1]})
            row["start_type"] = raw.get("start_type", "")
            row["disabled"] = bool(raw.get("disabled"))
            self._services[key] = row

    def apply_health(self, raw: dict) -> None:
        key = (raw.get("machine", "") or "", raw.get("service", ""))
        with self._lock:
            # Kept even for a service this client has never seen — one added on another
            # client — the same way a status event is, so the next snapshot fills in the rest
            # of the row rather than the verdict being the thing that goes missing.
            row = dict(self._services.get(key) or
                       {"name": key[1], "machine": key[0], "label": key[1]})
            row["health"] = raw.get("verdict", "unknown")
            row["health_detail"] = raw.get("detail", "")
            self._services[key] = row

    # -- the read API ------------------------------------------------------
    def _row(self, name: str, machine: str = "") -> dict:
        with self._lock:
            return self._services.get((machine or "", name)) or {}

    def status_of(self, name: str, machine: str = "") -> str:
        return self._row(name, machine).get("status", st.UNKNOWN)

    def get(self, name: str, machine: str = ""):
        """A ServiceState, for the callers that want more than a word. Built rather
        than stored: the hub sends rows, and a row is what the panel draws."""
        row = self._row(name, machine)
        if not row:
            return None
        return st.ServiceState(name=name, machine=machine or "",
                               status=row.get("status", st.UNKNOWN))

    def snapshot(self) -> dict:
        with self._lock:
            return {key: st.ServiceState(name=key[1], machine=key[0],
                                         status=row.get("status", st.UNKNOWN))
                    for key, row in self._services.items()}

    def counts(self) -> tuple:
        """(running, total) — exactly what `Store.counts()` answers.

        It used to return (running, stopped, other), with a docstring claiming it was
        "the way the local store counts them". It was not, and the tray unpacks two:
        `running, total = self._store.counts()` raised ValueError the instant the app
        was launched as a client. Every contract test passed, because they only asked
        whether the method existed — see the shape tests in tests/test_store_contract.py.
        """
        with self._lock:
            rows = list(self._services.values())
        return sum(1 for r in rows if r.get("status") == st.RUNNING), len(rows)

    def any_pending(self) -> bool:
        with self._lock:
            return any(st.is_pending(r.get("status", ""))
                       for r in self._services.values())

    def health_of(self, name: str, machine: str = "") -> str:
        return self._row(name, machine).get("health", "unknown")

    def health_detail(self, name: str, machine: str = "") -> str:
        return self._row(name, machine).get("health_detail", "")

    def health_timing(self, name: str, machine: str = "") -> dict:
        return dict(self._row(name, machine).get("health_timing") or {})

    def start_type(self, name: str, machine: str = "") -> str:
        return self._row(name, machine).get("start_type", "")

    def is_disabled(self, name: str, machine: str = "") -> bool:
        row = self._row(name, machine)
        return bool(row.get("disabled")) or row.get("start_type") == "Disabled"

    def machine_state(self, machine: str = "") -> dict:
        with self._lock:
            row = self._machines.get(machine or "")
        if not row or row.get("reachable") is None:
            return {}
        return {"reachable": bool(row.get("reachable")),
                "detail": row.get("detail", ""),
                "at": row.get("at", 0), "wall": row.get("at", 0)}

    def subscribe(self, fn) -> None:
        with self._lock:
            if fn not in self._subs:
                self._subs.append(fn)

    def unsubscribe(self, fn) -> None:
        with self._lock:
            if fn in self._subs:
                self._subs.remove(fn)

    # -- the write API, which is not ours ---------------------------------
    def _refuse(self, *_args, **_kw):
        raise ReadOnly("the hub is the only writer; send it a request instead")

    update = set_start_type = set_health = set_health_timing = _refuse
    note_machine = expect_stop = clear_expected = keep_only = forget = _refuse


class ReadOnly(RuntimeError):
    """A write reached the client's store. The hub is the only writer — a client that
    updated its own copy would show something nobody else could see."""


class HubClient:
    def __init__(self, url: str, token=None, fingerprint: str = "",
                 on_event=None, on_connected=None):
        self.url = url.rstrip("/")
        #: Tokens to try, best guess first. There can genuinely be two on one computer —
        #: this user's and the machine's — and only the hub knows which it still accepts.
        #: One stale one used to be the end of it: an upgrade replaced the machine's token
        #: on 2026-07-28, the copy in this user's profile was then refused, and a panel
        #: that had been working could not reconnect. A single string is still accepted.
        self._tokens = [t for t in ([token] if isinstance(token, str)
                                    else list(token or [])) if t]
        self.token = self._tokens[0] if self._tokens else ""
        self.fingerprint = fingerprint or ""
        self.store = RemoteStore()
        self.connected = False
        self._on_event = on_event
        self._on_connected = on_connected
        self._stop = threading.Event()
        self._thread = None
        #: The open event stream, so stop() can close it from another thread — see stop().
        self._stream = None
        self._events = threading.Event()      # set whenever anything arrives
        self._last_snapshot: dict = {}

    # -- identity ----------------------------------------------------------
    def _context(self):
        """TLS with the pin as the verification. A self-signed certificate cannot be
        checked against a chain, so `CERT_NONE` plus a fingerprint comparison *is* the
        check — and it happens on every connection, not only the first."""
        if not self.url.startswith("https"):
            return None
        return ssl._create_unverified_context()

    def peer_fingerprint(self) -> str:
        parsed = urllib.parse.urlparse(self.url)
        with socket.create_connection((parsed.hostname, parsed.port or 8797),
                                      timeout=TIMEOUT) as raw:
            with self._context().wrap_socket(raw) as tls:
                der = tls.getpeercert(binary_form=True)
        return "SHA256:" + base64.b64encode(
            hashlib.sha256(der).digest()).decode("ascii").rstrip("=")

    def check_identity(self) -> str:
        """The hub's fingerprint, pinning it the first time and refusing a change after.

        Returned so the caller can store it — the same shape as the Machines page's
        "Get it", and for the same reason: read over the network it proves nothing on
        its own, but a *change* proves something.
        """
        if not self.url.startswith("https"):
            return ""
        found = self.peer_fingerprint()
        if not self.fingerprint:
            self.fingerprint = found
            log.info("pinned the hub's certificate: %s", found)
            return found
        if found != self.fingerprint:
            raise WrongHub(
                f"the hub's certificate has changed. It was {self.fingerprint} and is "
                f"now {found}. Either that machine was rebuilt, or this is not the "
                f"same hub — check before accepting it.")
        return found

    # -- requests ----------------------------------------------------------
    def _ask(self, method: str, path: str, body=None, timeout: float = TIMEOUT):
        request = urllib.request.Request(
            self.url + path,
            data=json.dumps(body).encode("utf-8") if body is not None else None,
            method=method,
            headers={"Authorization": f"Bearer {self.token}",
                     "Content-Type": "application/json",
                     # Which machine this is. It identifies, it does not authenticate —
                     # the token does that — and it is what lets the hub's client list say
                     # where a token is actually being used from.
                     "X-Client-Host": socket.gethostname()})
        try:
            with urllib.request.urlopen(request, timeout=timeout,
                                        context=self._context()) as answer:
                raw = answer.read().decode("utf-8")
                return answer.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                said = json.loads(raw) if raw else {}
            except ValueError:
                said = {"error": raw[:200]}
            if exc.code == 401:
                if self._next_token():
                    # The other token this computer holds. Not a loop: _next_token only
                    # ever moves forward through a list of at most two.
                    return self._ask(method, path, body, timeout)
                raise Refused(said.get("error") or "the hub refused this token") \
                    from exc
            if exc.code == 404:
                raise NotFound(said.get("error") or "the hub has no such thing") from exc
            if exc.code == 409:
                if said.get("actor") is not None:
                    raise Busy(said.get("error", "already being acted on"),
                               actor=said.get("actor", ""),
                               action=said.get("action", "")) from exc
                raise Conflict(said.get("error") or "somebody else saved first",
                               etag=said.get("etag", "")) from exc
            raise RuntimeError(said.get("error") or f"the hub said {exc.code}") from exc
        except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
            raise Unreachable(f"{self.url} did not answer: {exc}") from exc

    def _next_token(self) -> bool:
        """Move to the next token this computer holds. True if there was another."""
        if self.token in self._tokens:
            following = self._tokens[self._tokens.index(self.token) + 1:]
        else:
            following = self._tokens
        if not following:
            return False
        self.token = following[0]
        log.info("that token was refused; trying the other one this computer holds")
        return True

    @property
    def host(self) -> str:
        """The computer the hub runs on, from its snapshot.

        Exposed here as well as on the store because a panel holds the client, not the
        store — and asking the wrong object for it got "" and therefore a machine row that
        showed this workstation's own name for the hub's computer. Found by the end-to-end
        run, which is the only place the two names differ.
        """
        return getattr(self.store, "host", "") or ""

    def ping(self) -> dict:
        return self._ask("GET", "/api/v1/ping")[1]

    def refresh_now(self) -> dict:
        """Fetch a snapshot and apply it. Used on connect, on reconnect, and by the
        Refresh button."""
        _status, shot = self._ask("GET", "/api/v1/snapshot")
        self._last_snapshot = shot or {}
        self.store.apply_snapshot(self._last_snapshot)
        return self._last_snapshot

    def snapshot(self) -> dict:
        return dict(self._last_snapshot)

    def act(self, action: str, service: str, machine: str = "",
            actor: str = "") -> str:
        _status, said = self._ask("POST", "/api/v1/actions",
                                  {"action": action, "service": service,
                                   "machine": machine, "actor": actor})
        return (said or {}).get("id", "")

    def run_stack(self, name: str, actor: str = "") -> bool:
        self._ask("POST", "/api/v1/stacks/run", {"name": name, "actor": actor})
        return True

    def run_trigger(self, name: str, actor: str = "") -> bool:
        self._ask("POST", "/api/v1/triggers/run", {"name": name, "actor": actor})
        return True

    def set_hub_port(self, port: int) -> dict:
        """Ask the hub to listen somewhere else. It answers before it moves."""
        _status, said = self._ask("POST", "/api/v1/hub/port", {"port": int(port)})
        return said or {}

    def follow_to_port(self, port: int) -> str:
        """Point this client at the same hub on a different port, and reconnect.

        Every client gets this, not only whoever asked: they are all on the old socket, and a
        client that kept retrying the old number would sit disconnected for ever with nothing
        on screen to explain it.
        """
        parsed = urllib.parse.urlparse(self.url)
        host = parsed.hostname or ""
        if not host or not port:
            return self.url
        where = f"[{host}]" if ":" in host else host
        self.url = f"{parsed.scheme}://{where}:{int(port)}"
        log.info("the hub moved to port %s; following it", port)
        # Drop the stream so the reader stops waiting on a socket that is about to close and
        # reconnects to the new address on its next turn.
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass
        return self.url

    def refresh_machine(self, machine: str = None) -> None:
        self._ask("POST", "/api/v1/refresh", {"machine": machine or ""})

    def config(self):
        """(Config, etag)."""
        _status, payload = self._ask("GET", "/api/v1/config")
        return wire.config_from_payload(payload or {})

    def save_config(self, cfg, etag: str, actor: str = "") -> None:
        payload = wire.config_payload(cfg)
        payload["etag"] = etag
        payload["actor"] = actor
        self._ask("PUT", "/api/v1/config", payload)

    def history(self, **filters) -> list:
        query = urllib.parse.urlencode({k: v for k, v in filters.items()
                                        if v not in (None, "")})
        _status, said = self._ask("GET", "/api/v1/history"
                                  + (f"?{query}" if query else ""))
        return (said or {}).get("rows", [])

    def services_on(self, machine: str) -> list:
        _status, said = self._ask(
            "GET", f"/api/v1/machines/{urllib.parse.quote(machine)}/services",
            timeout=90)          # enumerating a remote machine is the request
        return (said or {}).get("services", [])

    # -- who may connect ---------------------------------------------------
    def clients(self) -> dict:
        """Everybody paired with this hub, and what a new one would need to know.

        No tokens: the hub keeps a SHA-256 of each and nothing else, so there is nothing to
        show. Names, when each was issued and when it was last used — which is the question
        somebody actually has.
        """
        _status, said = self._ask("GET", "/api/v1/clients")
        return said or {"clients": []}

    def add_client(self, name: str, description: str = "") -> dict:
        """Issue a token and return it *once*, with the command to run on that machine.

        The hub does this rather than the panel because the client list lives in a store
        only administrators can write, and the panel does not run elevated.
        """
        _status, said = self._ask("POST", "/api/v1/clients",
                                  {"name": name, "description": description})
        return said or {}

    def revoke_client(self, name: str) -> bool:
        """True if there was one to revoke. Effective immediately, including on a hub that
        is already running — see hub_auth's cache."""
        try:
            status, _said = self._ask(
                "DELETE", f"/api/v1/clients/{urllib.parse.quote(name)}")
        except NotFound:
            return False
        return status in (200, 204)

    # -- the stream --------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._listen, daemon=True,
                                        name="hub-client")
        self._thread.start()

    def stop(self) -> None:
        """Stop reading, and mean it.

        The reader spends its life blocked in a socket read, and `_stop` is only looked
        at between lines — a stream is silent for up to KEEPALIVE_SECONDS at a time, so
        waiting politely took 20.1 s, measured. Shutting the socket down from here is
        what unblocks it: the read returns nothing, and `_listen` sees `_stop`.

        It has to be honest about this because `start()` refuses to run while the old
        thread is alive — a stop that had not finished made the next start a silent
        no-op, and the client then sat on a stream nobody was managing. It also runs on
        the way out of the application, where twenty seconds is a window that will not
        close.
        """
        self._stop.set()
        held = self._stream
        if held is not None:
            held.shutdown()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=5)
        self.connected = False

    def _listen(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            try:
                self.check_identity()
                # The stream first, then the snapshot, then the events.
                #
                # The hub queues events per listener from the moment the stream is
                # opened, so anything that happens while the snapshot is being taken is
                # waiting in that queue and lands *after* it — in order, and applied to
                # a store that already has the snapshot under it.
                #
                # The other order leaves a gap between the snapshot and the stream in
                # which a change is published to nobody. That is not a moment of
                # staleness: a store publishes only *changes*, so the client would go on
                # showing the old status until the same service changed again. Found by
                # the reconnect test failing under load, which is the only reason it was
                # ever more than theoretical.
                with self._open_stream() as stream:
                    self.refresh_now()
                    self._mark(True)
                    attempt = 0
                    self._pump(stream)
            except (Unreachable, WrongHub, Refused) as exc:
                self._mark(False)
                if attempt == 0:
                    log.info("hub unavailable: %s", exc)
            except Exception:
                self._mark(False)
                if self._stop.is_set():
                    # We shut the socket down ourselves — see stop(). The read fails with
                    # WinError 10053, and logging that as an ERROR with a stack put a
                    # frightening entry in the log every time the app was closed
                    # normally. The log is the file somebody reads when something has
                    # actually gone wrong.
                    log.info("hub connection closed on the way out")
                else:
                    log.exception("the hub connection failed")
            if self._stop.is_set():
                return
            self._mark(False)
            delay = BACKOFF[min(attempt, len(BACKOFF) - 1)]
            attempt += 1
            self._stop.wait(delay)

    def _open_stream(self):
        """Subscribe, and keep the connection so `stop()` can reach its socket.

        `http.client` rather than `urlopen` for this one request, deliberately: urlopen
        hands back a response with no way to get at the socket underneath, and closing
        the response does **not** interrupt a read that is already blocked on it —
        measured at 20.1 s to stop, which is the keepalive interval, because that is how
        long it took for something to arrive and the loop to look at `_stop`.

        A shutdown on the socket does interrupt it. Everything else here (the pin, the
        token) is the same as any other request.
        """
        parsed = urllib.parse.urlparse(self.url)
        port = parsed.port or 8797
        if parsed.scheme == "https":
            conn = http.client.HTTPSConnection(parsed.hostname, port,
                                               timeout=TIMEOUT,
                                               context=self._context())
        else:
            conn = http.client.HTTPConnection(parsed.hostname, port, timeout=TIMEOUT)
        conn.request("GET", "/api/v1/events",
                     headers={"Authorization": f"Bearer {self.token}",
                              "X-Client-Host": socket.gethostname()})
        # Taken before getresponse(), which gives the socket away — see _Stream.
        sock = conn.sock
        # No socket timeout while reading: a stream is meant to say nothing for long
        # stretches, and the keepalive is what proves it is alive.
        if sock is not None:
            sock.settimeout(None)
        answer = conn.getresponse()
        if answer.status != 200:
            conn.close()
            answer.close()
            raise Refused(f"the hub refused the event stream: {answer.status}")
        self._stream = _Stream(answer, sock)
        return self._stream

    def _pump(self, stream) -> None:
        """Every event, until the stream ends or this client is asked to stop."""
        for raw in stream:
            if self._stop.is_set():
                return
            if not raw:
                return          # the socket was shut down under us: see stop()
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue          # a comment: the keepalive, or ": open"
            try:
                payload = json.loads(line[5:])
            except ValueError:
                continue
            self._handle(payload)

    def _handle(self, payload: dict) -> None:
        kind = payload.get("kind")
        if kind == "status":
            self.store.apply_event(payload)
        elif kind == "health":
            self.store.apply_health(payload)
        elif kind == "machine":
            self.store.apply_machine(payload)
        elif kind == "start_type":
            self.store.apply_start_type(payload)
        elif kind == "action":
            # Nothing to store: an action is a moment, not a state. It goes straight to
            # _on_event, which is where the window is.
            pass
        elif kind == "hub_port":
            # Before the hub moves, so this client knows where to reconnect. The stored
            # address is updated too, or the next launch would go to the old port.
            self.follow_to_port(payload.get("port", 0))
        elif kind == "gap":
            # The hub dropped events for this connection, so what is held is no longer
            # reliable and it cannot be told which are missing. One fresh snapshot is the
            # whole repair. Not on this thread: this is the reader, and a request made from
            # it would stall the very stream the answer has to arrive alongside.
            log.info("the hub dropped %s event(s) for this client; reading a fresh snapshot",
                     payload.get("missed", "?"))
            threading.Thread(target=self._resync, daemon=True,
                             name="hub-resync").start()
        self._events.set()
        if self._on_event is not None:
            try:
                self._on_event(payload)
            except Exception:
                log.exception("a hub event listener failed")

    def _resync(self) -> None:
        """Read the whole state again, after a gap. Failure is not fatal: the stream is still
        open and the next reconnect takes a snapshot anyway."""
        try:
            self.refresh_now()
        except Exception as exc:
            log.info("could not resync after a gap: %s", exc)

    def _mark(self, connected: bool) -> None:
        if connected == self.connected:
            return
        self.connected = connected
        log.info("hub %s", "connected" if connected else "disconnected")
        if self._on_connected is not None:
            try:
                self._on_connected(connected)
            except Exception:
                log.exception("a connection listener failed")

    # -- waiting, for tests and for the panel ------------------------------
    def wait_for_event(self, timeout: float = 5.0) -> bool:
        self._events.clear()
        return self._events.wait(timeout)

    def wait_for(self, predicate, timeout: float = 10.0) -> bool:
        """True as soon as `predicate()` is, so neither a test nor the panel has to
        sleep and hope."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if predicate():
                    return True
            except Exception:
                pass
            time.sleep(0.02)
        return False
