"""One rule about time, in one place: store UTC, show local.

Everything written down — history rows, and later the events table — carries a
UTC timestamp. Everything a person reads is converted to the machine's local
time at the moment it is displayed or exported.

Why it has to be this way round:

  * Two rows written either side of a daylight-saving change have local
    timestamps that sort backwards. History used to sort rows by comparing their
    timestamp *strings*, which is chronological only while the offset never
    moves — true in Turkey, false for a customer in most of Europe every October.
  * Uptime and SLA arithmetic subtracts timestamps. Across a DST boundary, local
    times differ from elapsed time by an hour, so an availability figure would be
    quietly wrong twice a year.
  * A central hub collects several machines' history. UTC is the only
    representation in which two servers' events can be interleaved at all.

And why local on the way out: a row is evidence in a ticket, read by someone who
was in the room when it happened. "It stopped at 03:12" has to mean their 03:12.

A stored timestamp without an offset is read as UTC. Rows written before this
change carry an explicit offset (`+03:00`), so they keep their exact meaning —
mixed files are correct, because nothing compares the strings any more.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

#: How a moment is shown to a person: no offset, because it is their own clock.
DISPLAY = "%Y-%m-%d  %H:%M:%S"
#: The same moment in a spreadsheet cell Excel will recognise as a date.
EXPORT = "%Y-%m-%d %H:%M:%S"


def now_iso() -> str:
    """The moment to write down. UTC, to the second."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse(text) -> datetime | None:
    """A stored timestamp as an aware UTC datetime, or None if it isn't one.

    Accepts what is in the file today (offset-carrying, any offset) and what is
    written now (UTC). No offset means UTC.
    """
    try:
        moment = datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def to_local(text) -> datetime | None:
    """The same moment as a naive datetime on this machine's clock.

    Naive on purpose: this is what gets compared against a wall-clock setting
    like a trigger's "07:15", and what gets formatted for a person. Keeping it
    aware would only invite a naive/aware comparison to raise.
    """
    moment = parse(text)
    return None if moment is None else moment.astimezone().replace(tzinfo=None)


def local_text(text, fmt: str = DISPLAY) -> str:
    """A stored timestamp, formatted in local time. Unparseable input is handed
    back untouched — a table cell is no place to lose information."""
    moment = to_local(text)
    return moment.strftime(fmt) if moment is not None else str(text or "")


def sort_key(text) -> float:
    """Chronological order for a stored timestamp, whatever its offset.

    Unparseable rows sort oldest rather than throwing, because one bad line must
    not empty a timeline.
    """
    moment = parse(text)
    return moment.timestamp() if moment is not None else float("-inf")


def boot_time():
    """When Windows last started, as a naive local datetime, or None.

    A "when Windows starts" trigger has to mean that, not "when this app starts".
    They are the same thing only while the app launches once per boot — install
    three builds in an afternoon and the difference is three unwanted stack runs,
    which is exactly what happened.

    GetTickCount64 is what Task Manager's uptime shows, and needs no dependency.
    """
    try:
        import ctypes
        uptime_ms = ctypes.windll.kernel32.GetTickCount64()
    except Exception:
        return None
    return datetime.now() - timedelta(milliseconds=int(uptime_ms))


def offset_label(moment: datetime | None = None) -> str:
    """This machine's UTC offset, e.g. "UTC+03:00" — for an export header, so a
    column of local times says whose local time it is."""
    local = (moment or datetime.now(timezone.utc)).astimezone()
    offset = local.utcoffset()
    if offset is None:
        return "UTC"
    total = int(offset.total_seconds())
    sign = "-" if total < 0 else "+"
    hours, minutes = divmod(abs(total) // 60, 60)
    return f"UTC{sign}{hours:02d}:{minutes:02d}"
