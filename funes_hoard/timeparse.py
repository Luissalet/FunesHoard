"""Parse the date/time words the UI and the MCP tools accept.

Accepts ISO dates/datetimes, the words now/ahora, today/yesterday/hoy/ayer,
"this week"/"esta semana", "last week"/"la semana pasada", "this month"/
"este mes", and relative offsets like "-2h", "-3d", "-30m" (also "2h ago",
"hace 2h"). All parsing is done against a supplied `now` (epoch seconds,
local time) so it is deterministic and testable.

Day words are *ranges*, so a single moment needs to know which edge it
stands for: `since="today"` means "from the start of today", while
`until="yesterday"` / `before="ayer"` mean "up to the end of yesterday".
`edge="point"` keeps the historical behaviour (today -> now,
yesterday -> now - 24 h) for callers that want a single instant.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Literal, Optional, Tuple

from funes_hoard.errors import BadInput

Edge = Literal["point", "start", "end"]

_REL_RE = re.compile(
    r"^(?:hace\s+)?-?\s*(\d+(?:\.\d+)?)\s*(s|sec|m|min|h|d|w)(?:\s+ago)?$", re.IGNORECASE
)
_UNIT_SECONDS = {"s": 1, "sec": 1, "m": 60, "min": 60, "h": 3600, "d": 86400, "w": 604800}

_NOW_WORDS = {"", "now", "ahora"}
_TODAY_WORDS = {"today", "hoy"}
_YESTERDAY_WORDS = {"yesterday", "ayer"}
_THIS_WEEK_WORDS = {"this week", "esta semana"}
_LAST_WEEK_WORDS = {"last week", "la semana pasada", "semana pasada"}
_THIS_MONTH_WORDS = {"this month", "este mes"}

ACCEPTED_HINT = (
    "Use an ISO date/datetime (2026-09-22, 2026-09-22T14:30), today/hoy, "
    "yesterday/ayer, 'this week'/'esta semana', 'last week', or a relative "
    "offset like -2h, -3d, -30m."
)


class TimeParseError(BadInput):
    def __init__(self, message: str) -> None:
        super().__init__("bad_time", f"{message}. {ACCEPTED_HINT}")


def _midnight(d: date) -> float:
    return datetime(d.year, d.month, d.day).timestamp()


def _day_bounds(d: date) -> Tuple[float, float]:
    return _midnight(d), _midnight(d + timedelta(days=1))


def _week_bounds(d: date) -> Tuple[float, float]:
    monday = d - timedelta(days=d.weekday())
    return _midnight(monday), _midnight(monday + timedelta(days=7))


def _month_bounds(d: date) -> Tuple[float, float]:
    first = d.replace(day=1)
    nxt = (first + timedelta(days=32)).replace(day=1)
    return _midnight(first), _midnight(nxt)


def _named_range(text: str, now: float) -> Optional[Tuple[float, float]]:
    """Bounds for a word or plain date that names a whole period, else None."""
    today = datetime.fromtimestamp(now).date()
    if text in _TODAY_WORDS:
        return _day_bounds(today)
    if text in _YESTERDAY_WORDS:
        return _day_bounds(today - timedelta(days=1))
    if text in _THIS_WEEK_WORDS:
        return _week_bounds(today)
    if text in _LAST_WEEK_WORDS:
        return _week_bounds(today - timedelta(days=7))
    if text in _THIS_MONTH_WORDS:
        return _month_bounds(today)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        try:
            return _day_bounds(date.fromisoformat(text))
        except ValueError:
            return None
    return None


def parse_moment(value: Optional[str], now: float, edge: Edge = "point") -> float:
    """Parse a single point in time. None/"now" -> `now`.

    `edge` picks which end of a named period (today, yesterday, a plain
    date, this week...) the caller means; see the module docstring.
    """
    if value is None:
        return now
    text = " ".join(value.strip().lower().split())
    if text in _NOW_WORDS:
        return now
    if edge == "point":
        if text in _TODAY_WORDS:
            return now
        if text in _YESTERDAY_WORDS:
            return now - 86400
    named = _named_range(text, now)
    if named is not None:
        if edge == "end":
            return named[1]
        return named[0]
    m = _REL_RE.match(text)
    if m:
        amount, unit = m.groups()
        return now - float(amount) * _UNIT_SECONDS[unit.lower()]
    raw = value.strip()
    try:
        if raw.endswith(("Z", "z")):
            raw = raw[:-1] + "+00:00"
        return datetime.fromisoformat(raw.replace(" ", "T", 1)).timestamp()
    except ValueError as exc:
        raise TimeParseError(f"cannot parse time {value!r}") from exc


def parse_day_or_range(
    day: Optional[str], start: Optional[str], end: Optional[str], now: float
) -> Tuple[float, float]:
    """Resolve (day) or (start,end) into a concrete [start,end) window.

    - `day` takes priority: a day word, a period word ("this week") or a
      date; a datetime/relative offset selects the calendar day it falls on.
    - `start` alone that names a period ("yesterday", "2026-09-20") selects
      that whole period; any other `start` alone runs until now.
    - `end` alone covers the 24 h before it.
    - Nothing at all: today.
    """
    if day:
        text = " ".join(day.strip().lower().split())
        named = _named_range(text, now)
        if named is not None:
            return named
        moment = parse_moment(day, now, "point")
        return _day_bounds(datetime.fromtimestamp(moment).date())

    if start and not end:
        named = _named_range(" ".join(start.strip().lower().split()), now)
        if named is not None:
            return named

    if start or end:
        e = parse_moment(end, now, "end") if end else now
        s = parse_moment(start, now, "start") if start else e - 86400
        if e < s:
            s, e = e, s
        return s, e

    return _day_bounds(datetime.fromtimestamp(now).date())


def format_duration(seconds: float) -> str:
    minutes = int(round(max(0.0, seconds) / 60))
    if minutes < 60:
        return f"{minutes} min"
    h, m = divmod(minutes, 60)
    return f"{h} h {m:02d} min"


def _day_label(d: date, today: date) -> str:
    if d == today:
        return "today"
    if d == today - timedelta(days=1):
        return "yesterday"
    return f"{d.strftime('%a')} {d.isoformat()}"


def human_range(start_ts: float, end_ts: float, now: Optional[float] = None) -> str:
    """Human string like 'today 14:05-14:47, 42 min'."""
    today = datetime.fromtimestamp(now).date() if now is not None else datetime.now().date()
    sdt = datetime.fromtimestamp(start_ts)
    edt = datetime.fromtimestamp(end_ts)
    duration = format_duration(end_ts - start_ts)
    whole_days = sdt.time() == datetime.min.time() and edt.time() == datetime.min.time() and edt > sdt
    if whole_days:
        days = (edt.date() - sdt.date()).days
        if days == 1:
            return f"{_day_label(sdt.date(), today)}, whole day"
        last = edt.date() - timedelta(days=1)
        # Absolute dates for periods: "yesterday to Sun ..." misreads a week.
        return f"{sdt:%a} {sdt.date().isoformat()} to {last:%a} {last.isoformat()} ({days} days)"
    if sdt.date() == edt.date() or (edt - sdt) < timedelta(hours=24) and edt.time() == datetime.min.time():
        return f"{_day_label(sdt.date(), today)} {sdt:%H:%M}-{edt:%H:%M}, {duration}"
    return f"{_day_label(sdt.date(), today)} {sdt:%H:%M} to {_day_label(edt.date(), today)} {edt:%H:%M}, {duration}"


def iso_local(ts: Optional[float]) -> Optional[str]:
    """Epoch seconds -> local ISO 8601 with UTC offset, e.g. 2026-09-22T14:05:00+02:00."""
    if ts is None:
        return None
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def local_offset_str(ts: Optional[float] = None) -> str:
    """UTC offset for a timestamp, as '+01:00' style string."""
    dt = datetime.fromtimestamp(ts if ts is not None else datetime.now().timestamp()).astimezone()
    offset = dt.utcoffset() or timedelta(0)
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    total_minutes = abs(total_minutes)
    return f"{sign}{total_minutes // 60:02d}:{total_minutes % 60:02d}"
