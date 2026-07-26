"""Store UTC, show local — and the three places that got it wrong before."""

import json
from datetime import datetime, timedelta, timezone

from core import clock, history
from core import schedule as sched_mod
from core import state as st


def test_what_we_write_down_is_utc(tmp_path):
    path = str(tmp_path / "h.db")
    history.record_action("AppEngine", "restart", st.SRC_PANEL, path=path)

    row = history.read(path=path, limit=1)[0]
    written = datetime.fromisoformat(row["ts"])

    assert written.utcoffset() == timedelta(0), f"not UTC: {row['ts']}"
    assert abs((datetime.now(timezone.utc) - written).total_seconds()) < 90


def test_a_moment_is_shown_on_the_local_clock():
    """08:23Z is 11:23 for a reader in Istanbul, and their clock is the one that
    matters — they were in the room."""
    stored = "2026-07-26T08:23:39+00:00"
    expected = (datetime.fromisoformat(stored).astimezone()
                .strftime("%Y-%m-%d  %H:%M:%S"))

    assert clock.local_text(stored) == expected


def test_rows_written_before_the_change_keep_their_meaning():
    """Older rows carry a local offset. Same instant either way, so a mixed file
    needs no migration — only readers that parse instead of comparing text."""
    old, new = "2026-07-26T11:23:39+03:00", "2026-07-26T08:23:39+00:00"

    assert clock.parse(old) == clock.parse(new)
    assert clock.local_text(old) == clock.local_text(new)


def test_an_offsetless_timestamp_is_read_as_utc():
    assert clock.parse("2026-07-26T08:23:39") == clock.parse("2026-07-26T08:23:39Z")


def test_unreadable_timestamps_do_not_take_a_timeline_down():
    assert clock.parse("not a time") is None
    assert clock.local_text("not a time") == "not a time"
    assert clock.sort_key("not a time") < clock.sort_key("2026-07-26T08:00:00+00:00")


def test_a_mixed_file_is_still_in_time_order(tmp_path):
    """The bug this change exists to prevent: ordering timestamps as text.

    A is 07:00Z; B is 09:00+03:00, which is 06:00Z — so A is the newer of the
    two. As text, though, "09:00…+03:00" sorts above "07:00…+00:00", which is how
    a string sort put the older row first.
    """
    path = str(tmp_path / "h.db")
    history.import_records([
        {"ts": "2026-07-26T09:00:00+03:00", "service": "B", "to": "Running",
         "source": st.SRC_SCM},
        {"ts": "2026-07-26T07:00:00+00:00", "service": "A", "to": "Running",
         "source": st.SRC_SCM},
    ], path=path)

    rows = history.query(service_names=["A", "B"], path=path)

    assert [r["service"] for r in rows] == ["A", "B"]          # newest first
    # And the text order really is the other one, so this test would catch a
    # return to comparing strings.
    stamps = [r["ts"] for r in rows]
    assert stamps != sorted(stamps, reverse=True)


def test_the_exported_time_is_local_and_excel_shaped(tmp_path):
    path = str(tmp_path / "h.db")
    stored = "2026-07-26T08:23:39+00:00"
    history.import_records([{"ts": stored, "service": "A", "to": "Running",
                            "source": st.SRC_SCM}], path=path)
    rows = history.query(service_names=["A"], path=path)
    dest = str(tmp_path / "out.csv")

    history.export_csv(dest, rows=rows)

    cell = open(dest, encoding="utf-8-sig").read().splitlines()[2].split("\t")[0]
    assert cell == datetime.fromisoformat(stored).astimezone().strftime(
        "%Y-%m-%d %H:%M:%S")
    assert "+" not in cell and "T" not in cell        # a date to Excel, not text


def test_a_trigger_that_already_ran_is_not_fired_again_after_a_restart():
    """The trap in storing UTC. A 07:15 trigger is recorded at 04:15Z; if that is
    read as 04:15 local, its time looks not to have come and it fires twice."""
    from core import config as cfg_mod
    trigger = cfg_mod.Trigger(name="Morning start", when="time",
                              time_of_day="07:15", days=[0, 1, 2, 3, 4, 5, 6],
                              action="service", service="AppEngine")
    cfg = cfg_mod.Config(triggers=[trigger])
    ran_at_local = datetime.now().replace(hour=7, minute=15, second=4,
                                          microsecond=0)
    stored = ran_at_local.astimezone().astimezone(timezone.utc).isoformat(
        timespec="seconds")

    scheduler = sched_mod.Scheduler(lambda: cfg, lambda _t: None,
                                   now=lambda: ran_at_local + timedelta(minutes=5))
    seeded = scheduler.seed_from([{"run": "trigger", "name": trigger.name,
                                           "ts": stored, "outcome": "ok"}])

    assert seeded == 1, "the run was not recognised, so the trigger would refire"
    assert scheduler.due_now(now=ran_at_local + timedelta(minutes=5)) == []
