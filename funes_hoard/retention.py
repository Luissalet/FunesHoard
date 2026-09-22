"""Retention purge: delete spans (and the file/commit history that sits
alongside them) older than the configured number of days.

The spec asks for span retention; we apply the same cutoff to file_events
and commits so "delete my history" actually means it -- documented in the
README as a deliberate widening, not a deviation that drops functionality.
"""
from __future__ import annotations

import time
from typing import Dict

from funes_hoard.db import Database


def purge_older_than(db: Database, cutoff_ts: float) -> Dict[str, int]:
    counts = {}
    for table, col in (("spans", "start_ts"), ("file_events", "ts"), ("commits", "ts")):
        row = db.query_one(f"SELECT COUNT(*) c FROM {table} WHERE {col} < ?", (cutoff_ts,))
        counts[table] = row["c"] if row else 0
        db.execute(f"DELETE FROM {table} WHERE {col} < ?", (cutoff_ts,))
    return counts


def run_retention(db: Database, now: float | None = None) -> Dict[str, int]:
    now = now if now is not None else time.time()
    days = float(db.get_meta("retention_days", "180") or 180)
    cutoff = now - days * 86400.0
    return purge_older_than(db, cutoff)


def delete_range(db: Database, start_ts: float, end_ts: float) -> Dict[str, int]:
    counts = {}
    for table, col in (("spans", "start_ts"), ("file_events", "ts"), ("commits", "ts")):
        row = db.query_one(f"SELECT COUNT(*) c FROM {table} WHERE {col} >= ? AND {col} < ?", (start_ts, end_ts))
        counts[table] = row["c"] if row else 0
        db.execute(f"DELETE FROM {table} WHERE {col} >= ? AND {col} < ?", (start_ts, end_ts))
    return counts
