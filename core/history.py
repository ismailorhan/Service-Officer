"""State-change history: what happened, and what caused it.

One immutable row per change, with where the change came from, so a timeline
reads as a story — crashed → watchdog attempt → running again. That is what gets
pasted into a customer's ticket, and it is the only way to notice a service that
keeps dying quietly.

The rows live in SQLite (`core/db.py` says why, and it is not "row counts"). This
module keeps the shape the rest of the app talks in: dictionaries that look the
way they did when this was a JSON Lines file, so nothing outside had to learn
about columns. Timestamps are UTC — see `core/clock.py`.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone

from . import clock
from . import config as cfg_mod
from . import db
from . import state as st

HISTORY_PATH = os.path.join(cfg_mod.APP_DIR, "history.db")
#: What this used to be. Imported once, then set aside — see migrate_jsonl().
LEGACY_JSONL = os.path.join(cfg_mod.APP_DIR, "history.jsonl")
#: A hard ceiling, so a chatty server cannot fill a disk between retention runs.
#: Rows, not bytes, because rows are what a retention policy is about.
MAX_ROWS = 250_000

#: Why the last write failed, if one did. Losing history must not take the app
#: down — but it must not be invisible either. An empty timeline that turns out to
#: be a permissions problem is worse than an error, because it reads as "nothing
#: happened" for exactly as long as nobody checks.
_last_error = ""
_reported = False


def last_error() -> str:
    """Why the last read or write failed, if one did.

    Only this module's own last outcome — not whatever `db` remembers about the
    default path. Mixing the two meant a successful write to one store could not
    clear an error left by another, so the panel would keep showing a stale
    complaint about a file nobody was using.
    """
    return _last_error


def _write_failed(where: str, exc: Exception) -> None:
    global _last_error, _reported
    _last_error = f"{where}: {getattr(exc, 'strerror', None) or exc}"
    if not _reported:                      # once, or a broken disk floods the log
        _reported = True
        from . import applog
        applog.get("history").warning("cannot write history — %s", _last_error)


def _read_failed(exc: Exception) -> None:
    """A failed read is reported the same way, because an empty History page and
    a broken database look identical on screen otherwise."""
    global _last_error
    _last_error = f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# Between a record and a row
# ---------------------------------------------------------------------------
#: Which key in a record says what kind of row it is. Order matters: a run has no
#: service, a health row has no action.
_KINDS = (("run", "run"), ("health", "health"), ("action", "action"))


def _column(row, name: str):
    """A column's value, or None if this file is older than the column.

    Rows read from a database written by an older build simply do not have it, and
    the app has to keep working against one — a downgrade must not crash the page.
    """
    try:
        return row[name]
    except (IndexError, KeyError):
        return None


def _to_row(rec: dict) -> tuple:
    """A record dict as the columns of `events`, in db.COLUMNS order."""
    kind = next((k for key, k in _KINDS if rec.get(key)), "state")
    extra = json.dumps(rec["extra"]) if rec.get("extra") else None
    if kind == "run":
        # A run's name goes in `service`: it is what the timeline shows in that
        # column, and it keeps one index serving both.
        return (rec.get("ts"), "", rec.get("name", ""), "run", rec.get("run"),
                None, None, rec.get("source"), rec.get("outcome"), None,
                rec.get("seconds"), rec.get("detail"), extra,
                rec.get("actor", ""))
    event = rec.get("action") if kind == "action" else None
    state = rec.get("health") if kind == "health" else rec.get("to")
    detail = rec.get("detail") if kind == "health" else rec.get("note")
    return (rec.get("ts"), rec.get("machine", "") or "", rec.get("service", ""),
            kind, event, state, rec.get("from"), rec.get("source"), None,
            rec.get("exit_code"), None, detail, extra, rec.get("actor", ""))


def _to_record(row) -> dict:
    """A row as the record dict the rest of the app reads.

    Deliberately the old JSON Lines shape, keys omitted when empty exactly as
    they were: `query()` decides what a row *is* by which keys are present, so an
    always-present "action": None would quietly turn every state change into an
    action.
    """
    kind = row["kind"]
    out = {"ts": row["ts"], "source": row["source"]}
    # Omitted when empty, like every other key here: the History page shows the
    # column only when something in view fills it, and an always-present ""
    # would make every watchdog restart look like a record we lost.
    if _column(row, "actor"):
        out["actor"] = row["actor"]
    if kind == "run":
        out.update({"run": row["event"], "name": row["service"],
                    "outcome": row["outcome"],
                    "seconds": row["seconds"] or 0,
                    "detail": row["detail"] or ""})
        return out
    out["service"] = row["service"]
    out["machine"] = row["machine"] or ""
    if kind == "health":
        out["health"] = row["state"]
        out["detail"] = row["detail"] or ""
    elif kind == "action":
        out["action"] = row["event"]
        if row["detail"]:
            out["note"] = row["detail"]
    else:
        out["from"] = row["from_state"]
        out["to"] = row["state"]
        if row["exit_code"]:
            out["exit_code"] = row["exit_code"]
        if row["detail"]:
            out["note"] = row["detail"]
    if row["extra"]:
        try:
            out["extra"] = json.loads(row["extra"])
        except json.JSONDecodeError:
            pass
    return out


def _append(line: dict, path: str) -> None:
    """One place that writes, so one place reports when it can't."""
    global _last_error
    conn = db.connect(path)
    if conn is None:
        _write_failed(path, RuntimeError(db.last_error(path) or "cannot open"))
        return
    try:
        placeholders = ", ".join("?" * len(db.COLUMNS))
        with db._lock, conn:
            conn.execute(f"INSERT INTO events ({', '.join(db.COLUMNS)}) "
                         f"VALUES ({placeholders})", _to_row(line))
        _last_error = ""
    except sqlite3.Error as exc:
        _write_failed(path, exc)


def import_records(records, path: str = None) -> int:
    """Insert records that already have their own timestamps.

    The one way rows enter the store without being generated now: the JSONL
    migration below, and tests that need a history at particular times. Sharing
    it means the migration path is exercised by the suite rather than only by a
    customer's first upgrade.
    """
    conn = db.connect(path or HISTORY_PATH)
    if conn is None:
        return 0
    rows = [_to_row(rec) for rec in records if rec.get("ts")]
    if not rows:
        return 0
    placeholders = ", ".join("?" * len(db.COLUMNS))
    try:
        with db._lock, conn:
            conn.executemany(f"INSERT INTO events ({', '.join(db.COLUMNS)}) "
                             f"VALUES ({placeholders})", rows)
    except sqlite3.Error as exc:
        _write_failed(path or HISTORY_PATH, exc)
        return 0
    return len(rows)


def migrate_jsonl(path: str = None, source: str = None) -> int:
    """Bring an older install's history into the database, once.

    Only into an empty table, so a second run cannot double every row, and the
    source is renamed rather than deleted — it is the customer's evidence, and if
    this went wrong they should still have it.
    """
    path = path or HISTORY_PATH
    source = source or LEGACY_JSONL
    if not os.path.exists(source):
        return 0
    conn = db.connect(path)
    if conn is None:
        return 0
    with db._lock:
        if conn.execute("SELECT 1 FROM events LIMIT 1").fetchone():
            return 0
    records = []
    try:
        with open(source, "r", encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    records.append(json.loads(raw))
                except json.JSONDecodeError:
                    continue          # one torn line is not worth the rest
    except OSError as exc:
        _write_failed(source, exc)
        return 0
    brought = import_records(records, path=path)
    if brought:
        try:
            os.replace(source, source + ".migrated")
        except OSError:
            pass                      # imported is what matters; the rename isn't
    return brought


def path() -> str:
    """Where the log lives, for the UI to show. Evidence you can't find is no
    use when someone asks what happened at three in the morning."""
    return HISTORY_PATH


def _now_iso() -> str:
    """UTC — see core/clock.py for why, and what happens to older rows."""
    return clock.now_iso()


def record(event: st.Event, path: str = None, note: str = "") -> None:
    """Append one event. Never raises — losing history must not break the app."""
    path = path or HISTORY_PATH
    line = {
        "ts": _now_iso(),
        "service": event.name,
        "machine": event.state.machine,
        "from": event.previous,
        "to": event.status,
        "source": event.source,
    }
    if event.state.exit_code:
        line["exit_code"] = event.state.exit_code
    if note:
        line["note"] = note
    _append(line, path)


def record_health(service: str, verdict: str, detail: str = "",
                  machine: str = "", path: str = None) -> None:
    """Log a change between answering and not answering.

    Its own kind of row, because it is neither a state change the SCM reported
    nor something we asked for — and it is the row that explains why a service
    that never left Running still stopped working.
    """
    path = path or HISTORY_PATH
    line = {"ts": _now_iso(), "service": service, "machine": machine,
            "health": verdict, "detail": detail, "source": st.SRC_HEALTH}
    _append(line, path)


def record_action(service: str, action: str, source: str, machine: str = "",
                  note: str = "", actor: str = "", path: str = None) -> None:
    """Log that an action was *asked for*, separately from the state changes it
    causes. Without this the timeline shows a service stopping with no hint that
    somebody pressed the button.

    `actor` is who asked, when a person did: `source` says the panel, `actor` says
    which panel. Left empty for the watchdog and the scheduler, which have nobody
    behind them and would be libelled by a name.
    """
    path = path or HISTORY_PATH
    line = {"ts": _now_iso(), "service": service, "machine": machine,
            "action": action, "source": source}
    if note:
        line["note"] = note
    if actor:
        line["actor"] = actor
    _append(line, path)


def record_run(kind: str, name: str, outcome: str, seconds: float = 0.0,
               detail: str = "", source: str = "", actor: str = "",
               path: str = None) -> None:
    """A whole run: a stack, or a trigger firing. Outcome is success / failed /
    skipped / cancelled. Kept in the same file as everything else so one export
    tells the entire story."""
    path = path or HISTORY_PATH
    line = {"ts": _now_iso(), "run": kind, "name": name, "outcome": outcome,
            "seconds": round(float(seconds), 1), "detail": detail,
            "source": source or kind}
    if actor:
        line["actor"] = actor
    _append(line, path)


def runs(path: str = None, limit: int = 200, kind: str = None,
         name: str = None) -> list:
    """Recent executions, newest first — what the Schedule page lists.

    A machine with no stacks has no run rows at all, and the index means asking
    costs nothing rather than reading everything to find nothing.
    """
    where = ["kind = 'run'"]
    args: list = []
    if kind:
        where.append("event = ?")
        args.append(kind)
    if name:
        where.append("service = ?")
        args.append(name)
    return _select(where, args, limit, path)


def read(path: str = None, limit: int = 500, service: str = None) -> list:
    """Most recent first."""
    where, args = [], []
    if service:
        where.append("service = ?")
        args.append(service)
    return _select(where, args, limit, path)


def _select(where: list, args: list, limit: int, path: str = None) -> list:
    """Newest rows first, as the record dicts the rest of the app expects.

    Ordered by `id`, not by `ts`: id is the order things were written, which is
    what "newest" means, and it needs no index of its own.
    """
    conn = db.connect(path or HISTORY_PATH)
    if conn is None:
        return []
    sql = f"SELECT * FROM events{_clause(where)} ORDER BY id DESC LIMIT ?"
    try:
        with db._lock:
            rows = conn.execute(sql, (*args, int(limit))).fetchall()
    except sqlite3.Error as exc:
        _read_failed(exc)
        return []
    return [_to_record(row) for row in rows]


def _clause(where: list) -> str:
    return f" WHERE {' AND '.join(where)}" if where else ""


def trim(retention_days: int, path: str = None) -> int:
    """Drop rows past the retention window, and hard-cap how many are kept.
    Returns how many were removed.

    Two DELETEs, where this used to rewrite the whole file through a temporary
    copy. That rewrite is exactly what could not survive a second process: an
    append landing mid-trim went into the file being replaced. Deleting rows never
    moves the file, so a writer and a trimmer can be different programs.
    """
    path = path or HISTORY_PATH
    conn = db.connect(path)
    if conn is None:
        return 0
    cutoff = (datetime.now(timezone.utc)
              - timedelta(days=max(1, retention_days))).isoformat(timespec="seconds")
    removed = 0
    try:
        with db._lock, conn:
            removed += conn.execute("DELETE FROM events WHERE ts < ?",
                                    (cutoff,)).rowcount
            # The ceiling, newest kept. One id lookup and a range delete, rather
            # than counting rows.
            edge = conn.execute("SELECT id FROM events ORDER BY id DESC "
                                "LIMIT 1 OFFSET ?", (MAX_ROWS,)).fetchone()
            if edge is not None:
                removed += conn.execute("DELETE FROM events WHERE id <= ?",
                                        (edge["id"],)).rowcount
        if removed:
            # Hand the freed pages back a chunk at a time. A full VACUUM would
            # rewrite the file — the very thing this change exists to avoid.
            with db._lock:
                conn.execute("PRAGMA incremental_vacuum")
    except sqlite3.Error as exc:
        _read_failed(exc)
        return 0
    return max(0, removed)


# ---------------------------------------------------------------------------
# Unified timeline
# ---------------------------------------------------------------------------
#: our own source codes, written to the file, spelled out for people
SOURCE_TEXT = {
    st.SRC_SCM: "observed",
    st.SRC_PANEL: "you, from the panel",
    st.SRC_WATCHDOG: "watchdog",
    st.SRC_STACK: "stack run",
    st.SRC_SCHEDULE: "scheduled trigger",
    st.SRC_HEALTH: "health check",
}

ACTION_TEXT = {"start": "start requested", "stop": "stop requested",
               "restart": "restart requested", "kill": "process killed",
               "run stack": "stack run requested"}


#: Transitional states. Every one is written to the log, but a restart producing
#: "Stopping", "Stopped", "Starting", "Running" is four rows saying one thing, so
#: the halfway states are held back unless full detail is asked for.
PENDING_STATES = ("Starting", "Stopping", "Resuming", "Pausing")


def query(service_names=None, labels=None, service: str = None, hours: int = None,
          include_windows: bool = False, windows_levels=None, limit: int = 800,
          path: str = None, full: bool = False, local_services=None) -> list:
    """One timeline, newest first, in a shape a table can render directly.

    Merges three kinds of row: what we asked for (action), what the SCM told us
    (state), and what Windows logged about the service (windows) — so "it
    stopped" sits next to "terminated unexpectedly, .NET exception".

    full=False leaves out the halfway states, so a restart reads as "restart
    requested" then "Running" instead of four rows. Nothing is dropped from the
    store — this only decides what is worth looking at.
    """
    where: list = []
    args: list = []
    if hours:
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        where.append("ts >= ?")
        args.append(since.isoformat(timespec="seconds"))
    if service:
        # A run is about a stack or a trigger, not about one service, so filtering
        # by service excludes them rather than showing unrelated runs.
        where.append("service = ? AND kind != 'run'")
        args.append(service)
    if not full:
        # The halfway states, left out in SQL rather than after the fact, so the
        # limit counts rows that will actually be shown.
        marks = ", ".join("?" * len(PENDING_STATES))
        where.append(f"NOT (kind = 'state' AND state IN ({marks})"
                     f" AND exit_code IS NULL)")
        args.extend(PENDING_STATES)

    label_of = dict(zip(service_names or [], labels or []))
    rows = []

    # Newest first, limited in the query. Taking the newest `limit` from each
    # source and then merging still gives the newest `limit` overall, which is why
    # the Windows event log can be limited separately below.
    for rec in _select(where, args, limit, path):
        name = rec.get("service", "")
        # `actor` in the common part rather than per-branch: a state change caused
        # by somebody's restart carries it too, and that is exactly the row a person
        # reading the timeline is trying to attribute.
        common = {"ts": rec.get("ts", ""), "service": name,
                  "label": label_of.get(name, name), "level": "",
                  "actor": rec.get("actor", "")}
        if rec.get("run"):
            # A whole run — shown in the timeline too, so a stack's outcome sits
            # among the state changes it caused. (A service filter has already
            # excluded these in SQL.)
            level = {"failed": "Error", "skipped": "Warning"}.get(rec["outcome"], "")
            rows.append({**common, "kind": "run", "service": rec.get("name", ""),
                         "label": rec.get("name", ""),
                         "event": f"{rec['run']} {rec['outcome']}",
                         "detail": " · ".join(x for x in (
                             rec.get("detail", ""),
                             f"{rec.get('seconds', 0)}s" if rec.get("seconds") else "")
                             if x),
                         "level": level,
                         "source": SOURCE_TEXT.get(rec.get("source", ""),
                                                   rec.get("source", ""))})
        elif rec.get("health"):
            verdict = rec["health"]
            rows.append({**common, "kind": "health",
                         "event": ("not responding" if verdict == "unhealthy"
                                   else "responding again" if verdict == "healthy"
                                   else verdict),
                         "detail": rec.get("detail", ""),
                         "level": "Error" if verdict == "unhealthy" else "",
                         "source": SOURCE_TEXT.get(rec.get("source", ""),
                                                   rec.get("source", ""))})
        elif rec.get("action"):
            rows.append({**common, "kind": "action",
                         "event": ACTION_TEXT.get(rec["action"], rec["action"]),
                         "detail": rec.get("note", ""),
                         "source": SOURCE_TEXT.get(rec.get("source", ""),
                                                   rec.get("source", ""))})
        else:
            to = rec.get("to", "")      # the halfway states are excluded in SQL
            detail = []
            if rec.get("from"):
                detail.append(f"was {rec['from']}")
            if rec.get("exit_code"):
                detail.append(f"exit code {rec['exit_code']}")
            if rec.get("note"):
                detail.append(rec["note"])
            level = "Error" if rec.get("exit_code") else ""
            rows.append({**common, "kind": "state", "event": to,
                         "detail": " · ".join(detail), "level": level,
                         "source": SOURCE_TEXT.get(rec.get("source", ""),
                                                   rec.get("source", ""))})

    # The Windows event log is *this* computer's — eventlog.read opens it with
    # OpenEventLog(None, …) and there is no machine to pass. So it may only be read
    # for services that live here. Merging it into another machine's timeline
    # attributes this computer's events to that machine, which is the same silent lie
    # as a File check measuring the wrong disk.
    #
    # `local_services` is required rather than defaulting to all of them: a caller
    # that has not said which are local gets no event-log rows, because defaulting
    # the other way round is how this got in.
    here = set(local_services or ())
    wanted, wanted_labels = [], []
    if include_windows and service_names and here:
        wanted = [n for n in ([service] if service else list(service_names))
                  if n in here]
        wanted_labels = ([label_of.get(service, service)] if service
                         else list(labels or []))
    if wanted:
        from . import eventlog
        for rec in eventlog.read(wanted, wanted_labels, hours=hours or 168,
                                 levels=windows_levels, limit=400):
            name = rec.get("service") or ""
            rows.append({
                "ts": rec["ts"], "service": name,
                "label": label_of.get(name, name), "kind": "windows",
                "event": rec["summary"] or f"event {rec['event_id']}",
                "detail": rec["message"], "level": rec["level"],
                "source": f"Windows event log · {rec['source']}",
            })

    # By the moment, not by the string. Our rows and the Windows event log's are
    # both UTC now, but a file written before this change carries local offsets,
    # and text order across two different offsets is not time order.
    rows.sort(key=lambda r: clock.sort_key(r["ts"]), reverse=True)
    return rows[:limit]


def export_csv(dest: str, rows=None, path: str = None, service: str = None,
               for_excel: bool = True) -> int:
    """Write a timeline out for a ticket. Pass the rows you are looking at so the
    file matches the filters on screen; otherwise everything is exported.

    Excel doesn't read the delimiter from the file — for a .csv it uses whatever
    the Windows list separator happens to be, which is why a semicolon file
    arrived as one fat column. Two things fix that for good: tabs, and Excel's own
    `sep=` first line, which it honours in any locale. Pass for_excel=False for a
    plain comma file that other tools will parse.

    Times go out in *local* time, formatted so Excel reads the cell as a date
    rather than text. The offset is named once in the column header instead of on
    every row: this is evidence for a ticket, read by someone who was there, and
    a thousand cells each ending in "+03:00" would only be in the way.
    """
    import csv
    if rows is None:
        rows = query(service=service, path=path)
    delimiter = "\t" if for_excel else ","
    # utf-8-sig: without the BOM, Excel reads a Turkish name as mojibake.
    with open(dest, "w", encoding="utf-8-sig", newline="") as f:
        if for_excel:
            f.write(f"sep={delimiter}\n")
        w = csv.writer(f, delimiter=delimiter)
        # "Asked by" always in the file even when every row is blank: a CSV goes
        # into a ticket, and a column that comes and goes breaks whatever reads it.
        w.writerow([f"Time ({clock.offset_label()})", "Service", "Kind", "Event",
                    "Detail", "Level", "Source", "Asked by"])
        for r in rows:
            w.writerow([clock.local_text(r.get("ts", ""), clock.EXPORT),
                        r.get("label") or r.get("service", ""),
                        r.get("kind", ""), r.get("event", ""), r.get("detail", ""),
                        r.get("level", ""), r.get("source", ""),
                        r.get("actor", "")])
    return len(rows)


def attach(store: st.Store, enabled_getter, path: str = None) -> None:
    """Subscribe to a store so every change is written while enabled."""
    def on_event(event: st.Event):
        if enabled_getter():
            record(event, path=path)
    store.subscribe(on_event)
