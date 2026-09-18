"""Shared reset-relative time parsing for Mochi commands and future forms."""
import datetime
import math

weekdays = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2, "weds": 2, "wedn": 2, "reset": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6
}

def days_until_weekday(now, target_weekday):
    today_weekday = now.weekday()
    days_ahead = (target_weekday - today_weekday) % 7
    if days_ahead == 0: days_ahead = 7
    return days_ahead

def parse_mtime(time_text, *, now=None):
    """Return a Unix timestamp; invalid input raises ValueError.

    Reset-relative dates use the following UTC midnight. A weekday matching
    today means next week; "next weekday" adds another week, as before.
    Supply an aware datetime as now for deterministic tests.
    """
    try:
        return _parse_mtime(time_text, now=now)
    except (OverflowError, OSError) as error:
        raise ValueError("Time is outside the supported range.") from error


def _parse_mtime(time_text, *, now):
    parts = time_text.lower().split()
    if not parts:
        raise ValueError("Enter a time.")
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    elif now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must include a timezone.")
    now = now.astimezone(datetime.timezone.utc)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)

    first = parts[0]
    if first == "next":
        if len(parts) < 2 or parts[1] not in weekdays:
            raise ValueError("Use next followed by a weekday.")
        days = days_until_weekday(now, weekdays[parts[1]]) + 8
        consumed = 2
    elif first in weekdays:
        days = days_until_weekday(now, weekdays[first]) + 1
        consumed = 1
    elif first in ("today", "tomorrow", "tmr", "tmw"):
        days = 1 if first == "today" else 2
        consumed = 1
    elif "/" in first:
        pieces = first.split("/")
        if len(pieces) != 2 or any(not p.isascii() or not p.isdigit() or len(p) > 2 for p in pieces):
            raise ValueError("Use MM/DD without a year.")
        month, day = map(int, pieces)
        year = now.year + ((month, day) < (now.month, now.day))
        try:
            date = datetime.datetime(year, month, day, tzinfo=datetime.timezone.utc)
        except ValueError as error:
            raise ValueError(f"That date does not exist in {year}.") from error
        days = (date - midnight).days + 1
        consumed = 1
    else:
        if len(parts) != 1:
            raise ValueError("A numeric hour offset must be provided alone.")
        days = 1
        consumed = 0

    remaining = parts[consumed:]
    if len(remaining) > 1:
        raise ValueError("Unexpected words after the time.")
    try:
        hours = float(remaining[0]) if remaining else 0
    except ValueError as error:
        raise ValueError("The hour offset must be a number, such as +2 or -1.5") from error
    if not math.isfinite(hours):
        raise ValueError("Hour offset must be finite.")
    target = midnight + datetime.timedelta(days=days, hours=hours)
    return int(target.timestamp())
