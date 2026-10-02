"""Resolve a spoken due date ("el martes", "mañana", "en dos semanas", "15 de octubre") to an ISO day.

Pure and deterministic: the base day is the day of the meeting, passed in, so the same words always give the same date and tests
never depend on today. Returns None when the words do not name one specific day ("la semana que viene", "pronto"): the caller keeps
the words as `due_text` and never guesses. The parsing is the family's (`hoard_link.dates.parse_due`).
"""

from __future__ import annotations

from datetime import date

from funes_hoard.hoard_link.dates import parse_due


def resolve_due(text: str | None, base: date) -> str | None:
    """ISO date for `text` relative to the meeting day `base`, or None when it does not name one day."""
    found = parse_due(text, today=base)
    return found.isoformat() if found else None
