"""Read-side query functions shared by the UI API and the agent API.

Kept separate from `api.py` so the exact same logic backs both surfaces,
as the contract requires: the agent endpoints return exactly what these
functions produce.
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional

from funes_hoard.core.summary import Context, SpanRow, day_totals, focus_blocks, where_was_i as _where_was_i
from funes_hoard.db import Database
from funes_hoard.timeparse import human_range, local_offset_str, parse_day_or_range, parse_moment

MAX_LIMIT = 100


def _row_to_span(r) -> SpanRow:
    return SpanRow(
        id=r["id"], start_ts=r["start_ts"], end_ts=r["end_ts"], kind=r["kind"],
        app=r["app"], exe=r["exe"], title=r["title"], pid=r["pid"],
        category=r["category"], project=r["project"],
    )


def get_spans(db: Database, start_ts: float, end_ts: float) -> List[SpanRow]:
    rows = db.query(
        "SELECT * FROM spans WHERE start_ts < ? AND end_ts > ? ORDER BY start_ts ASC",
        (end_ts, start_ts),
    )
    return [_row_to_span(r) for r in rows]


def _fmt_span(s: SpanRow) -> dict:
    return {
        "id": s.id,
        "start": s.start_ts,
        "end": s.end_ts,
        "kind": s.kind,
        "app": s.app,
        "title": s.title,
        "category": s.category,
        "project": s.project,
        "duration_s": round(s.duration_s),
        "human": human_range(s.start_ts, s.end_ts),
    }


def activity_now(db: Database, collector) -> dict:
    open_row = db.query_one("SELECT * FROM spans WHERE open = 1 ORDER BY id DESC LIMIT 1")
    now = time.time()
    paused = collector.is_paused(now) if collector else False
    idle_s = getattr(collector, "last_idle_s", 0.0) if collector else 0.0
    result = {
        "recording": not paused,
        "paused": paused,
        "idle_s": round(idle_s, 1),
        "utc_offset": local_offset_str(now),
        "now": now,
    }
    if open_row is not None:
        result.update({
            "app": open_row["app"],
            "title": open_row["title"],
            "category": open_row["category"],
            "project": open_row["project"],
            "kind": open_row["kind"],
            "since": open_row["start_ts"],
        })
    else:
        result.update({"app": None, "title": None, "category": None, "project": None, "kind": None, "since": None})
    return result


def activity_where_was_i(db: Database, before: Optional[str], contexts: int, now: Optional[float] = None) -> dict:
    now = now if now is not None else time.time()
    before_ts = parse_moment(before, now)
    contexts = max(1, min(contexts, 20))
    lookback = 3 * 86400.0
    spans = get_spans(db, before_ts - lookback, before_ts)
    ctxs: List[Context] = _where_was_i(spans, before_ts, contexts)
    out = []
    for c in ctxs:
        files = db.query(
            "SELECT path FROM file_events WHERE ts >= ? AND ts < ? ORDER BY ts DESC LIMIT 5",
            (c.start_ts, c.end_ts),
        )
        commits = db.query(
            "SELECT subject, sha FROM commits WHERE ts >= ? AND ts < ? ORDER BY ts DESC LIMIT 5",
            (c.start_ts, c.end_ts),
        )
        out.append({
            "project": c.project,
            "app": c.app,
            "start": c.start_ts,
            "end": c.end_ts,
            "duration_s": round(c.duration_s),
            "human": human_range(c.start_ts, c.end_ts),
            "files": [f["path"] for f in files],
            "commits": [{"subject": r["subject"], "sha": r["sha"][:10]} for r in commits],
        })
    return {"before": before_ts, "contexts": out}


def activity_timeline(db: Database, start: Optional[str], end: Optional[str], min_minutes: float, limit: int, now: Optional[float] = None) -> dict:
    now = now if now is not None else time.time()
    start_ts, end_ts = parse_day_or_range(None, start, end, now)
    limit = max(1, min(limit, MAX_LIMIT))
    spans = get_spans(db, start_ts, end_ts)
    spans = [s for s in spans if s.duration_s >= min_minutes * 60]
    spans.sort(key=lambda s: s.start_ts)
    truncated = len(spans) > limit
    out = [_fmt_span(s) for s in spans[:limit]]
    return {
        "start": start_ts, "end": end_ts, "items": out,
        "truncated": truncated, "has_more": truncated,
        "next_offset": limit if truncated else None,
    }


def activity_summary(db: Database, day: Optional[str], start: Optional[str], end: Optional[str], group_by: str, now: Optional[float] = None) -> dict:
    now = now if now is not None else time.time()
    start_ts, end_ts = parse_day_or_range(day, start, end, now)
    spans = get_spans(db, start_ts, end_ts)
    totals = day_totals(spans)
    blocks = focus_blocks(spans)
    blocks.sort(key=lambda b: -b.duration_s)
    longest = blocks[0] if blocks else None
    return {
        "start": start_ts,
        "end": end_ts,
        "human_range": human_range(start_ts, end_ts),
        "active_s": round(totals["active_s"]),
        "away_s": round(totals["away_s"]),
        "by_category": {k: round(v) for k, v in totals["by_category"].items()},
        "by_app": {k: round(v) for k, v in list(totals["by_app"].items())[:10]},
        "by_project": {k: round(v) for k, v in totals["by_project"].items()},
        "first_activity": totals["first_activity"],
        "last_activity": totals["last_activity"],
        "context_switches": totals["context_switches"],
        "focus_blocks": [
            {"key": b.key, "start": b.start_ts, "end": b.end_ts, "duration_s": round(b.duration_s), "human": human_range(b.start_ts, b.end_ts)}
            for b in blocks[:10]
        ],
        "longest_focus_block_s": round(longest.duration_s) if longest else 0,
    }


def activity_search(db: Database, query: str, since: Optional[str], until: Optional[str], limit: int, now: Optional[float] = None) -> dict:
    now = now if now is not None else time.time()
    since_ts = parse_moment(since, now) if since else 0.0
    until_ts = parse_moment(until, now) if until else now
    limit = max(1, min(limit, MAX_LIMIT))
    items: List[dict] = []
    if getattr(db, "fts_available", False):
        rows = db.query(
            "SELECT source, ref_id, ts, text, snippet(search_fts, 0, '[', ']', '...', 8) AS snip"
            " FROM search_fts WHERE search_fts MATCH ? AND ts >= ? AND ts < ? ORDER BY ts DESC LIMIT ?",
            (query, since_ts, until_ts, limit + 1),
        )
        for r in rows:
            items.append({"source": r["source"], "ref_id": r["ref_id"], "ts": r["ts"], "text": r["snip"] or r["text"]})
    else:
        like = f"%{query}%"
        rows = db.query(
            "SELECT 'span' src, id, start_ts ts, title text FROM spans WHERE title LIKE ? AND start_ts >= ? AND start_ts < ?"
            " UNION ALL SELECT 'file', id, ts, path FROM file_events WHERE path LIKE ? AND ts >= ? AND ts < ?"
            " UNION ALL SELECT 'commit', id, ts, subject FROM commits WHERE subject LIKE ? AND ts >= ? AND ts < ?"
            " ORDER BY ts DESC LIMIT ?",
            (like, since_ts, until_ts, like, since_ts, until_ts, like, since_ts, until_ts, limit + 1),
        )
        for r in rows:
            items.append({"source": r["src"], "ref_id": r["id"], "ts": r["ts"], "text": r["text"]})
    truncated = len(items) > limit
    return {"query": query, "items": items[:limit], "truncated": truncated, "has_more": truncated}


def activity_recent_files(db: Database, since: Optional[str], limit: int, now: Optional[float] = None) -> dict:
    now = now if now is not None else time.time()
    since_ts = parse_moment(since, now) if since else now - 7 * 86400.0
    limit = max(1, min(limit, MAX_LIMIT))
    rows = db.query("SELECT * FROM file_events WHERE ts >= ? ORDER BY ts DESC LIMIT ?", (since_ts, limit + 1))
    truncated = len(rows) > limit
    items = [{"path": r["path"], "ts": r["ts"], "app_hint": r["app_hint"]} for r in rows[:limit]]
    return {"items": items, "truncated": truncated, "has_more": truncated}


def activity_projects(db: Database, since: Optional[str], limit: int, now: Optional[float] = None) -> dict:
    now = now if now is not None else time.time()
    since_ts = parse_moment(since, now) if since else now - 30 * 86400.0
    limit = max(1, min(limit, MAX_LIMIT))
    spans = get_spans(db, since_ts, now)
    by_project: Dict[str, dict] = {}
    for s in spans:
        if s.kind != "active" or not s.project:
            continue
        entry = by_project.setdefault(s.project, {"project": s.project, "time_s": 0.0, "last_touched": 0.0})
        entry["time_s"] += s.duration_s
        entry["last_touched"] = max(entry["last_touched"], s.end_ts)
    for p, entry in by_project.items():
        row = db.query_one(
            "SELECT COUNT(*) c, MAX(ts) t FROM commits WHERE repo = ? AND ts >= ?", (p, since_ts)
        )
        entry["commits"] = row["c"] if row else 0
    items = sorted(by_project.values(), key=lambda e: -e["time_s"])[: limit + 1]
    truncated = len(items) > limit
    for e in items[:limit]:
        e["time_s"] = round(e["time_s"])
    return {"items": items[:limit], "truncated": truncated, "has_more": truncated}
