"""Shared status model and event bus.

Everything that cares about service state — the tray icon, the flyout, the
watchdog, the history log, a running stack — subscribes here instead of being
called directly by whatever noticed the change. Before this existed the SCM
handler poked the spinner, the icon and the flyout by hand, and each new feature
meant another hard-wired call.

Publishing is deliberately synchronous and off the UI thread: subscribers must
not block, and UI subscribers are responsible for marshalling onto their own
thread (Qt signals do this for us).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

# Statuses the SCM can report. Kept as plain strings: they cross process and
# file boundaries (history, config) and reading them raw is a feature.
RUNNING = "Running"
STOPPED = "Stopped"
PAUSED = "Paused"
NOT_FOUND = "Not Found"
UNKNOWN = "Unknown"
PENDING = frozenset({"Starting", "Stopping", "Resuming", "Pausing"})

#: Where a change came from — carried into history so a timeline reads as a story.
SRC_SCM = "scm"          # observed, cause unknown (external or a crash)
SRC_PANEL = "panel"      # the user pressed a button in our UI
SRC_WATCHDOG = "watchdog"
SRC_STACK = "stack"
SRC_SCHEDULE = "schedule"   # a trigger fired


def category(status: str) -> str:
    if status == RUNNING:
        return "running"
    if status == STOPPED:
        return "stopped"
    if status == PAUSED:
        return "paused"
    if status in PENDING:
        return "pending"
    return "none"


def is_pending(status: str) -> bool:
    return status in PENDING


@dataclass(frozen=True)
class ServiceState:
    name: str
    machine: str = ""
    status: str = UNKNOWN
    exit_code: int = 0
    pid: int = 0
    since: float = field(default_factory=time.monotonic)

    @property
    def key(self) -> tuple:
        return (self.machine or "", self.name)


@dataclass(frozen=True)
class Event:
    """A state change. `previous` is None the first time we see a service."""
    state: ServiceState
    previous: str | None
    source: str = SRC_SCM

    @property
    def name(self) -> str:
        return self.state.name

    @property
    def status(self) -> str:
        return self.state.status

    @property
    def is_stop(self) -> bool:
        return self.status == STOPPED and self.previous != STOPPED

    @property
    def crashed(self) -> bool:
        """A stop the service didn't ask for. A clean stop reports exit code 0;
        anything else means it died (1067 = process terminated unexpectedly)."""
        return self.is_stop and self.state.exit_code not in (0,)


class Store:
    """Thread-safe status map plus a subscriber list."""

    def __init__(self):
        self._states: dict = {}
        self._lock = threading.RLock()
        self._subs: list = []
        #: services whose next stop we caused ourselves, so the watchdog can
        #: tell "I stopped it" from "it fell over".
        self._expected: dict = {}

    # -- subscription ------------------------------------------------------
    def subscribe(self, fn) -> None:
        with self._lock:
            self._subs.append(fn)

    def unsubscribe(self, fn) -> None:
        with self._lock:
            if fn in self._subs:
                self._subs.remove(fn)

    def _emit(self, event: Event) -> None:
        for fn in list(self._subs):
            try:
                fn(event)
            except Exception:
                # A broken subscriber must never take down the notifier thread.
                pass

    # -- reading -----------------------------------------------------------
    def get(self, name: str, machine: str = "") -> ServiceState | None:
        with self._lock:
            return self._states.get((machine or "", name))

    def status_of(self, name: str, machine: str = "") -> str:
        st = self.get(name, machine)
        return st.status if st else UNKNOWN

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._states)

    def counts(self) -> tuple:
        """(running, total) over everything we're tracking."""
        with self._lock:
            states = list(self._states.values())
        return sum(1 for s in states if s.status == RUNNING), len(states)

    def any_pending(self) -> bool:
        with self._lock:
            return any(is_pending(s.status) for s in self._states.values())

    def forget(self, name: str, machine: str = "") -> None:
        with self._lock:
            self._states.pop((machine or "", name), None)

    def keep_only(self, keys) -> None:
        """Drop services that are no longer configured."""
        wanted = {(m or "", n) for m, n in keys}
        with self._lock:
            for k in [k for k in self._states if k not in wanted]:
                del self._states[k]

    # -- writing -----------------------------------------------------------
    def update(self, name: str, status: str, machine: str = "",
               exit_code: int = 0, pid: int = 0, source: str = SRC_SCM):
        """Record a status. Returns the Event if it changed, else None."""
        key = (machine or "", name)
        with self._lock:
            old = self._states.get(key)
            if old is not None and old.status == status:
                return None
            state = ServiceState(name=name, machine=machine or "", status=status,
                                 exit_code=exit_code, pid=pid)
            self._states[key] = state
            previous = old.status if old else None

            # An expected stop is consumed the moment we see the stop it was
            # registered for; anything later is a genuine surprise.
            src = source
            if status == STOPPED and self._expected.pop(key, None):
                src = SRC_PANEL if source == SRC_SCM else source

        event = Event(state=state, previous=previous, source=src)
        self._emit(event)
        return event

    # -- intent ------------------------------------------------------------
    def expect_stop(self, name: str, machine: str = "") -> None:
        """We are about to stop this service on purpose."""
        with self._lock:
            self._expected[(machine or "", name)] = time.monotonic()

    def clear_expected(self, name: str, machine: str = "") -> None:
        with self._lock:
            self._expected.pop((machine or "", name), None)


#: The process-wide store. Tests build their own instead of using this.
store = Store()
