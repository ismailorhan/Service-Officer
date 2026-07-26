"""The timeline has to read as an explanation, not a log dump."""

import json
from datetime import datetime, timedelta, timezone

from core import clock
from core import config as cfg_mod
from core import history
from core import state as st


def write(path, rows):
    """Seed a history with rows that carry their own timestamps.

    The same call the JSONL migration uses, so the suite exercises that path
    rather than reaching past the store into a file format.
    """
    history.import_records(rows, path=path)


def iso(minutes_ago=0):
    return (datetime.now(timezone.utc)
            - timedelta(minutes=minutes_ago)).astimezone().isoformat(timespec="seconds")


def test_actions_and_states_appear_as_one_story(tmp_path):
    p = str(tmp_path / "h.db")
    store = st.Store()
    history.attach(store, lambda: True, path=p)

    history.record_action("AppEngine", "restart", st.SRC_PANEL, path=p)
    store.update("AppEngine", st.RUNNING)
    store.update("AppEngine", st.STOPPED, exit_code=1067)

    rows = history.query(service_names=["AppEngine"], labels=["CompuTec AppEngine"],
                         path=p)
    kinds = [r["kind"] for r in rows]
    assert "action" in kinds and "state" in kinds
    action = next(r for r in rows if r["kind"] == "action")
    assert action["event"] == "restart requested"
    assert action["source"] == "you, from the panel"      # not the raw code
    crash = next(r for r in rows if r["event"] == st.STOPPED)
    assert "exit code 1067" in crash["detail"]
    assert crash["level"] == "Error"
    assert crash["label"] == "CompuTec AppEngine"          # display name, not short


def test_source_codes_are_spelled_out(tmp_path):
    p = str(tmp_path / "h.db")
    write(p, [
        {"ts": iso(1), "service": "A", "to": "Running", "source": st.SRC_SCM},
        {"ts": iso(2), "service": "A", "to": "Stopped", "source": st.SRC_WATCHDOG},
        {"ts": iso(3), "service": "A", "to": "Stopped", "source": st.SRC_STACK},
    ])
    sources = {r["source"] for r in history.query(service_names=["A"], path=p)}
    assert sources == {"observed", "watchdog", "stack run"}


def test_filters_by_service_and_time_range(tmp_path):
    p = str(tmp_path / "h.db")
    write(p, [
        {"ts": iso(5), "service": "A", "to": "Running", "source": st.SRC_SCM},
        {"ts": iso(5), "service": "B", "to": "Running", "source": st.SRC_SCM},
        {"ts": iso(60 * 40), "service": "A", "to": "Stopped", "source": st.SRC_SCM},
    ])
    names = ["A", "B"]
    assert len(history.query(service_names=names, path=p)) == 3
    assert len(history.query(service_names=names, service="A", path=p)) == 2
    recent = history.query(service_names=names, hours=1, path=p)
    assert len(recent) == 2                     # the 40-hour-old row is out


def test_newest_first(tmp_path):
    p = str(tmp_path / "h.db")
    write(p, [
        {"ts": iso(30), "service": "A", "to": "Stopped", "source": st.SRC_SCM},
        {"ts": iso(1), "service": "A", "to": "Running", "source": st.SRC_SCM},
    ])
    rows = history.query(service_names=["A"], path=p)
    assert [r["event"] for r in rows] == ["Running", "Stopped"]


def test_export_matches_what_is_on_screen(tmp_path):
    p = str(tmp_path / "h.db")
    write(p, [
        {"ts": iso(1), "service": "A", "to": "Running", "source": st.SRC_SCM},
        {"ts": iso(2), "service": "B", "to": "Running", "source": st.SRC_SCM},
    ])
    filtered = history.query(service_names=["A", "B"], service="A", path=p)
    dest = str(tmp_path / "out.csv")
    assert history.export_csv(dest, rows=filtered) == 1

    # Tab separated, with Excel's own sep= hint first: Excel takes the delimiter
    # for a .csv from the Windows list separator, so a semicolon file landed in
    # one column. The hint line is honoured in any locale.
    text = open(dest, encoding="utf-8-sig").read().splitlines()
    assert text[0] == "sep=\t"
    # The time column names the zone once, because the times in it are local.
    assert text[1].split("\t") == [f"Time ({clock.offset_label()})", "Service",
                                   "Kind", "Event", "Detail", "Level", "Source"]
    assert "A" in text[2] and "B" not in text[2]


def test_plain_export_stays_parseable_by_other_tools(tmp_path):
    p = str(tmp_path / "h.db")
    write(p, [{"ts": iso(1), "service": "A", "to": "Running", "source": st.SRC_SCM}])
    rows = history.query(service_names=["A"], path=p)
    dest = str(tmp_path / "plain.csv")
    history.export_csv(dest, rows=rows, for_excel=False)
    text = open(dest, encoding="utf-8-sig").read().splitlines()
    assert text[0].split(",")[:2] == [f"Time ({clock.offset_label()})",
                                      "Service"]           # no hint line


def test_windows_events_are_merged_when_asked_for(tmp_path, monkeypatch):
    """The reason a service died usually lives in the Windows log, so it has to
    land in the same timeline rather than a separate screen."""
    from core import eventlog
    p = str(tmp_path / "h.db")
    write(p, [{"ts": iso(2), "service": "AppEngine", "to": "Stopped",
               "exit_code": 1067, "source": st.SRC_SCM}])

    monkeypatch.setattr(eventlog, "read", lambda *a, **k: [{
        "ts": iso(2), "service": "AppEngine", "level": "Error",
        "source": "Service Control Manager", "event_id": 7031,
        "summary": "terminated unexpectedly",
        "message": "The AppEngine service terminated unexpectedly.",
        "log": "System"}])

    plain = history.query(service_names=["AppEngine"], path=p)
    assert all(r["kind"] != "windows" for r in plain)

    merged = history.query(service_names=["AppEngine"], labels=["CompuTec AppEngine"],
                           include_windows=True, path=p)
    win = next(r for r in merged if r["kind"] == "windows")
    assert win["event"] == "terminated unexpectedly"
    assert win["source"].startswith("Windows event log")
    assert win["level"] == "Error"


def test_reading_the_real_event_log_does_not_explode():
    """Against the live log: it must return quickly and never raise, whatever is
    in there."""
    from core import eventlog
    rows = eventlog.read(["Spooler", "AppEngine"], ["Print Spooler"], hours=48,
                         limit=20)
    assert isinstance(rows, list)
    for r in rows:
        assert {"ts", "level", "source", "event_id", "message"} <= set(r)
