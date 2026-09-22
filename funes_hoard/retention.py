"""Retention purge and delete-a-range.

Both remove spans *and* the file/commit history that sits alongside them,
plus the matching rows of the full-text index -- otherwise a deleted window
title would still come back from search.

The spec asks for span retention; we apply the same cutoff to file_events
and commits so "delete my history" actually means it -- documented in the
README as a deliberate widening.
"""
from __future__ import annotations

import time
from typing import Dict, List

from funes_hoard.db import Database

_SOURCES = (("spans", "span"), ("file_events", "file"), ("commits", "commit"))


def _delete_ids(db: Database, table: str, source: str, ids: List[int]) -> None:
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        marks = ",".join("?" * len(chunk))
        db.execute(f"DELETE FROM {table} WHERE id IN ({marks})", chunk)
        if db.fts_available:
            db.execute(f"DELETE FROM search_fts WHERE source = ? AND ref_id IN ({marks})", [source, *chunk])


def _purge(db: Database, where: Dict[str, tuple]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for table, source in _SOURCES:
        clause, params = where[table]
        ids = [r["id"] for r in db.query(f"SELECT id FROM {table} WHERE {clause}", params)]
        _delete_ids(db, table, source, ids)
        counts[table] = len(ids)
    return counts


def purge_older_than(db: Database, cutoff_ts: float) -> Dict[str, int]:
    return _purge(db, {
        "spans": ("start_ts < ?", (cutoff_ts,)),
        "file_events": ("ts < ?", (cutoff_ts,)),
        "commits": ("ts < ?", (cutoff_ts,)),
    })


def run_retention(db: Database, now: float | None = None) -> Dict[str, int]:
    now = now if now is not None else time.time()
    days = float(db.get_meta("retention_days", "180") or 180)
    cutoff = now - days * 86400.0
    return purge_older_than(db, cutoff)


def delete_range(db: Database, start_ts: float, end_ts: float) -> Dict[str, int]:
    """Delete everything recorded in [start_ts, end_ts).

    Spans that merely overlap the range are deleted too: a window title
    that was on screen during the range must not survive because its span
    happened to start a minute earlier.
    """
    return _purge(db, {
        "spans": ("start_ts < ? AND end_ts > ?", (end_ts, start_ts)),
        "file_events": ("ts >= ? AND ts < ?", (start_ts, end_ts)),
        "commits": ("ts >= ? AND ts < ?", (start_ts, end_ts)),
    })
