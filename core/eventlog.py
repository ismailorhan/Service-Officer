"""What Windows itself recorded about a service.

Our own history says *that* a service stopped; the Windows event log often says
*why* — "terminated unexpectedly", "hung on starting", "timed out", plus whatever
the service's own source logged. Merging the two into one timeline is what makes
a support ticket answerable.

Reading is deliberately bounded: the System log on a busy server holds tens of
thousands of records, so scanning walks backwards from newest and stops at a
record limit or once it passes the requested window.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

try:
    import win32evtlog
    import win32evtlogutil
    _AVAILABLE = True
except ImportError:            # pragma: no cover - pywin32 always ships with us
    _AVAILABLE = False

#: Service Control Manager events worth surfacing. The SCM logs these under
#: "Service Control Manager" in the System log.
SCM_EVENTS = {
    7000: "failed to start",
    7001: "depends on a service that failed to start",
    7009: "timed out while connecting",
    7011: "timed out waiting for a transaction response",
    7022: "hung on starting",
    7023: "terminated with an error",
    7024: "terminated with a service-specific error",
    7026: "failed to load",
    7031: "terminated unexpectedly",
    7032: "recovery action failed",
    7034: "terminated unexpectedly",
    7036: "changed state",
}

LEVEL_ERROR, LEVEL_WARNING, LEVEL_INFO = "Error", "Warning", "Information"

_TYPE_TO_LEVEL = {1: LEVEL_ERROR, 2: LEVEL_WARNING, 4: LEVEL_INFO,
                  8: LEVEL_INFO, 16: LEVEL_INFO}

MAX_SCAN = 4000                # records to walk before giving up
LOGS = ("System", "Application")


def available() -> bool:
    return _AVAILABLE


def _as_utc(value) -> datetime:
    """pywin32 hands back a local-time datetime (or an int on old builds)."""
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, timezone.utc)
        if value.tzinfo is None:
            return value.replace(tzinfo=None).astimezone(timezone.utc)
        return value.astimezone(timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


def _mentions(record, needles) -> bool:
    haystack = [record.SourceName or ""]
    haystack.extend(str(s) for s in (record.StringInserts or ()))
    blob = " ".join(haystack).lower()
    return any(n in blob for n in needles)


def read(service_names, labels=None, hours: int = 24, levels=None,
         include_state_changes: bool = False, log_names=LOGS, limit: int = 300) -> list:
    """Windows events that mention any of these services, newest first.

    service_names: short names. labels: display names, which is what the SCM
    actually writes into its messages, so both are matched.
    """
    if not _AVAILABLE or not service_names:
        return []

    levels = set(levels or (LEVEL_ERROR, LEVEL_WARNING))
    needles = {n.lower() for n in service_names if n}
    needles |= {l.lower() for l in (labels or []) if l}
    since = datetime.now(timezone.utc) - timedelta(hours=max(1, hours))
    out = []

    for log_name in log_names:
        handle = None
        try:
            handle = win32evtlog.OpenEventLog(None, log_name)
            flags = (win32evtlog.EVENTLOG_BACKWARDS_READ |
                     win32evtlog.EVENTLOG_SEQUENTIAL_READ)
            scanned = 0
            done = False
            while not done and scanned < MAX_SCAN and len(out) < limit:
                records = win32evtlog.ReadEventLog(handle, flags, 0)
                if not records:
                    break
                for record in records:
                    scanned += 1
                    when = _as_utc(record.TimeGenerated)
                    if when < since:
                        done = True         # backwards read: everything older too
                        break
                    level = _TYPE_TO_LEVEL.get(record.EventType, LEVEL_INFO)
                    event_id = record.EventID & 0xFFFF
                    if event_id == 7036 and not include_state_changes:
                        continue
                    if level not in levels and event_id not in (7031, 7034):
                        continue
                    if not _mentions(record, needles):
                        continue
                    try:
                        message = win32evtlogutil.SafeFormatMessage(record, log_name)
                    except Exception:
                        message = " ".join(str(s) for s in (record.StringInserts or ()))
                    out.append({
                        # UTC — `when` already is, via _as_utc — so these rows
                        # interleave with ours by moment rather than by text.
                        "ts": when.isoformat(timespec="seconds"),
                        "service": _guess_service(record, service_names, labels),
                        "level": level,
                        "source": record.SourceName or log_name,
                        "event_id": event_id,
                        "summary": SCM_EVENTS.get(event_id, ""),
                        "message": " ".join((message or "").split()),
                        "log": log_name,
                    })
                    if len(out) >= limit:
                        break
        except Exception:
            continue
        finally:
            if handle is not None:
                try:
                    win32evtlog.CloseEventLog(handle)
                except Exception:
                    pass

    out.sort(key=lambda r: r["ts"], reverse=True)
    return out[:limit]


def _guess_service(record, service_names, labels) -> str:
    """Which of our services this event is about, for the Service column."""
    blob = " ".join([record.SourceName or ""] +
                    [str(s) for s in (record.StringInserts or ())]).lower()
    pairs = list(zip(service_names, labels or service_names))
    for name, label in pairs:
        if label and label.lower() in blob:
            return name
    for name, _label in pairs:
        if name and name.lower() in blob:
            return name
    return ""
