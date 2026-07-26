import json
from datetime import datetime, timedelta, timezone

from core import history
from core import state as st


# ── state / event bus ──────────────────────────────────────────────────────
def test_only_real_changes_are_published():
    store = st.Store()
    seen = []
    store.subscribe(seen.append)
    store.update("A", st.RUNNING)
    store.update("A", st.RUNNING)        # same status again
    store.update("A", st.STOPPED)
    assert [e.status for e in seen] == [st.RUNNING, st.STOPPED]
    assert seen[1].previous == st.RUNNING


def test_crash_is_distinguished_from_clean_stop():
    store = st.Store()
    store.update("A", st.RUNNING)
    assert store.update("A", st.STOPPED, exit_code=1067).crashed is True
    store.update("A", st.RUNNING)
    assert store.update("A", st.STOPPED, exit_code=0).crashed is False


def test_a_broken_subscriber_cannot_break_the_notifier():
    store = st.Store()
    ok = []
    store.subscribe(lambda e: (_ for _ in ()).throw(RuntimeError("boom")))
    store.subscribe(ok.append)
    store.update("A", st.RUNNING)
    assert len(ok) == 1


def test_counts_and_pending():
    store = st.Store()
    store.update("A", st.RUNNING)
    store.update("B", st.STOPPED)
    store.update("C", "Starting")
    assert store.counts() == (1, 3)
    assert store.any_pending() is True


def test_keep_only_drops_unconfigured_services():
    store = st.Store()
    store.update("A", st.RUNNING)
    store.update("B", st.RUNNING)
    store.keep_only([("", "A")])
    assert store.status_of("A") == st.RUNNING
    assert store.status_of("B") == st.UNKNOWN


# ── history ────────────────────────────────────────────────────────────────
def test_records_events_with_source_and_exit_code(tmp_path):
    p = str(tmp_path / "h.jsonl")
    store = st.Store()
    history.attach(store, lambda: True, path=p)
    store.update("AppEngine", st.RUNNING)
    store.update("AppEngine", st.STOPPED, exit_code=1067)

    rows = history.read(p)
    assert [r["to"] for r in rows] == [st.STOPPED, st.RUNNING]   # newest first
    assert rows[0]["exit_code"] == 1067
    assert rows[0]["from"] == st.RUNNING
    assert rows[0]["source"] == st.SRC_SCM


def test_recording_can_be_switched_off(tmp_path):
    p = str(tmp_path / "h.jsonl")
    store = st.Store()
    enabled = [False]
    history.attach(store, lambda: enabled[0], path=p)
    store.update("A", st.RUNNING)
    assert history.read(p) == []
    enabled[0] = True
    store.update("A", st.STOPPED)
    assert len(history.read(p)) == 1


def test_trim_drops_entries_past_retention(tmp_path):
    p = tmp_path / "h.jsonl"
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    new = datetime.now(timezone.utc).isoformat()
    p.write_text("\n".join(json.dumps({"ts": t, "service": "A", "to": "Running"})
                           for t in (old, new)) + "\n", encoding="utf-8")

    dropped = history.trim(30, str(p))
    rows = history.read(str(p))
    assert dropped == 1 and len(rows) == 1 and rows[0]["ts"] == new


def test_malformed_lines_are_skipped_not_fatal(tmp_path):
    p = tmp_path / "h.jsonl"
    p.write_text('{"ts": "x", broken\n' +
                 json.dumps({"ts": datetime.now(timezone.utc).isoformat(),
                             "service": "A", "to": "Running"}) + "\n",
                 encoding="utf-8")
    assert len(history.read(str(p))) == 1


def test_filter_by_service_and_csv_export(tmp_path):
    p = str(tmp_path / "h.jsonl")
    store = st.Store()
    history.attach(store, lambda: True, path=p)
    store.update("A", st.RUNNING)
    store.update("B", st.RUNNING)

    assert [r["service"] for r in history.read(p, service="B")] == ["B"]

    dest = str(tmp_path / "out.csv")
    assert history.export_csv(dest, path=p) == 2
    lines = open(dest, encoding="utf-8-sig").read().splitlines()
    assert lines[0] == "sep=\t"                  # so Excel splits the columns
    assert "Service" in lines[1] and any("A" in line for line in lines[2:])


# -- reading from the end ---------------------------------------------------
def _write_rows(path, n, start=0):
    with open(path, "w", encoding="utf-8") as fh:
        for i in range(start, start + n):
            fh.write(json.dumps({"ts": f"2026-07-26T00:00:{i % 60:02d}",
                                 "service": f"Svc{i % 3}", "to": "Running",
                                 "seq": i}) + "\n")


def test_reading_stops_once_it_has_enough(tmp_path, monkeypatch):
    """A query must cost what it shows, not what the file has ever held."""
    path = tmp_path / "h.jsonl"
    _write_rows(path, 5000)
    seen = []
    real = history._parse
    monkeypatch.setattr(history, "_parse",
                        lambda raw: (seen.append(1), real(raw))[1])

    rows = history.read(path=str(path), limit=10)

    assert [r["seq"] for r in rows] == list(range(4999, 4989, -1))
    # 5,000 rows in the file; a limit of 10 must not have parsed them all.
    assert len(seen) < 500, f"parsed {len(seen)} lines to return 10"


def test_a_record_split_across_two_chunks_survives(tmp_path, monkeypatch):
    """The reverse reader joins fragments, so a tiny chunk must change nothing."""
    path = tmp_path / "h.jsonl"
    _write_rows(path, 400)
    monkeypatch.setattr(history, "_CHUNK", 64)      # forces mid-record splits

    rows = history.read(path=str(path), limit=400)

    assert len(rows) == 400
    assert [r["seq"] for r in rows] == list(range(399, -1, -1))


def test_no_trailing_newline_still_reads_the_last_row(tmp_path):
    path = tmp_path / "h.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": "2026-07-26T00:00:01", "service": "A",
                             "to": "Running"}) + "\n")
        fh.write(json.dumps({"ts": "2026-07-26T00:00:02", "service": "B",
                             "to": "Running"}))       # no newline
    rows = history.read(path=str(path), limit=10)
    assert [r["service"] for r in rows] == ["B", "A"]


def test_missing_and_empty_files_are_not_errors(tmp_path):
    assert history.read(path=str(tmp_path / "nope.jsonl")) == []
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    assert history.read(path=str(empty)) == []


def test_runs_returns_the_newest_executions(tmp_path):
    path = tmp_path / "h.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        for i in range(50):
            fh.write(json.dumps({"ts": f"2026-07-26T00:{i:02d}:00",
                                 "run": "stack", "name": f"run{i}",
                                 "outcome": "ok", "seconds": 1}) + "\n")
            fh.write(json.dumps({"ts": f"2026-07-26T00:{i:02d}:30",
                                 "service": "Svc", "to": "Running"}) + "\n")

    got = history.runs(path=str(path), limit=5)

    assert [r["name"] for r in got] == ["run49", "run48", "run47", "run46",
                                        "run45"]


def test_looking_for_runs_does_not_parse_every_state_change(tmp_path,
                                                            monkeypatch):
    """A machine with no stacks still has a large history; asking for runs must
    not pay for it."""
    path = tmp_path / "h.jsonl"
    _write_rows(path, 3000)                      # state changes only, no runs
    parsed = []
    real = history._parse
    monkeypatch.setattr(history, "_parse",
                        lambda raw: (parsed.append(1), real(raw))[1])

    assert history.runs(path=str(path), limit=200) == []
    assert not parsed, "parsed lines that could not possibly be runs"


def test_the_prefilter_never_hides_a_real_run(tmp_path):
    """A state row mentioning "run" in its text is skipped by the caller, not by
    the prefilter, and a real run is still found behind it."""
    path = tmp_path / "h.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": "2026-07-26T00:00:01", "service": "A",
                             "to": "Running", "note": 'the word "run" here'})
                 + "\n")
        fh.write(json.dumps({"ts": "2026-07-26T00:00:02", "run": "stack",
                             "name": "morning", "outcome": "ok",
                             "seconds": 2}) + "\n")

    got = history.runs(path=str(path), limit=10)

    assert [r["name"] for r in got] == ["morning"]
