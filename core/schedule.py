"""Triggers: when something should happen, without anyone watching.

Two kinds of "when" today — Windows starting, and a time of day on chosen
days — and two kinds of "what" — run a stack, or act on one service. Both lists
are meant to grow (a maintenance window; sending mail), which is why the trigger
model keeps them apart and this runner only decides *whether* to fire, handing
the doing to a callback.

Time triggers are checked on a slow tick rather than slept until, because a
laptop that suspends through 03:00 should still catch up when it wakes: a
trigger whose time has passed today and hasn't run yet fires late rather than
being skipped.
"""

from __future__ import annotations

import threading
from datetime import date, datetime, timedelta

CATCH_UP_MINUTES = 30      # how late a missed time trigger may still fire


class Scheduler:
    def __init__(self, config_getter, on_fire, tick_seconds: float = 20.0,
                 now=None, log=None):
        """on_fire(trigger) does the work. now() is injectable for tests."""
        self._config = config_getter
        self._on_fire = on_fire
        self._tick = tick_seconds
        self._now = now or datetime.now
        self._log = log or (lambda text: None)
        self._stop = threading.Event()
        self._thread = None
        self._last_run: dict = {}      # trigger name -> date it last fired
        self._started_at = None

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._started_at = self._now()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    @property
    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # -- decisions ---------------------------------------------------------
    def due_at_startup(self) -> list:
        return [t for t in self._config().triggers
                if t.enabled and t.when == "startup"]

    def _time_due(self, trigger, now: datetime) -> bool:
        if not trigger.enabled or trigger.when != "time":
            return False
        if trigger.days and now.weekday() not in trigger.days:
            return False
        try:
            hour, minute = (int(p) for p in trigger.time_of_day.split(":"))
        except (ValueError, AttributeError):
            return False
        start = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now < start:
            return False

        repeat = max(0, int(trigger.repeat_seconds or 0))
        if not repeat:
            if self._last_run.get(trigger.name) == now.date():
                return False           # once a day, already done
            # Fire late if we were asleep or the app was closed, but not all day.
            return now - start <= timedelta(minutes=CATCH_UP_MINUTES)

        # "at 03:00, then every 2 hours": the due moments are start + n*repeat,
        # and we fire at the most recent one we haven't already run.
        elapsed = (now - start).total_seconds()
        occurrence = start + timedelta(seconds=(int(elapsed // repeat) * repeat))
        if self._last_run.get(trigger.name) == occurrence:
            return False
        return (now - occurrence) <= timedelta(minutes=CATCH_UP_MINUTES)

    def occurrence_for(self, trigger, now: datetime):
        """Which scheduled moment a firing belongs to, so repeats aren't
        double-counted. A once-a-day trigger is keyed by date."""
        repeat = max(0, int(trigger.repeat_seconds or 0))
        if trigger.when != "time" or not repeat:
            return now.date()
        hour, minute = (int(p) for p in trigger.time_of_day.split(":"))
        start = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        elapsed = max(0.0, (now - start).total_seconds())
        return start + timedelta(seconds=(int(elapsed // repeat) * repeat))

    def due_now(self, now: datetime = None) -> list:
        now = now or self._now()
        return [t for t in self._config().triggers if self._time_due(t, now)]

    def mark_ran(self, trigger, when=None) -> None:
        self._last_run[trigger.name] = (when if when is not None
                                        else self.occurrence_for(trigger, self._now()))

    # -- the loop ----------------------------------------------------------
    def run_startup_triggers(self) -> None:
        """Called once after the app is up; each waits out its own delay."""
        for trigger in self.due_at_startup():
            delay = max(0, trigger.delay_seconds)
            self._log(f"trigger “{trigger.name}” scheduled {delay}s after startup")

            def fire(t=trigger):
                if not self._stop.is_set():
                    self._fire(t)
            timer = threading.Timer(delay, fire)
            timer.daemon = True
            timer.start()

    def _fire(self, trigger) -> None:
        self._log(f"trigger “{trigger.name}” firing: {trigger.summary()}")
        try:
            self._on_fire(trigger)
        except Exception as exc:
            self._log(f"trigger “{trigger.name}” failed: {exc}")

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._stop.wait(self._tick)
            if self._stop.is_set():
                break
            now = self._now()
            for trigger in self.due_now(now):
                self.mark_ran(trigger, self.occurrence_for(trigger, now))
                self._fire(trigger)
