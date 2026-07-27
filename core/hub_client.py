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


class WrongHub(RuntimeError):
    """The certificate is not the one we pinned. Either the hub was rebuilt, or this is
    not the hub."""


class RemoteStore:
    """The read half of a store, answered from the hub's last snapshot."""

    def __init__(self):
        self._lock = threading.RLock()
        self._services: dict = {}      # (machine, name) -> row
        self._machines: dict = {}      # machine -> row
        self._subs: list = []

    # -- filling it in (the client's business, not a caller's) -------------
    def apply_snapshot(self, shot: dict) -> None:
        with self._lock:
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

    def apply_health(self, raw: dict) -> None:
        key = (raw.get("machine", "") or "", raw.get("service", ""))
        with self._lock:
            row = self._services.get(key)
            if row is None:
                return
            row["health"] = raw.get("verdict", "unknown")
            row["health_detail"] = raw.get("detail", "")

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
        """(running, stopped, other), the way the local store counts them."""
        with self._lock:
            rows = list(self._services.values())
        running = sum(1 for r in rows if r.get("status") == st.RUNNING)
        stopped = sum(1 for r in rows if r.get("status") == st.STOPPED)
        return running, stopped, len(rows) - running - stopped

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
    def __init__(self, url: str, token: str, fingerprint: str = "",
                 on_event=None, on_connected=None):
        self.url = url.rstrip("/")
        self.token = token
        self.fingerprint = fingerprint or ""
        self.store = RemoteStore()
        self.connected = False
        self._on_event = on_event
        self._on_connected = on_connected
        self._stop = threading.Event()
        self._thread = None
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
                     "Content-Type": "application/json"})
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
                raise Refused(said.get("error") or "the hub refused this token") \
                    from exc
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

    # -- the stream --------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._listen, daemon=True,
                                        name="hub-client")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=5)
        self.connected = False

    def _listen(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            try:
                self.check_identity()
                # The snapshot first, then the stream: the other order leaves a gap in
                # which a change arrives, is applied to nothing, and is then overwritten
                # by a snapshot taken before it.
                self.refresh_now()
                self._mark(True)
                attempt = 0
                self._read_stream()
            except (Unreachable, WrongHub, Refused) as exc:
                self._mark(False)
                if attempt == 0:
                    log.info("hub unavailable: %s", exc)
            except Exception:
                self._mark(False)
                log.exception("the hub connection failed")
            if self._stop.is_set():
                return
            self._mark(False)
            delay = BACKOFF[min(attempt, len(BACKOFF) - 1)]
            attempt += 1
            self._stop.wait(delay)

    def _read_stream(self) -> None:
        request = urllib.request.Request(
            self.url + "/api/v1/events",
            headers={"Authorization": f"Bearer {self.token}"})
        with urllib.request.urlopen(request, timeout=None,
                                    context=self._context()) as stream:
            for raw in stream:
                if self._stop.is_set():
                    return
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
        self._events.set()
        if self._on_event is not None:
            try:
                self._on_event(payload)
            except Exception:
                log.exception("a hub event listener failed")

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
