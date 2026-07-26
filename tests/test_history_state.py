import json
import pathlib
from datetime import datetime, timedelta, timezone

from core import db, history
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
    p = str(tmp_path / "h.db")
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
    p = str(tmp_path / "h.db")
    store = st.Store()
    enabled = [False]
    history.attach(store, lambda: enabled[0], path=p)
    store.update("A", st.RUNNING)
    assert history.read(p) == []
    enabled[0] = True
    store.update("A", st.STOPPED)
    assert len(history.read(p)) == 1


def test_trim_drops_entries_past_retention(tmp_path):
    p = tmp_path / "h.db"
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    new = datetime.now(timezone.utc).isoformat()
    history.import_records(
        [{"ts": t, "service": "A", "to": "Running"} for t in (old, new)],
        path=str(p))

    dropped = history.trim(30, str(p))
    rows = history.read(str(p))
    assert dropped == 1 and len(rows) == 1 and rows[0]["ts"] == new


def test_trim_does_not_replace_the_file(tmp_path):
    """Retention used to rewrite the whole file, which is how an append from
    another process could be lost. Deleting rows must leave the file in place."""
    p = tmp_path / "h.db"
    history.import_records([{"ts": (datetime.now(timezone.utc)
                                   - timedelta(days=40)).isoformat(),
                            "service": "A", "to": "Running"}], path=str(p))
    before = p.stat().st_ino if hasattr(p.stat(), "st_ino") else None

    assert history.trim(30, str(p)) == 1

    assert p.exists()
    assert not (tmp_path / "h.db.tmp").exists()
    if before:
        assert p.stat().st_ino == before, "the file was replaced, not edited"


def test_filter_by_service_and_csv_export(tmp_path):
    p = str(tmp_path / "h.db")
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


# -- what the store guarantees ----------------------------------------------
def _seed(path, n, kind="state"):
    """n rows with their own timestamps, oldest first."""
    if kind == "run":
        rows = [{"ts": f"2026-07-26T00:{i // 60:02d}:{i % 60:02d}+00:00",
                 "run": "stack", "name": f"run{i}", "outcome": "ok",
                 "seconds": 1} for i in range(n)]
    else:
        rows = [{"ts": f"2026-07-26T00:{i // 60:02d}:{i % 60:02d}+00:00",
                 "service": f"Svc{i % 3}", "to": "Running", "note": str(i)}
                for i in range(n)]
    return history.import_records(rows, path=str(path))


def _work_done(conn, sql: str) -> int:
    """How much SQLite actually did, in virtual-machine steps.

    A measurement rather than a reading of EXPLAIN: the plan for this query says
    "SCAN events", which sounds like the whole table and is not — with a LIMIT it
    walks the rowid tree from the end and stops. Counting the work says so
    without depending on the wording of a plan.
    """
    steps = [0]

    def tick():
        steps[0] += 1
        return 0

    conn.set_progress_handler(tick, 100)
    try:
        conn.execute(sql).fetchall()
    finally:
        conn.set_progress_handler(None, 0)
    return steps[0]


def test_a_query_reads_what_it_shows_not_the_whole_table(tmp_path):
    """The point of a database here: asking for ten rows out of five thousand
    must not touch the other four thousand nine hundred and ninety."""
    path = tmp_path / "h.db"
    assert _seed(path, 5000) == 5000

    rows = history.read(path=str(path), limit=10)

    assert [r["note"] for r in rows] == [str(i) for i in range(4999, 4989, -1)]
    conn = db.connect(str(path))
    ten = _work_done(conn, "SELECT * FROM events ORDER BY id DESC LIMIT 10")
    everything = _work_done(conn, "SELECT * FROM events ORDER BY id DESC")
    assert ten * 20 < everything, f"ten rows cost {ten}, all 5000 cost {everything}"


def test_asking_for_runs_uses_the_index_not_a_scan(tmp_path):
    """A machine with no stacks still has a long history. Finding no runs in it
    must cost nothing."""
    path = tmp_path / "h.db"
    _seed(path, 3000)                       # state changes only, no runs

    assert history.runs(path=str(path), limit=200) == []

    conn = db.connect(str(path))
    plan = " ".join(str(r[-1]) for r in conn.execute(
        "EXPLAIN QUERY PLAN SELECT * FROM events WHERE kind = 'run' "
        "ORDER BY id DESC LIMIT 200"))
    assert "events_kind_id" in plan, plan


def test_runs_returns_the_newest_executions(tmp_path):
    path = tmp_path / "h.db"
    _seed(path, 50, kind="run")
    _seed(path, 50)                         # interleaved state changes

    got = history.runs(path=str(path), limit=5)

    assert [r["name"] for r in got] == ["run49", "run48", "run47", "run46",
                                        "run45"]


def test_a_state_row_that_mentions_a_run_is_not_one(tmp_path):
    path = tmp_path / "h.db"
    history.import_records([
        {"ts": "2026-07-26T00:00:01+00:00", "service": "A", "to": "Running",
         "note": 'the word "run" here'},
        {"ts": "2026-07-26T00:00:02+00:00", "run": "stack", "name": "morning",
         "outcome": "ok", "seconds": 2},
    ], path=str(path))

    assert [r["name"] for r in history.runs(path=str(path))] == ["morning"]


def test_a_missing_store_is_not_an_error(tmp_path):
    assert history.read(path=str(tmp_path / "nope.db")) == []
    assert history.runs(path=str(tmp_path / "nope.db")) == []


def test_a_corrupt_store_is_set_aside_rather_than_losing_the_app(tmp_path):
    """A history that cannot be opened must not stop the app, and must not be
    deleted either — damaged or not, it is the customer's evidence."""
    path = tmp_path / "h.db"
    _seed(path, 5)
    db.close(str(path))
    with open(path, "r+b") as fh:           # scribble over the header
        fh.seek(0)
        fh.write(b"this is not a database at all")

    assert db.integrity(str(path)) != "ok"
    moved = db.set_aside(str(path))

    assert moved and pathlib.Path(moved).exists(), "the damaged file was lost"
    assert not path.exists()
    history.record_action("A", "start", "panel", path=str(path))
    assert len(history.read(path=str(path))) == 1


def test_the_row_ceiling_keeps_the_newest(tmp_path, monkeypatch):
    path = tmp_path / "h.db"
    monkeypatch.setattr(history, "MAX_ROWS", 10)
    _seed(path, 25)

    dropped = history.trim(3650, str(path))     # retention wide open

    rows = history.read(path=str(path), limit=100)
    assert dropped == 15 and len(rows) == 10
    assert rows[0]["note"] == "24", "kept the wrong end of the history"


def test_two_connections_can_read_while_one_writes(tmp_path):
    """WAL, and the reason for it: the agent will write while the panel reads."""
    import sqlite3
    path = tmp_path / "h.db"
    _seed(path, 3)
    reader = sqlite3.connect(str(path))
    try:
        history.record_action("A", "restart", "panel", path=str(path))
        # A second, independent connection sees the committed row without having
        # been blocked by the write.
        count = reader.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        assert count == 4
        assert reader.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        reader.close()
