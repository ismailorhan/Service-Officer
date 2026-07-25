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


def _parse_ts(text) -> datetime | None:
    """History timestamps carry an offset; comparisons here are naive local."""
    try:
        parsed = datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed


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
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=self._tick + 1)

    @property
    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # -- decisions ---------------------------------------------------------
    @staticmethod
    def signature(trigger) -> tuple:
        """What "already ran" is remembered against.

        Keying on the name alone was a trap: a trigger that had already run today
        stayed blocked for the rest of the day, so moving it from 12:11 to 15:52
        did nothing until tomorrow. Any change to the schedule itself must clear
        that memory.
        """
        return (trigger.when, trigger.time_of_day, tuple(sorted(trigger.days)),
                int(trigger.repeat_seconds or 0))

    def _already_ran(self, trigger, occurrence) -> bool:
        remembered = self._last_run.get(trigger.name)
        if not remembered:
            return False
        signature, when = remembered
        return signature == self.signature(trigger) and when == occurrence

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

        occurrence = self.occurrence_for(trigger, now)
        if self._already_ran(trigger, occurrence):
            return False
        repeat = max(0, int(trigger.repeat_seconds or 0))
        reference = start if not repeat else occurrence
        # Fire late if we were asleep or the app was closed, but not all day.
        return now - reference <= timedelta(minutes=CATCH_UP_MINUTES)

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

    def seed_from(self, records) -> int:
        """Remember runs that happened before this process started.

        Without this the memory of "already ran today" dies with the app, and
        restarting inside the catch-up window fires everything again — measured:
        a trigger set for 16:12 fired at 16:31 because the app had been
        restarted at 16:29. Records are the history's run entries, newest first;
        only the newest per trigger matters.

        A record can only be matched to the schedule that is configured *now*,
        so a trigger whose time has since been edited is correctly not blocked.
        """
        by_name = {t.name: t for t in self._config().triggers}
        seeded = 0
        for rec in records:
            name = rec.get("name")
            trigger = by_name.pop(name, None)
            if trigger is None or trigger.when != "time":
                continue
            when = _parse_ts(rec.get("ts"))
            if when is None:
                continue
            occurrence = self.occurrence_for(trigger, when)
            # Only if that scheduled moment had actually arrived: a "Run now"
            # at 09:00 must not cancel the 22:00 trigger it belongs to.
            if self._passed(trigger, when):
                self._last_run[trigger.name] = (self.signature(trigger), occurrence)
                seeded += 1
            if not by_name:
                break
        return seeded

    def _passed(self, trigger, moment: datetime) -> bool:
        """Had this trigger's time already come by `moment` that day?"""
        try:
            hour, minute = (int(p) for p in trigger.time_of_day.split(":"))
        except (ValueError, AttributeError):
            return False
        return moment >= moment.replace(hour=hour, minute=minute, second=0,
                                        microsecond=0)

    def mark_ran(self, trigger, when=None) -> None:
        occurrence = (when if when is not None
                      else self.occurrence_for(trigger, self._now()))
        self._last_run[trigger.name] = (self.signature(trigger), occurrence)

    def next_run_at(self, trigger, now: datetime = None):
        """When this will next fire, so the UI can say so instead of the user
        having to trust it. None for a startup trigger or a disabled one."""
        now = now or self._now()
        if not trigger.enabled or trigger.when != "time":
            return None
        try:
            hour, minute = (int(p) for p in trigger.time_of_day.split(":"))
        except (ValueError, AttributeError):
            return None
        repeat = max(0, int(trigger.repeat_seconds or 0))

        def allowed(moment) -> bool:
            return not trigger.days or moment.weekday() in trigger.days

        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if repeat:
            while candidate <= now:
                candidate += timedelta(seconds=repeat)
                if candidate.date() != now.date():
                    candidate = candidate.replace(hour=hour, minute=minute)
                    break
        for _ in range(8):                     # today, then the next week
            if allowed(candidate) and candidate > now and not self._already_ran(
                    trigger, self.occurrence_for(trigger, candidate)):
                return candidate
            candidate = (candidate + timedelta(days=1)).replace(hour=hour,
                                                                minute=minute)
        return None

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
