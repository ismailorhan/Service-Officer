"""Recovery: bring a service back when it falls over.

The hard part isn't restarting — it's deciding *whether to*. Three kinds of stop
look identical in the status alone, so we use two extra signals:

* our own actions register an expected stop in the store beforehand, so a stop
  we caused is never undone;
* everything else is judged by the service's exit code. A clean stop reports 0
  — that's an administrator stopping it in services.msc, and fighting them would
  be obnoxious. A non-zero code (1067 = process terminated unexpectedly) is a
  crash, which is what we're here for.

A service that cannot stay up must not be restarted forever, so attempts are
bounded, spaced with backoff, and a flap guard gives up entirely if it keeps
dying inside a window.
"""

from __future__ import annotations

import threading
import time

from . import state as st


class Watchdog:
    def __init__(self, config_getter, control, store: st.Store,
                 notify=None, on_log=None, timer_factory=None):
        """config_getter: () -> core.config.Config
        control: object with start_service(name, machine="")
        notify: (title, text) -> None
        on_log: (event, note) -> None, for history annotations
        timer_factory: for tests — (delay, fn) -> object with .start()/.cancel()
        """
        self._config = config_getter
        self._control = control
        self._store = store
        self._notify = notify or (lambda title, text: None)
        self._on_log = on_log or (lambda event, note: None)
        self._timer_factory = timer_factory or self._real_timer

        self._lock = threading.RLock()
        self._attempts: dict = {}      # key -> attempts made in this episode
        self._timers: dict = {}        # key -> pending timer
        self._stops: dict = {}         # key -> [monotonic timestamps]
        self._suspended: set = set()   # keys we've given up on
        self._running_since: dict = {} # key -> monotonic when it reached Running

    # -- lifecycle ---------------------------------------------------------
    @staticmethod
    def _real_timer(delay, fn):
        t = threading.Timer(delay, fn)
        t.daemon = True
        return t

    def attach(self, store: st.Store = None) -> None:
        (store or self._store).subscribe(self.on_event)

    def stop(self) -> None:
        with self._lock:
            for t in self._timers.values():
                try:
                    t.cancel()
                except Exception:
                    pass
            self._timers.clear()

    def is_suspended(self, name: str, machine: str = "") -> bool:
        return (machine or "", name) in self._suspended

    def resume(self, name: str, machine: str = "") -> None:
        key = (machine or "", name)
        with self._lock:
            self._suspended.discard(key)
            self._stops.pop(key, None)
            self._attempts.pop(key, None)

    # -- the rule ----------------------------------------------------------
    def _rules_for(self, name: str, machine: str):
        svc = self._config().service(name, machine)
        return (svc.recovery if svc else None), svc

    def should_recover(self, event: st.Event) -> tuple:
        """Returns (bool, reason). Reason explains a 'no' for the log."""
        if not event.is_stop:
            return False, "not a stop"
        rec, _svc = self._rules_for(event.name, event.state.machine)
        if rec is None:
            return False, "service not configured"
        if not rec.enabled:
            return False, "recovery disabled"
        if event.source != st.SRC_SCM:
            return False, f"stop initiated by {event.source}"
        if self.is_suspended(event.name, event.state.machine):
            return False, "recovery suspended (flapping)"
        if not event.crashed and not rec.restart_on_clean_stop:
            return False, "clean stop left alone"
        return True, "crash" if event.crashed else "clean stop, restart requested"

    # -- event handling ----------------------------------------------------
    def on_event(self, event: st.Event) -> None:
        key = event.state.key

        if event.status == st.RUNNING:
            with self._lock:
                self._running_since[key] = time.monotonic()
            self._cancel(key)
            # Only clear the episode once it has held Running for a while —
            # a service that starts then dies again is still the same episode.
            self._timer_factory(60.0, lambda: self._settle(key)).start()
            return

        ok, reason = self.should_recover(event)
        if not ok:
            return

        rec, _ = self._rules_for(event.name, event.state.machine)
        if self._flapping(key, rec):
            with self._lock:
                self._suspended.add(key)
                self._attempts.pop(key, None)
            self._on_log(event, "recovery suspended: flapping")
            if self._config().notifications.on_give_up:
                self._notify("Service Officer",
                             f"{event.name} keeps stopping — recovery suspended.")
            return

        with self._lock:
            attempts = self._attempts.get(key, 0)
        if rec.max_attempts and attempts >= rec.max_attempts:
            self._on_log(event, f"gave up after {attempts} attempts")
            if self._config().notifications.on_give_up:
                self._notify("Service Officer",
                             f"{event.name} did not come back after "
                             f"{attempts} attempts.")
            return

        if self._config().notifications.on_crash and attempts == 0:
            self._notify("Service Officer", f"{event.name} stopped unexpectedly.")

        delay = rec.delay_for(attempts + 1)
        self._schedule(key, event, delay, attempts + 1)

    # -- internals ---------------------------------------------------------
    def _settle(self, key) -> None:
        with self._lock:
            since = self._running_since.get(key)
            if since and (time.monotonic() - since) >= 59:
                self._attempts.pop(key, None)
                self._stops.pop(key, None)

    def _flapping(self, key, rec) -> bool:
        now = time.monotonic()
        window = rec.flap_window_minutes * 60
        with self._lock:
            stamps = [t for t in self._stops.get(key, []) if now - t <= window]
            stamps.append(now)
            self._stops[key] = stamps
            return len(stamps) >= rec.flap_threshold

    def _cancel(self, key) -> None:
        with self._lock:
            t = self._timers.pop(key, None)
        if t:
            try:
                t.cancel()
            except Exception:
                pass

    def _schedule(self, key, event, delay: float, attempt: int) -> None:
        self._cancel(key)
        machine = event.state.machine
        name = event.name

        def fire():
            with self._lock:
                self._timers.pop(key, None)
                self._attempts[key] = attempt
            # Bail out if it came back by itself, or config changed under us.
            if self._store.status_of(name, machine) == st.RUNNING:
                return
            ok, _ = self.should_recover(event)
            if not ok:
                return
            self._on_log(event, f"watchdog attempt {attempt}")
            try:
                self._control.start_service(name, machine=machine)
            except Exception as exc:
                self._on_log(event, f"attempt {attempt} failed: {exc}")

        timer = self._timer_factory(delay, fire)
        with self._lock:
            self._timers[key] = timer
        timer.start()

    # -- introspection for the UI -----------------------------------------
    def attempts_for(self, name: str, machine: str = "") -> int:
        with self._lock:
            return self._attempts.get((machine or "", name), 0)
