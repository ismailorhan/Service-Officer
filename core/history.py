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

from . import config as cfg_mod
from . import state as st

HISTORY_PATH = os.path.join(cfg_mod.APP_DIR, "history.jsonl")
MAX_BYTES = 5 * 1024 * 1024      # keep it well-behaved on a customer server

_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


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
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with _lock, open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError:
        pass


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
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with _lock, open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError:
        pass


def read(path: str = None, limit: int = 500, service: str = None) -> list:
    """Most recent first. Malformed lines are skipped, not fatal."""
    path = path or HISTORY_PATH
    if not os.path.exists(path):
        return []
    out = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if service and rec.get("service") != service:
                    continue
                out.append(rec)
    except OSError:
        return []
    out.reverse()
    return out[:limit]


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
                    ts = datetime.fromisoformat(rec["ts"])
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                except (json.JSONDecodeError, KeyError, ValueError):
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
}

ACTION_TEXT = {"start": "start requested", "stop": "stop requested",
               "restart": "restart requested", "kill": "process killed",
               "run stack": "stack run requested"}


def _within(ts: str, since) -> bool:
    if since is None:
        return True
    try:
        when = datetime.fromisoformat(ts)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return when >= since
    except (TypeError, ValueError):
        return True


def query(service_names=None, labels=None, service: str = None, hours: int = None,
          include_windows: bool = False, windows_levels=None, limit: int = 800,
          path: str = None) -> list:
    """One timeline, newest first, in a shape a table can render directly.

    Merges three kinds of row: what we asked for (action), what the SCM told us
    (state), and what Windows logged about the service (windows) — so "it
    stopped" sits next to "terminated unexpectedly, .NET exception".
    """
    since = None
    if hours:
        since = datetime.now(timezone.utc) - timedelta(hours=hours)

    label_of = dict(zip(service_names or [], labels or []))
    rows = []

    for rec in read(path=path, limit=100000):
        if service and rec.get("service") != service:
            continue
        if not _within(str(rec.get("ts", "")), since):
            continue
        name = rec.get("service", "")
        common = {"ts": rec.get("ts", ""), "service": name,
                  "label": label_of.get(name, name), "level": ""}
        if rec.get("action"):
            rows.append({**common, "kind": "action",
                         "event": ACTION_TEXT.get(rec["action"], rec["action"]),
                         "detail": rec.get("note", ""),
                         "source": SOURCE_TEXT.get(rec.get("source", ""),
                                                   rec.get("source", ""))})
        else:
            detail = []
            if rec.get("from"):
                detail.append(f"was {rec['from']}")
            if rec.get("exit_code"):
                detail.append(f"exit code {rec['exit_code']}")
            if rec.get("note"):
                detail.append(rec["note"])
            level = "Error" if rec.get("exit_code") else ""
            rows.append({**common, "kind": "state", "event": rec.get("to", ""),
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

    rows.sort(key=lambda r: r["ts"], reverse=True)
    return rows[:limit]


def export_csv(dest: str, rows=None, path: str = None, service: str = None) -> int:
    """Write a timeline to CSV for a ticket. Pass the rows you are looking at so
    the file matches the filters on screen; otherwise everything is exported."""
    import csv
    if rows is None:
        rows = query(service=service, path=path)
    with open(dest, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Time", "Service", "Kind", "Event", "Detail", "Level", "Source"])
        for r in rows:
            w.writerow([r.get("ts", ""), r.get("label") or r.get("service", ""),
                        r.get("kind", ""), r.get("event", ""), r.get("detail", ""),
                        r.get("level", ""), r.get("source", "")])
    return len(rows)


def attach(store: st.Store, enabled_getter, path: str = None) -> None:
    """Subscribe to a store so every change is written while enabled."""
    def on_event(event: st.Event):
        if enabled_getter():
            record(event, path=path)
    store.subscribe(on_event)
