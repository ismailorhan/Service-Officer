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
    text = open(dest, encoding="utf-8-sig").read()
    assert "Service" in text.splitlines()[0] and "A" in text
