"""Small time-bound parser: ISO dates/datetimes, epoch seconds and a few Spanish/English phrases."""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta

TIME_HELP = "ISO date/datetime (2026-03-01, 2026-03-01T09:00), or 'hoy', 'ayer', 'esta semana', 'hace 3 días', 'today', 'yesterday'."


def _day_start(dt: datetime) -> datetime:
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def parse_bound(raw: str | None, end: bool = False, now: float | None = None) -> float | None:
    """Return epoch seconds for a bound, or None when empty. `end` picks the end of a day/phrase."""
    if raw is None or not str(raw).strip():
        return None
    text = str(raw).strip().lower()
    current = datetime.fromtimestamp(now or time.time())
    today = _day_start(current)
    day = timedelta(days=1)
    if re.fullmatch(r"\d{9,11}(\.\d+)?", text):
        return float(text)
    if text in ("hoy", "today"):
        return (today + day if end else today).timestamp()
    if text in ("ayer", "yesterday"):
        return (today if end else today - day).timestamp()
    if text in ("esta semana", "this week"):
        start = today - timedelta(days=today.weekday())
        return (start + timedelta(days=7) if end else start).timestamp()
    if text in ("este mes", "this month"):
        start = today.replace(day=1)
        nxt = (start + timedelta(days=32)).replace(day=1)
        return (nxt if end else start).timestamp()
    match = re.fullmatch(r"(?:hace|last)\s+(\d+)\s+(d[ií]as?|days?|horas?|hours?|semanas?|weeks?)", text) or re.fullmatch(r"(\d+)\s+(d[ií]as?|days?|horas?|hours?|semanas?|weeks?)\s+ago", text)
    if match:
        amount, unit = int(match.group(1)), match.group(2)
        if unit.startswith(("h",)):
            return (current - timedelta(hours=amount)).timestamp()
        if unit.startswith(("s", "w")):
            return (today - timedelta(weeks=amount)).timestamp()
        return (today - timedelta(days=amount)).timestamp()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%d/%m/%Y"):
        try:
            parsed = datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
        if fmt in ("%Y-%m-%d", "%d/%m/%Y") and end:
            parsed += day
        return parsed.timestamp()
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError as error:
        raise ValueError(f"Unrecognised time '{raw}'. {TIME_HELP}") from error


def iso_local(epoch: float | None) -> str | None:
    return datetime.fromtimestamp(epoch).isoformat(timespec="seconds") if epoch else None
