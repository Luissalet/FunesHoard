"""Parse the date/time words the UI and the MCP tools accept.

Accepts ISO dates/datetimes, the words today/yesterday/hoy/ayer, "this
week"/"esta semana", and relative offsets like "-2h", "-3d", "-30m". All
parsing is done against a supplied `now` (epoch seconds, local time assumed
naive) so it is deterministic and testable.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, date
from typing import Optional, Tuple

_REL_RE = re.compile(r"^-(\d+(?:\.\d+)?)(s|m|h|d|w)$", re.IGNORECASE)
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}

_TODAY_WORDS = {"today", "hoy"}
_YESTERDAY_WORDS = {"yesterday", "ayer"}
_THIS_WEEK_WORDS = {"this week", "esta semana"}
_LAST_WEEK_WORDS = {"last week", "la semana pasada", "semana pasada"}


class TimeParseError(ValueError):
    pass


def _day_bounds(d: date) -> Tuple[float, float]:
    start = datetime(d.year, d.month, d.day)
    end = start + timedelta(days=1)
    return start.timestamp(), end.timestamp()


def _week_bounds(d: date) -> Tuple[float, float]:
    monday = d - timedelta(days=d.weekday())
    start = datetime(monday.year, monday.month, monday.day)
    end = start + timedelta(days=7)
    return start.timestamp(), end.timestamp()


def parse_moment(value: Optional[str], now: float) -> float:
    """Parse a single point in time. None/"now" -> `now`."""
    if value is None:
        return now
    text = value.strip().lower()
    if text in ("", "now", "ahora"):
        return now
    if text in _TODAY_WORDS:
        return now
    if text in _YESTERDAY_WORDS:
        return now - 86400
    m = _REL_RE.match(text)
    if m:
        amount, unit = m.groups()
        return now - float(amount) * _UNIT_SECONDS[unit.lower()]
    # ISO datetime or date
    try:
        if "t" in text or " " in text.strip():
            iso = value.strip().replace(" ", "T", 1)
            return datetime.fromisoformat(iso).timestamp()
        return datetime.fromisoformat(value.strip()).timestamp()
    except ValueError as exc:
        raise TimeParseError(f"cannot parse moment: {value!r}") from exc


def parse_day_or_range(
    day: Optional[str], start: Optional[str], end: Optional[str], now: float
) -> Tuple[float, float]:
    """Resolve (day) or (start,end) into a concrete [start,end) window.

    `day` takes priority when given; otherwise `start`/`end` are parsed as
    moments, with `end` defaulting to `now`. Words "this week"/"esta semana"
    and "last week" are accepted for `day`.
    """
    if day:
        text = day.strip().lower()
        if text in _THIS_WEEK_WORDS:
            return _week_bounds(datetime.fromtimestamp(now).date())
        if text in _LAST_WEEK_WORDS:
            s, e = _week_bounds(datetime.fromtimestamp(now).date())
            return s - 7 * 86400, e - 7 * 86400
        if text in _TODAY_WORDS:
            return _day_bounds(datetime.fromtimestamp(now).date())
        if text in _YESTERDAY_WORDS:
            return _day_bounds(datetime.fromtimestamp(now - 86400).date())
        try:
            d = datetime.fromisoformat(text).date()
        except ValueError as exc:
            raise TimeParseError(f"cannot parse day: {day!r}") from exc
        return _day_bounds(d)

    if start or end:
        s = parse_moment(start, now) if start else now - 86400
        e = parse_moment(end, now) if end else now
        if e < s:
            s, e = e, s
        return s, e

    return _day_bounds(datetime.fromtimestamp(now).date())


def human_range(start_ts: float, end_ts: float) -> str:
    """Human string like 'today 14:05-14:47, 42 min'."""
    sdt = datetime.fromtimestamp(start_ts)
    edt = datetime.fromtimestamp(end_ts)
    today = datetime.now().date()
    day_label = sdt.strftime("%Y-%m-%d")
    if sdt.date() == today:
        day_label = "today"
    elif sdt.date() == today - timedelta(days=1):
        day_label = "yesterday"
    minutes = round((end_ts - start_ts) / 60)
    return f"{day_label} {sdt.strftime('%H:%M')}-{edt.strftime('%H:%M')}, {minutes} min"


def local_offset_str(ts: Optional[float] = None) -> str:
    """UTC offset for a timestamp, as '+01:00' style string."""
    dt = datetime.fromtimestamp(ts if ts is not None else datetime.now().timestamp()).astimezone()
    offset = dt.utcoffset() or timedelta(0)
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    total_minutes = abs(total_minutes)
    return f"{sign}{total_minutes // 60:02d}:{total_minutes % 60:02d}"
