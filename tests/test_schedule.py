"""Triggers decide *whether* to fire; the doing is somebody else's job."""

from datetime import date, datetime

from core import config as cfg_mod
from core.schedule import Scheduler


def at(day, hour, minute):
    return datetime(2026, 7, day, hour, minute)


def build(triggers, now=None):
    cfg = cfg_mod.Config(triggers=triggers)
    fired = []
    sched = Scheduler(lambda: cfg, fired.append, now=lambda: now or at(20, 3, 0))
    return sched, fired, cfg


def test_time_trigger_fires_at_its_minute_and_only_once_a_day():
    t = cfg_mod.Trigger(name="nightly", when="time", time_of_day="03:00")
    sched, _fired, _cfg = build([t])

    assert sched.due_now(at(20, 2, 59)) == []      # not yet
    assert sched.due_now(at(20, 3, 0)) == [t]
    sched.mark_ran(t, date(2026, 7, 20))
    assert sched.due_now(at(20, 3, 1)) == []       # already ran today
    assert sched.due_now(at(21, 3, 0)) == [t]      # tomorrow again


def test_a_missed_trigger_catches_up_but_not_all_day():
    """A machine asleep at 03:00 should still run the job when it wakes — but a
    trigger from this morning must not fire at six in the evening."""
    t = cfg_mod.Trigger(name="nightly", when="time", time_of_day="03:00")
    sched, _fired, _cfg = build([t])
    assert sched.due_now(at(20, 3, 25)) == [t]     # 25 minutes late: fine
    assert sched.due_now(at(20, 4, 30)) == []      # 90 minutes late: skip


def test_chosen_days_are_respected():
    # 2026-07-20 is a Monday.
    t = cfg_mod.Trigger(name="weekly", when="time", time_of_day="03:00", days=[0])
    sched, _fired, _cfg = build([t])
    assert sched.due_now(at(20, 3, 0)) == [t]      # Monday
    assert sched.due_now(at(21, 3, 0)) == []       # Tuesday
    assert at(21, 3, 0).weekday() == 1


def test_no_days_means_every_day():
    t = cfg_mod.Trigger(name="daily", when="time", time_of_day="03:00")
    sched, _fired, _cfg = build([t])
    assert sched.due_now(at(25, 3, 0)) == [t]
    assert sched.due_now(at(26, 3, 0)) == [t]


def test_disabled_triggers_never_fire():
    t = cfg_mod.Trigger(name="off", when="time", time_of_day="03:00", enabled=False)
    sched, _fired, _cfg = build([t])
    assert sched.due_now(at(20, 3, 0)) == []
    assert sched.due_at_startup() == []


def test_startup_triggers_are_separate_from_time_ones():
    boot = cfg_mod.Trigger(name="boot", when="startup", delay_seconds=0)
    nightly = cfg_mod.Trigger(name="nightly", when="time", time_of_day="03:00")
    sched, fired, _cfg = build([boot, nightly])
    assert sched.due_at_startup() == [boot]
    assert sched.due_now(at(20, 3, 0)) == [nightly]

    sched.run_startup_triggers()
    import time
    time.sleep(0.2)                               # zero-delay timer
    assert fired == [boot]


def test_repeat_fires_on_each_interval_from_the_start_time():
    """"at 03:00, then every 2 hours" — the point of a repeat is that it keeps
    going, so once-a-day bookkeeping must not block it."""
    t = cfg_mod.Trigger(name="every2h", when="time", time_of_day="03:00",
                        repeat_seconds=2 * 3600)
    sched, _fired, _cfg = build([t])

    assert sched.due_now(at(20, 2, 59)) == []
    first = at(20, 3, 0)
    assert sched.due_now(first) == [t]
    sched.mark_ran(t, sched.occurrence_for(t, first))
    assert sched.due_now(at(20, 3, 30)) == []       # same slot, already done
    assert sched.due_now(at(20, 4, 59)) == []       # next slot not reached
    third = at(20, 5, 0)
    assert sched.due_now(third) == [t]              # 03:00 + 2h
    sched.mark_ran(t, sched.occurrence_for(t, third))
    assert sched.due_now(at(20, 7, 0)) == [t]       # and again at 07:00


def test_repeat_summary_reads_naturally():
    t = cfg_mod.Trigger(name="r", when="time", time_of_day="03:00",
                        repeat_seconds=2 * 3600, action="stack", stack="S")
    assert t.when_text() == "every day at 03:00, then every 2h"
    t.repeat_seconds = 90 * 60
    assert "every 90m" in t.when_text()


def test_notification_preference():
    t = cfg_mod.Trigger(name="n", notify="failed")
    assert t.wants_notice("failed") and not t.wants_notice("success")
    t.notify = "both"
    assert t.wants_notice("success") and t.wants_notice("failed")
    assert not t.wants_notice("skipped")
    t.notify = "all"
    assert t.wants_notice("skipped")
    t.notify = "never"
    assert not any(t.wants_notice(o) for o in ("success", "failed", "skipped"))


def test_summaries_read_as_sentences():
    boot = cfg_mod.Trigger(name="b", when="startup", delay_seconds=30,
                           action="stack", stack="SAP B1")
    assert boot.summary() == "when Windows starts, after 30s → run “SAP B1”"

    weekly = cfg_mod.Trigger(name="w", when="time", time_of_day="22:15", days=[0, 4],
                             action="service", service="AppEngine",
                             service_action="restart")
    services = [cfg_mod.Service(name="AppEngine", label="CompuTec AppEngine")]
    assert weekly.summary(services) == ("Mon, Fri at 22:15 → "
                                        "restart CompuTec AppEngine")


def test_triggers_survive_a_config_round_trip(tmp_path):
    path = str(tmp_path / "services.json")
    cfg = cfg_mod.Config(triggers=[
        cfg_mod.Trigger(name="boot", when="startup", delay_seconds=90,
                        action="stack", stack="SAP B1"),
        cfg_mod.Trigger(name="nightly", when="time", time_of_day="02:30",
                        days=[5, 6], action="service", service="AppEngine",
                        service_action="restart", enabled=False)])
    cfg_mod.save(cfg, path)
    back = cfg_mod.load(path)

    boot = back.trigger("boot")
    assert (boot.when, boot.delay_seconds, boot.stack) == ("startup", 90, "SAP B1")
    nightly = back.trigger("nightly")
    assert (nightly.time_of_day, nightly.days, nightly.service_action,
            nightly.enabled) == ("02:30", [5, 6], "restart", False)


def test_nonsense_trigger_values_are_repaired(tmp_path):
    import json
    path = tmp_path / "services.json"
    path.write_text(json.dumps({"triggers": [
        {"name": "odd", "when": "whenever", "time_of_day": "99:99",
         "days": [9, 2, "x"], "action": "explode", "service_action": "melt",
         "delay_seconds": -5}]}), encoding="utf-8")

    t = cfg_mod.load(str(path)).trigger("odd")
    assert (t.when, t.time_of_day, t.days) == ("startup", "03:00", [2])
    assert (t.action, t.service_action, t.delay_seconds) == ("stack", "start", 0)
