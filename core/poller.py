"""Statuses for machines that cannot tell us themselves.

The local SCM pushes: a service changes and we know within ~32 ms, measured. No
remote transport does that yet — `scm.Watcher` subscribes to local services only,
and SSH has nothing to subscribe to until the journal doorbell lands. Without
this, a remote service showed its state once at startup and then sat there being
quietly wrong, which is worse than showing nothing.

So: one thread, and for each machine that has no push, one batched question per
interval. Batched matters. Asking per service would be N round trips to the same
host; `statuses()` asks about all of a host's services in one, which is why it is
part of the connector protocol rather than a loop over `status()`.
"""

from __future__ import annotations

import threading
import time

from . import connectors
from . import state as st


class Poller:
    """Polls the machines that need polling, and nothing else.

    `on_status(name, machine, status)` is called for every answer, on this
    thread — the caller marshals onto its own. Same shape as health.Monitor, for
    the same reason.
    """

    #: How long a machine stays marked unreachable before we try it again, so a
    #: server that is genuinely down costs one failed connection a minute rather
    #: than one every interval.
    RETRY_SECONDS = 60

    def __init__(self, config_getter, on_status, on_unreachable=None,
                 tick_seconds: float = 1.0, now=None):
        self._config = config_getter
        self._on_status = on_status
        self._on_unreachable = on_unreachable or (lambda machine, why: None)
        self._tick = tick_seconds
        self._now = now or time.monotonic
        self._stop = threading.Event()
        self._thread = None
        self._due: dict = {}          # machine -> when to ask next
        self._down: dict = {}         # machine -> when to retry after a failure

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2)

    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # -- what needs polling ------------------------------------------------
    def machines_to_poll(self) -> dict:
        """{machine name: [service names]} for the machines without push.

        A machine is asked about only if something on it is being watched: an
        unused entry in the machines list is a note to the user, not work for us.
        """
        cfg = self._config()
        wanted: dict = {}
        for svc in cfg.services:
            name = svc.machine or ""
            if not name:
                continue          # the local SCM pushes; polling it too is waste
            wanted.setdefault(name, []).append(svc.name)
        out = {}
        for machine, services in wanted.items():
            try:
                if connectors.for_machine(machine).abilities().push:
                    continue      # it will tell us; asking as well is noise
            except Exception:
                pass              # cannot even ask what it can do — so poll it
            out[machine] = services
        return out

    def interval_for(self, machine: str) -> float:
        record = connectors.machine_record(machine)
        return float(getattr(record, "poll_seconds", 5) or 5)

    # -- the loop ----------------------------------------------------------
    def poll_once(self, machine: str, services: list) -> None:
        """One machine, one round trip. Failure marks it unreachable rather than
        leaving stale states on screen — a green row for a server that is down is
        a lie, and the whole point of this app is not telling that lie."""
        try:
            conn = connectors.for_machine(machine)
            batch = getattr(conn, "statuses", None)
            found = (batch(services) if callable(batch)
                     else {name: conn.status(name) for name in services})
        except Exception as exc:
            self._down[machine] = self._now() + self.RETRY_SECONDS
            why = f"{type(exc).__name__}: {exc}"
            for name in services:
                self._on_status(name, machine, None)
            self._on_unreachable(machine, why)
            return
        self._down.pop(machine, None)
        for name in services:
            self._on_status(name, machine, found.get(name))

    def due_now(self) -> list:
        """Which machines to ask on this tick."""
        now = self._now()
        out = []
        for machine, services in self.machines_to_poll().items():
            if self._down.get(machine, 0) > now:
                continue
            if self._due.get(machine, 0) > now:
                continue
            self._due[machine] = now + self.interval_for(machine)
            out.append((machine, services))
        return out

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                for machine, services in self.due_now():
                    if self._stop.is_set():
                        break
                    self.poll_once(machine, services)
            except Exception:
                pass              # a poller that dies leaves the UI frozen in time
            self._stop.wait(self._tick)
