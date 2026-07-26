"""State-change history.

Append-only JSON Lines next to the config. One line per change, with where the
change came from, so a timeline reads as a story: crashed → watchdog attempt →
running again. That is what gets pasted into a customer's ticket, and it is the
only way to notice a service that keeps dying quietly.

JSON Lines rather than a database: appending is a single write with no schema to
migrate, a half-written last line costs one event, and the file can be read with
any text editor on a server where nothing is installed.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone

from . import clock
from . import config as cfg_mod
from . import state as st

HISTORY_PATH = os.path.join(cfg_mod.APP_DIR, "history.jsonl")
MAX_BYTES = 5 * 1024 * 1024      # keep it well-behaved on a customer server

_lock = threading.Lock()

#: Why the last write failed, if one did. Losing history must not take the app
#: down — but it must not be invisible either. An empty timeline that turns out to
#: be a permissions problem is worse than an error, because it reads as "nothing
#: happened" for exactly as long as nobody checks.
_last_error = ""
_reported = False


def last_error() -> str:
    return _last_error


def _write_failed(where: str, exc: Exception) -> None:
    global _last_error, _reported
    _last_error = f"{where}: {getattr(exc, 'strerror', None) or exc}"
    if not _reported:                      # once, or a broken disk floods the log
        _reported = True
        from . import applog
        applog.get("history").warning("cannot write history — %s", _last_error)


def _append(line: dict, path: str) -> None:
    """One place that writes, so one place reports when it can't."""
    global _last_error
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with _lock, open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
        _last_error = ""
    except OSError as exc:
        _write_failed(path, exc)


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
                  note: str = "", path: str = None) -> None:
    """Log that an action was *asked for*, separately from the state changes it
    causes. Without this the timeline shows a service stopping with no hint that
    somebody pressed the button."""
    path = path or HISTORY_PATH
    line = {"ts": _now_iso(), "service": service, "machine": machine,
            "action": action, "source": source}
    if note:
        line["note"] = note
    _append(line, path)


def record_run(kind: str, name: str, outcome: str, seconds: float = 0.0,
               detail: str = "", source: str = "", path: str = None) -> None:
    """A whole run: a stack, or a trigger firing. Outcome is success / failed /
    skipped / cancelled. Kept in the same file as everything else so one export
    tells the entire story."""
    path = path or HISTORY_PATH
    line = {"ts": _now_iso(), "run": kind, "name": name, "outcome": outcome,
            "seconds": round(float(seconds), 1), "detail": detail,
            "source": source or kind}
    _append(line, path)


def runs(path: str = None, limit: int = 200, kind: str = None,
         name: str = None) -> list:
    """Recent executions, newest first — what the Schedule page lists."""
    out = []
    for rec in newest_first(path, must_contain=b'"run"'):
        if not rec.get("run"):
            continue
        if kind and rec["run"] != kind:
            continue
        if name and rec.get("name") != name:
            continue
        out.append(rec)
        if len(out) >= limit:
            break
    return out


#: How much of the file to pull in at a time when reading backwards. Large enough
#: that a normal query is one seek and one read; small enough to be worth it.
_CHUNK = 64 * 1024


def _parse(raw: bytes):
    """One record, or None if the line isn't one. Never raises."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def newest_first(path: str = None, must_contain: bytes = b""):
    """Yield records from the end of the file towards the start.

    The file is append-only and chronological, so the rows anyone wants are the
    last bytes of it. Reading forwards meant showing 200 rows cost parsing every
    line ever written — 54 ms for 20,000 rows, and linear in a file that only
    grows. Reading backwards makes a query cost what it displays.

    A generator on purpose: the caller stops when it has enough, and never has to
    say how much "enough" might be in raw lines.

    `must_contain` is a cheap pre-filter for callers that want one kind of record:
    a line without those bytes cannot be one, so it is skipped without being
    parsed. It only ever *saves* work — a coincidental match is parsed and then
    discarded by the caller's own test, exactly as before. This matters because a
    machine with no stacks has no run records at all, so asking for the last few
    runs would otherwise parse every state change ever recorded.
    """
    path = path or HISTORY_PATH
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            pos = f.tell()
            carry = b""              # a line split across two chunks
            while pos > 0:
                step = min(_CHUNK, pos)
                pos -= step
                f.seek(pos)
                lines = (f.read(step) + carry).split(b"\n")
                carry = lines.pop(0)     # may be a fragment; the next read completes it
                for raw in reversed(lines):
                    if must_contain and must_contain not in raw:
                        continue
                    rec = _parse(raw)
                    if rec is not None:
                        yield rec
            if not must_contain or must_contain in carry:
                rec = _parse(carry)
                if rec is not None:
                    yield rec
    except OSError:
        return


def read(path: str = None, limit: int = 500, service: str = None) -> list:
    """Most recent first. Malformed lines are skipped, not fatal."""
    out = []
    for rec in newest_first(path):
        if service and rec.get("service") != service:
            continue
        out.append(rec)
        if len(out) >= limit:
            break
    return out


def trim(retention_days: int, path: str = None) -> int:
    """Drop entries older than the retention window, and hard-cap the file.
    Returns how many were removed."""
    path = path or HISTORY_PATH
    if not os.path.exists(path):
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, retention_days))
    kept, dropped = [], 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    dropped += 1
                    continue
                ts = clock.parse(rec.get("ts"))
                if ts is None:
                    dropped += 1
                    continue
                if ts >= cutoff:
                    kept.append(raw)
                else:
                    dropped += 1
    except OSError:
        return 0

    # Size cap, newest kept.
    while kept and sum(len(k) + 1 for k in kept) > MAX_BYTES:
        kept.pop(0)
        dropped += 1

    if not dropped:
        return 0
    try:
        tmp = path + ".tmp"
        with _lock:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write("\n".join(kept) + ("\n" if kept else ""))
            os.replace(tmp, path)
    except OSError:
        return 0
    return dropped


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


def _within(ts: str, since) -> bool:
    if since is None:
        return True
    when = clock.parse(ts)
    # A row we cannot date is kept: it is better seen and questioned than
    # silently dropped from a timeline someone is using as evidence.
    return True if when is None else when >= since


#: Transitional states. Every one is written to the log, but a restart producing
#: "Stopping", "Stopped", "Starting", "Running" is four rows saying one thing, so
#: the halfway states are held back unless full detail is asked for.
PENDING_STATES = ("Starting", "Stopping", "Resuming", "Pausing")


def query(service_names=None, labels=None, service: str = None, hours: int = None,
          include_windows: bool = False, windows_levels=None, limit: int = 800,
          path: str = None, full: bool = False) -> list:
    """One timeline, newest first, in a shape a table can render directly.

    Merges three kinds of row: what we asked for (action), what the SCM told us
    (state), and what Windows logged about the service (windows) — so "it
    stopped" sits next to "terminated unexpectedly, .NET exception".

    full=False leaves out the halfway states, so a restart reads as "restart
    requested" then "Running" instead of four rows. Nothing is dropped from the
    file — this only decides what is worth looking at.
    """
    since = None
    if hours:
        since = datetime.now(timezone.utc) - timedelta(hours=hours)

    label_of = dict(zip(service_names or [], labels or []))
    rows = []

    # Newest first, and we stop as soon as we have a table's worth. The sort at
    # the end still decides the order, because the Windows event log is merged in
    # with timestamps of its own.
    for rec in newest_first(path):
        if len(rows) >= limit:
            break
        if service and rec.get("service") != service:
            continue
        if not _within(str(rec.get("ts", "")), since):
            continue
        name = rec.get("service", "")
        common = {"ts": rec.get("ts", ""), "service": name,
                  "label": label_of.get(name, name), "level": ""}
        if rec.get("run"):
            # A whole run — shown in the timeline too, so a stack's outcome sits
            # among the state changes it caused.
            if service:
                continue
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
            to = rec.get("to", "")
            if not full and to in PENDING_STATES and not rec.get("exit_code"):
                continue          # halfway there says nothing on its own
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

    if include_windows and service_names:
        from . import eventlog
        wanted = [service] if service else list(service_names)
        wanted_labels = ([label_of.get(service, service)] if service
                         else list(labels or []))
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
        w.writerow([f"Time ({clock.offset_label()})", "Service", "Kind", "Event",
                    "Detail", "Level", "Source"])
        for r in rows:
            w.writerow([clock.local_text(r.get("ts", ""), clock.EXPORT),
                        r.get("label") or r.get("service", ""),
                        r.get("kind", ""), r.get("event", ""), r.get("detail", ""),
                        r.get("level", ""), r.get("source", "")])
    return len(rows)


def attach(store: st.Store, enabled_getter, path: str = None) -> None:
    """Subscribe to a store so every change is written while enabled."""
    def on_event(event: st.Event):
        if enabled_getter():
            record(event, path=path)
    store.subscribe(on_event)
