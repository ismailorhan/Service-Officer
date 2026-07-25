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


def export_csv(dest: str, path: str = None, service: str = None) -> int:
    """Write the history to CSV for a ticket. Returns rows written."""
    import csv
    rows = read(path=path, limit=100000, service=service)
    with open(dest, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Time", "Service", "Machine", "From", "To", "Source",
                    "Exit code", "Note"])
        for r in rows:
            w.writerow([r.get("ts", ""), r.get("service", ""), r.get("machine", ""),
                        r.get("from") or "", r.get("to", ""), r.get("source", ""),
                        r.get("exit_code", ""), r.get("note", "")])
    return len(rows)


def attach(store: st.Store, enabled_getter, path: str = None) -> None:
    """Subscribe to a store so every change is written while enabled."""
    def on_event(event: st.Event):
        if enabled_getter():
            record(event, path=path)
    store.subscribe(on_event)
