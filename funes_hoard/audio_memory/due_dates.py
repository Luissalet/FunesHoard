"""Resolve a spoken due date ("el martes", "mañana", "en dos semanas", "15 de octubre") to an ISO day.

Pure and deterministic: the base day is the day of the meeting, passed in, so
the same words always give the same date and tests never depend on today.
Returns None when the words do not name one specific day ("la semana que
viene", "pronto"): the caller keeps the words as `due_text` and never guesses.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, timedelta

WEEKDAYS = {
    "lunes": 0, "martes": 1, "miercoles": 2, "jueves": 3, "viernes": 4, "sabado": 5, "domingo": 6,
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
}
MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}
NUMBER_WORDS = {
    "un": 1, "una": 1, "uno": 1, "one": 1, "dos": 2, "two": 2, "tres": 3, "three": 3, "cuatro": 4, "four": 4,
    "cinco": 5, "five": 5, "seis": 6, "six": 6, "siete": 7, "seven": 7, "ocho": 8, "eight": 8, "nueve": 9,
    "nine": 9, "diez": 10, "ten": 10, "quince": 15, "veinte": 20,
}


def _plain(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def _valid(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _next_on_or_after(base: date, month: int, day: int) -> date | None:
    """The first date with this day and month that is not before `base`."""
    for year in (base.year, base.year + 1):
        candidate = _valid(year, month, day)
        if candidate and candidate >= base:
            return candidate
    return None


def _add_months(base: date, months: int) -> date:
    index = base.month - 1 + months
    year, month = base.year + index // 12, index % 12 + 1
    day = base.day
    while day > 28 and _valid(year, month, day) is None:
        day -= 1
    return date(year, month, day)


def _count(token: str) -> int | None:
    if token.isdigit():
        return int(token)
    return NUMBER_WORDS.get(token)


def resolve_due(text: str | None, base: date) -> str | None:
    """ISO date for `text` relative to the meeting day `base`, or None when it does not name one day."""
    if not text or not str(text).strip():
        return None
    words = _plain(str(text)).strip()

    match = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", words)
    if match:
        found = _valid(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        return found.isoformat() if found else None

    match = re.search(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4}|\d{2})\b", words)
    if match:
        year = int(match.group(3))
        year = year + 2000 if year < 100 else year
        found = _valid(year, int(match.group(2)), int(match.group(1)))
        return found.isoformat() if found else None
    match = re.search(r"\b(\d{1,2})[/-](\d{1,2})\b", words)
    if match:
        found = _next_on_or_after(base, int(match.group(2)), int(match.group(1)))
        return found.isoformat() if found else None

    month_names = "|".join(MONTHS)
    day = month_name = year_text = None
    match = re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:de\s+|of\s+)?({month_names})\b(?:\s+(?:de\s+|of\s+)?(\d{{4}}))?", words)
    if match:
        day, month_name, year_text = int(match.group(1)), match.group(2), match.group(3)
    else:
        match = re.search(rf"\b({month_names})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(\d{{4}}))?", words)
        if match:
            day, month_name, year_text = int(match.group(2)), match.group(1), match.group(3)
    if day is not None and month_name:
        month = MONTHS[month_name]
        found = _valid(int(year_text), month, day) if year_text else _next_on_or_after(base, month, day)
        return found.isoformat() if found else None

    # "por la mañana" is a time of day, not "tomorrow".
    cleaned = re.sub(r"\b(?:por|de|en|esta|la|a la)\s+(?:la\s+)?manana\b", " ", words)

    match = re.search(r"\b(?:en|dentro de|in)\s+(\d+|[a-z]+)\s+(dias?|days?|semanas?|weeks?|meses|mes|months?)\b", cleaned)
    if match:
        amount = _count(match.group(1))
        if amount is not None and 0 < amount <= 366:
            unit = match.group(2)
            if unit.startswith(("dia", "day")):
                return (base + timedelta(days=amount)).isoformat()
            if unit.startswith(("semana", "week")):
                return (base + timedelta(weeks=amount)).isoformat()
            return _add_months(base, amount).isoformat()

    if re.search(r"\bpasado manana\b|\bday after tomorrow\b", cleaned):
        return (base + timedelta(days=2)).isoformat()
    if re.search(r"\bmanana\b|\btomorrow\b", cleaned):
        return (base + timedelta(days=1)).isoformat()
    if re.search(r"\bhoy\b|\btoday\b|\besta noche\b|\btonight\b", cleaned):
        return base.isoformat()

    match = re.search(r"\b(" + "|".join(WEEKDAYS) + r")\b", cleaned)
    if match:
        target = WEEKDAYS[match.group(1)]
        ahead = (target - base.weekday()) % 7 or 7
        return (base + timedelta(days=ahead)).isoformat()

    if re.search(r"\b(?:a\s+)?(?:fin|final|finales)\s+de\s+mes\b|\bend of (?:the )?month\b", cleaned):
        first_next = _add_months(base.replace(day=1), 1)
        return (first_next - timedelta(days=1)).isoformat()
    return None
