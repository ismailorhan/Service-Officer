"""The event store: one SQLite file, opened and versioned in one place.

Why SQLite rather than the append-only JSON Lines file this replaces — the
reason is not row counts, and it is worth being precise about, because "JSON
doesn't scale" would have been the wrong reason:

  * **Two processes are coming.** The roadmap's next step is Service Officer
    installed as a Windows service, running the watchdog and the scheduler while
    the desktop panel becomes a client. Appending from two processes is safe, but
    retention was not: `trim()` rewrote the file through a temporary copy and
    `os.replace`, so any append landing during a trim went to the abandoned
    inode and was **lost without trace**. Here retention is a DELETE, and WAL
    lets one writer and many readers work without blocking each other.
  * **Uptime and SLA reporting needs aggregation**, not a scan. "AppEngine 99.8%
    this month" over indexed rows is a query; over a text file it is a full parse
    every time it is asked for.
  * **A central hub interleaves several machines' events.** `id` is monotonic, so
    an uploader can ask "what happened after 41,207" and mean it.

What has *not* changed: rows are immutable events, appended and never edited.
That was right, and it is what makes both aggregation and a hub possible later.

Schema changes go in `_STEPS`, keyed by the version they upgrade *to*, and
`PRAGMA user_version` records where a file is. Never edit an old step: a customer
has a file at that version.
"""

from __future__ import annotations

import os
import sqlite3
import threading

SCHEMA_VERSION = 2

#: Applied in order to bring a file up to SCHEMA_VERSION. The key is the version
#: the step produces.
_STEPS = {
    1: (
        # ts is UTC ISO-8601 (see core/clock.py). Text, not a number, because a
        # human opening this file with any SQLite browser should be able to read
        # it — and because ISO-8601 UTC sorts correctly as text.
        """
        CREATE TABLE IF NOT EXISTS events (
            id         INTEGER PRIMARY KEY,
            ts         TEXT    NOT NULL,
            machine    TEXT    NOT NULL DEFAULT '',
            service    TEXT    NOT NULL DEFAULT '',
            kind       TEXT    NOT NULL,
            -- What was asked for ("restart") or what kind of run this was
            -- ("stack", "trigger"). `state` is the result, which is a different
            -- question and sometimes both are present.
            event      TEXT,
            state      TEXT,
            from_state TEXT,
            source     TEXT,
            outcome    TEXT,
            exit_code  INTEGER,
            seconds    REAL,
            detail     TEXT,
            extra      TEXT
        )
        """,
        "CREATE INDEX IF NOT EXISTS events_ts ON events(ts)",
        "CREATE INDEX IF NOT EXISTS events_service_ts ON events(service, ts)",
        "CREATE INDEX IF NOT EXISTS events_kind_id ON events(kind, id)",
    ),
    2: (
        # Derived, not authoritative: a day's totals per service, so a year's
        # availability reads 365 rows instead of aggregating every event again.
        # Rebuildable from `events` at any time, which is why it may be deleted
        # freely and why nothing reads it as a source of truth.
        """
        CREATE TABLE IF NOT EXISTS uptime_daily (
            day               TEXT    NOT NULL,
            machine           TEXT    NOT NULL DEFAULT '',
            service           TEXT    NOT NULL DEFAULT '',
            running_seconds   INTEGER NOT NULL DEFAULT 0,
            stopped_seconds   INTEGER NOT NULL DEFAULT 0,
            unhealthy_seconds INTEGER NOT NULL DEFAULT 0,
            restarts          INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (day, machine, service)
        )
        """,
    ),
}

#: The columns of `events`, in order, for INSERT.
COLUMNS = ("ts", "machine", "service", "kind", "event", "state", "from_state",
           "source", "outcome", "exit_code", "seconds", "detail", "extra")

_lock = threading.RLock()
_conns: dict = {}
_broken: dict = {}


def last_error(path: str) -> str:
    """Why this file could not be opened, if it couldn't."""
    return _broken.get(os.path.abspath(path), "")


def connect(path: str) -> sqlite3.Connection | None:
    """An open, migrated connection for `path`, or None if it cannot be had.

    Connections are cached per path and shared between threads, serialised by
    this module's lock. One connection is right at this volume — a handful of
    events a minute — and it keeps the write ordering obvious. `check_same_thread`
    is off because the SCM watcher, the health monitor, the scheduler and the GUI
    all record events; the lock, not the thread, is what makes that safe.

    Never raises. A history that cannot be opened must not stop the app: the user
    still gets a tray icon and a panel that says what went wrong.
    """
    key = os.path.abspath(path)
    with _lock:
        existing = _conns.get(key)
        if existing is not None:
            return existing
        conn = None
        try:
            os.makedirs(os.path.dirname(key), exist_ok=True)
            conn = sqlite3.connect(key, check_same_thread=False, timeout=5.0)
            conn.row_factory = sqlite3.Row
            # WAL: readers never block the writer, and a reader sees a consistent
            # snapshot. This is what makes the agent-writes / panel-reads split
            # work without a lock protocol of our own.
            conn.execute("PRAGMA journal_mode = WAL")
            # NORMAL rather than FULL: an event is worth a millisecond, not a
            # disk flush each time. The cost of a power cut is the last few
            # events, not a corrupt file — WAL guarantees that much.
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute("PRAGMA foreign_keys = ON")
            # So retention can hand freed pages back incrementally. Only takes
            # effect on a file created with it, which is why it is set before the
            # first table exists; an older file simply keeps its free pages.
            conn.execute("PRAGMA auto_vacuum = INCREMENTAL")
            _migrate(conn)
            _conns[key] = conn
            _broken.pop(key, None)
            return conn
        except (sqlite3.Error, OSError) as exc:
            # `sqlite3.connect` on a corrupt file succeeds — the first statement
            # is what fails — so there is an open handle to let go of. On Windows
            # keeping it would lock the file, and set_aside() could then never
            # move the damaged history out of the way: recovery would be
            # impossible in exactly the case it exists for.
            if conn is not None:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass
            _broken[key] = f"{type(exc).__name__}: {exc}"
            return None


def close(path: str = None) -> None:
    """Let go of a connection — for tests, and for a clean shutdown."""
    with _lock:
        keys = [os.path.abspath(path)] if path else list(_conns)
        for key in keys:
            conn = _conns.pop(key, None)
            if conn is not None:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring a file up to SCHEMA_VERSION, one step at a time, in a transaction."""
    have = conn.execute("PRAGMA user_version").fetchone()[0]
    if have > SCHEMA_VERSION:
        # A newer build wrote this file. Reading it is fine — the columns we know
        # still exist, because steps only ever add — so say nothing and carry on
        # rather than refusing to start after a downgrade.
        return
    for version in range(have + 1, SCHEMA_VERSION + 1):
        with conn:
            for statement in _STEPS[version]:
                conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {version}")


def integrity(path: str) -> str:
    """"ok", or what SQLite says is wrong. Cheap enough to run at startup."""
    conn = connect(path)
    if conn is None:
        return last_error(path) or "cannot open"
    try:
        with _lock:
            return conn.execute("PRAGMA quick_check").fetchone()[0]
    except sqlite3.Error as exc:
        return f"{type(exc).__name__}: {exc}"


def set_aside(path: str) -> str:
    """Move an unusable file out of the way and return where it went.

    A corrupt history is not worth the app refusing to run, and it is not ours to
    delete either — it is the customer's evidence, however damaged.
    """
    close(path)
    base = f"{path}.corrupt"
    target, n = base, 1
    while os.path.exists(target):
        target = f"{base}-{n}"
        n += 1
    try:
        os.replace(path, target)
        for suffix in ("-wal", "-shm"):
            if os.path.exists(path + suffix):
                os.replace(path + suffix, target + suffix)
        return target
    except OSError:
        return ""


def size_bytes(path: str) -> int:
    """The file and its write-ahead log together — what the disk actually holds."""
    total = 0
    for suffix in ("", "-wal", "-shm"):
        try:
            total += os.path.getsize(path + suffix)
        except OSError:
            pass
    return total
