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


def test_editing_the_time_after_it_ran_lets_it_fire_again_today():
    """The bug this exists for: a trigger that had already run today stayed
    blocked for the rest of the day, so moving it from 12:11 to 15:52 did
    nothing until tomorrow — which reads exactly like a scheduler that doesn't
    work."""
    t = cfg_mod.Trigger(name="app", when="time", time_of_day="12:11")
    sched, _fired, _cfg = build([t])

    assert sched.due_now(at(20, 12, 11)) == [t]
    sched.mark_ran(t, sched.occurrence_for(t, at(20, 12, 11)))
    assert sched.due_now(at(20, 12, 12)) == []      # same schedule, done

    t.time_of_day = "15:52"                        # the user moves it
    assert sched.due_now(at(20, 15, 51)) == []      # not yet
    assert sched.due_now(at(20, 15, 52)) == [t]     # fires, same day


def test_editing_days_or_repeat_also_clears_the_memory():
    t = cfg_mod.Trigger(name="app", when="time", time_of_day="03:00")
    sched, _fired, _cfg = build([t])
    sched.mark_ran(t, sched.occurrence_for(t, at(20, 3, 0)))
    assert sched.due_now(at(20, 3, 5)) == []

    t.days = [0]                                   # 2026-07-20 is a Monday
    assert sched.due_now(at(20, 3, 5)) == [t]
    sched.mark_ran(t, sched.occurrence_for(t, at(20, 3, 5)))
    assert sched.due_now(at(20, 3, 6)) == []

    t.repeat_seconds = 3600
    assert sched.due_now(at(20, 3, 6)) == [t]


def test_a_restart_does_not_repeat_what_already_ran(tmp_path):
    """Measured on the real app: a trigger set for 16:12 fired again at 16:31
    because it was restarted at 16:29 and the memory lived only in the process.
    Inside the catch-up window that means every restart re-runs the action."""
    t = cfg_mod.Trigger(name="app", when="time", time_of_day="16:12")
    sched, _fired, _cfg = build([t], now=at(20, 16, 31))
    assert sched.due_now() == [t]                   # a fresh process, no memory

    sched.seed_from([{"run": "trigger", "name": "app",
                      "ts": "2026-07-20T16:12:04+03:00", "outcome": "success"}])
    assert sched.due_now() == []                    # it is on disk, so not again
    assert sched.due_now(at(21, 16, 12)) == [t]     # tomorrow still fires


def test_seeding_ignores_runs_that_do_not_match_the_schedule():
    t = cfg_mod.Trigger(name="app", when="time", time_of_day="22:00")
    sched, _fired, _cfg = build([t], now=at(20, 22, 5))

    # A "Run now" at nine in the morning is not the 22:00 occurrence.
    assert sched.seed_from([{"run": "trigger", "name": "app",
                             "ts": "2026-07-20T09:00:00+03:00"}]) == 0
    assert sched.due_now() == [t]

    # Neither is a run recorded against a name we no longer have, nor a
    # timestamp we can't read.
    assert sched.seed_from([{"run": "trigger", "name": "gone",
                             "ts": "2026-07-20T22:00:00+03:00"}]) == 0
    assert sched.seed_from([{"run": "trigger", "name": "app", "ts": "nonsense"}]) == 0

    # An edit since that run leaves the trigger free to fire.
    sched.seed_from([{"run": "trigger", "name": "app",
                      "ts": "2026-07-20T22:00:03+03:00"}])
    assert sched.due_now() == []
    t.time_of_day = "22:04"
    assert sched.due_now() == [t]


def test_next_run_says_when_so_the_user_need_not_guess():
    t = cfg_mod.Trigger(name="app", when="time", time_of_day="15:52")
    sched, _fired, _cfg = build([t], now=at(20, 15, 0))
    assert sched.next_run_at(t) == at(20, 15, 52)

    # Past today's time: tomorrow.
    assert sched.next_run_at(t, at(20, 16, 0)) == at(21, 15, 52)

    # Restricted to Fridays, asked on a Monday: 2026-07-24.
    t.days = [4]
    assert sched.next_run_at(t, at(20, 15, 0)) == at(24, 15, 52)

    # Nothing to promise for the other kinds.
    assert sched.next_run_at(cfg_mod.Trigger(name="b", when="startup")) is None
    assert sched.next_run_at(cfg_mod.Trigger(name="o", when="time",
                                             enabled=False)) is None


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
