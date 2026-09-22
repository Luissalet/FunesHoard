"""Read-side query functions shared by the UI API and the agent API.

Kept separate from `api.py` so the exact same logic backs both surfaces.
The UI gets epoch seconds (it formats them itself); the agent endpoints
pass the same result through `agent_view()`, which turns every timestamp
into local ISO 8601 with its UTC offset and truncates long window titles,
because the consumer is a small local model with a finite context.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from funes_hoard.core.summary import (
    Context, DEFAULT_SKIP_CATEGORIES, SpanRow, clip_spans, day_totals, focus_blocks, where_was_i as _where_was_i,
)
from funes_hoard.db import Database
from funes_hoard.errors import BadInput
from funes_hoard.timeparse import (
    format_duration, human_range, iso_local, local_offset_str, parse_day_or_range, parse_moment,
)

MAX_LIMIT = 100
TITLE_MAX = 160
GROUP_BY = ("category", "app", "project", "all")


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


def human_moment(ts: float, now: Optional[float] = None) -> str:
    """'today 14:05', 'yesterday 09:12' or 'Mon 2026-09-21 18:40'."""
    today = datetime.fromtimestamp(now if now is not None else time.time()).date()
    dt = datetime.fromtimestamp(ts)
    if dt.date() == today:
        label = "today"
    elif dt.date() == today - timedelta(days=1):
        label = "yesterday"
    else:
        label = f"{dt:%a} {dt.date().isoformat()}"
    return f"{label} {dt:%H:%M}"


def _fmt_span(s: SpanRow, now: float) -> dict:
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
        "human": human_range(s.start_ts, s.end_ts, now),
    }


def activity_now(db: Database, collector) -> dict:
    open_row = db.query_one("SELECT * FROM spans WHERE open = 1 ORDER BY id DESC LIMIT 1")
    now = time.time()
    paused = collector.is_paused(now) if collector else False
    idle_s = getattr(collector, "last_idle_s", 0.0) if collector else 0.0
    result = {
        "recording": not paused,
        "paused": paused,
        "paused_until": collector.paused_until(now) if (collector and paused) else None,
        "idle_s": round(idle_s, 1),
        "utc_offset": local_offset_str(now),
        "now": now,
    }
    if open_row is not None and not paused:
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


def activity_where_was_i(
    db: Database, before: Optional[str], contexts: int, now: Optional[float] = None,
    all_categories: bool = False,
) -> dict:
    now = now if now is not None else time.time()
    before_ts = min(parse_moment(before, now, "end"), now)
    contexts = max(1, min(contexts, 20))
    lookback = 3 * 86400.0
    spans = get_spans(db, before_ts - lookback, before_ts)
    skip = frozenset() if all_categories else DEFAULT_SKIP_CATEGORIES
    ctxs: List[Context] = _where_was_i(spans, before_ts, contexts, skip_categories=skip)
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
            "title": c.title,
            "start": c.start_ts,
            "end": c.end_ts,
            "duration_s": round(c.duration_s),
            "human": human_range(c.start_ts, c.end_ts, now),
            "files": [f["path"] for f in files],
            "commits": [{"subject": r["subject"], "sha": r["sha"][:10]} for r in commits],
        })
    return {"before": before_ts, "contexts": out}


def activity_timeline(
    db: Database, start: Optional[str], end: Optional[str], min_minutes: Optional[float], limit: int,
    now: Optional[float] = None, offset: int = 0, day: Optional[str] = None,
) -> dict:
    now = now if now is not None else time.time()
    start_ts, end_ts = parse_day_or_range(day, start, end, now)
    if min_minutes is None:
        # A6: a caller that did not ask for a specific granularity gets a
        # coarser one for anything longer than a day -- a week of every
        # 2-minute blip is ~9,000 tokens for a tool whose result should be
        # skimmable; activity_summary is the right call for real totals.
        min_minutes = 5.0 if (end_ts - start_ts) > 86400 else 2.0
    limit = max(1, min(limit, MAX_LIMIT))
    offset = max(0, offset)
    spans = clip_spans(get_spans(db, start_ts, end_ts), start_ts, end_ts)
    spans = [s for s in spans if s.duration_s >= min_minutes * 60]
    spans.sort(key=lambda s: s.start_ts)
    page = spans[offset:offset + limit]
    has_more = len(spans) > offset + limit
    return {
        "start": start_ts, "end": end_ts,
        "human_range": human_range(start_ts, end_ts, now),
        "total": len(spans),
        "items": [_fmt_span(s, now) for s in page],
        "truncated": has_more, "has_more": has_more,
        "next_offset": offset + limit if has_more else None,
    }


def activity_summary(
    db: Database, day: Optional[str], start: Optional[str], end: Optional[str], group_by: str,
    now: Optional[float] = None,
) -> dict:
    group_by = (group_by or "category").strip().lower()
    if group_by not in GROUP_BY:
        raise BadInput("bad_group_by", f"group_by must be one of {', '.join(GROUP_BY)} (got {group_by!r}).")
    now = now if now is not None else time.time()
    start_ts, end_ts = parse_day_or_range(day, start, end, now)
    spans = clip_spans(get_spans(db, start_ts, end_ts), start_ts, end_ts)
    totals = day_totals(spans)
    blocks = focus_blocks(spans)
    blocks.sort(key=lambda b: -b.duration_s)
    longest = blocks[0] if blocks else None
    result: Dict[str, Any] = {
        "start": start_ts,
        "end": end_ts,
        "human_range": human_range(start_ts, end_ts, now),
        "active_s": round(totals["active_s"]),
        "active_human": format_duration(totals["active_s"]),
        "away_s": round(totals["away_s"]),
        "away_human": format_duration(totals["away_s"]),
    }
    for key in ("category", "app", "project"):
        if group_by in (key, "all"):
            items = list(totals[f"by_{key}"].items())
            if key == "app":
                items = items[:10]
            # A7: the server instructions tell the model to answer from
            # *_human strings, but by_category/by_app/by_project were bare
            # seconds -- a "human" sibling map means it never has to convert.
            result[f"by_{key}"] = {k: round(v) for k, v in items}
            result[f"by_{key}_human"] = {k: format_duration(v) for k, v in items}
    result.update({
        "first_activity": totals["first_activity"],
        "last_activity": totals["last_activity"],
        "context_switches": totals["context_switches"],
        "focus_blocks": [
            {"key": b.key, "start": b.start_ts, "end": b.end_ts, "duration_s": round(b.duration_s),
             "human": human_range(b.start_ts, b.end_ts, now)}
            for b in blocks[:10]
        ],
        "longest_focus_block_s": round(longest.duration_s) if longest else 0,
    })
    return result


_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def fts_match_expression(query: str, any_word: bool = False) -> str:
    """Turn free text into a safe FTS5 expression.

    Raw user/model text is not valid FTS5 syntax in general (`funes-hoard`,
    `C++`, a stray quote, a bare `AND` all raise). Every word becomes a
    quoted prefix term; all terms must match unless `any_word`.
    """
    tokens = _TOKEN_RE.findall(query)
    return (" OR " if any_word else " ").join('"' + t.replace('"', '""') + '"*' for t in tokens)


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _search_rows(db: Database, query: str, since_ts: float, until_ts: float, limit: int, marks: tuple, any_word: bool, now: float) -> List[dict]:
    items: List[dict] = []
    if getattr(db, "fts_available", False):
        rows = db.query(
            "SELECT source, ref_id, ts, text, snippet(search_fts, 0, ?, ?, '...', 12) AS snip"
            " FROM search_fts WHERE search_fts MATCH ? AND ts >= ? AND ts < ? ORDER BY ts DESC LIMIT ?",
            (marks[0], marks[1], fts_match_expression(query, any_word), since_ts, until_ts, limit + 1),
        )
        for r in rows:
            items.append({"source": r["source"], "ref_id": r["ref_id"], "ts": r["ts"],
                          "when": human_moment(r["ts"], now), "text": r["snip"] or r["text"]})
        return items
    words = _TOKEN_RE.findall(query)
    joiner = " OR " if any_word else " AND "
    clauses = {}
    params: Dict[str, list] = {}
    for table, col in (("spans", "title"), ("file_events", "path"), ("commits", "subject")):
        clauses[table] = "(" + joiner.join(f"{col} LIKE ? ESCAPE '\\'" for _ in words) + ")"
        params[table] = [f"%{_like_escape(w)}%" for w in words]
    rows = db.query(
        "SELECT 'span' src, id, start_ts ts, title text FROM spans WHERE " + clauses["spans"]
        + " AND kind = 'active' AND title != '[redacted]' AND start_ts >= ? AND start_ts < ?"
        " UNION ALL SELECT 'file', id, ts, path FROM file_events WHERE " + clauses["file_events"] + " AND ts >= ? AND ts < ?"
        " UNION ALL SELECT 'commit', id, ts, subject FROM commits WHERE " + clauses["commits"] + " AND ts >= ? AND ts < ?"
        " ORDER BY ts DESC LIMIT ?",
        (*params["spans"], since_ts, until_ts, *params["file_events"], since_ts, until_ts,
         *params["commits"], since_ts, until_ts, limit + 1),
    )
    for r in rows:
        items.append({"source": r["src"], "ref_id": r["id"], "ts": r["ts"],
                      "when": human_moment(r["ts"], now), "text": r["text"]})
    return items


def activity_search(
    db: Database, query: str, since: Optional[str], until: Optional[str], limit: int,
    now: Optional[float] = None, marks: tuple = ("[", "]"),
) -> dict:
    """Newest matches first. All words must match; when that finds nothing
    and the query has several words, fall back to any word and say so in
    `matched`. `marks` wrap the matched words in the snippet: readable
    brackets for the model; the UI asks for control characters that cannot
    collide with brackets that are really in a title."""
    now = now if now is not None else time.time()
    query = (query or "").strip()
    if not _TOKEN_RE.search(query):
        raise BadInput("empty_query", "query must contain at least one word, e.g. 'DuckDB' or 'invoice'.")
    since_ts = parse_moment(since, now, "start") if since else 0.0
    until_ts = parse_moment(until, now, "end") if until else now + 1
    limit = max(1, min(limit, MAX_LIMIT))
    matched = "all words"
    items = _search_rows(db, query, since_ts, until_ts, limit, marks, False, now)
    if not items and len(_TOKEN_RE.findall(query)) > 1:
        items = _search_rows(db, query, since_ts, until_ts, limit, marks, True, now)
        matched = "any word"
    truncated = len(items) > limit
    return {"query": query, "matched": matched, "items": items[:limit], "truncated": truncated, "has_more": truncated}


def recent_commits(db: Database, since: Optional[str], limit: int, now: Optional[float] = None) -> dict:
    now = now if now is not None else time.time()
    since_ts = parse_moment(since, now, "start") if since else now - 30 * 86400.0
    limit = max(1, min(limit, MAX_LIMIT))
    rows = db.query("SELECT * FROM commits WHERE ts >= ? ORDER BY ts DESC LIMIT ?", (since_ts, limit + 1))
    items = [
        {"id": r["id"], "ts": r["ts"], "repo": r["repo"], "sha": r["sha"][:10], "subject": r["subject"], "author": r["author"]}
        for r in rows[:limit]
    ]
    return {"items": items, "truncated": len(rows) > limit, "has_more": len(rows) > limit}


def activity_recent_files(db: Database, since: Optional[str], limit: int, now: Optional[float] = None) -> dict:
    now = now if now is not None else time.time()
    since_ts = parse_moment(since, now, "start") if since else now - 7 * 86400.0
    limit = max(1, min(limit, MAX_LIMIT))
    rows = db.query("SELECT * FROM file_events WHERE ts >= ? ORDER BY ts DESC LIMIT ?", (since_ts, limit + 1))
    truncated = len(rows) > limit
    items = [
        {"id": r["id"], "path": r["path"], "ts": r["ts"], "when": human_moment(r["ts"], now), "app_hint": r["app_hint"]}
        for r in rows[:limit]
    ]
    return {"since": since_ts, "items": items, "truncated": truncated, "has_more": truncated}


def activity_projects(db: Database, since: Optional[str], limit: int, now: Optional[float] = None) -> dict:
    now = now if now is not None else time.time()
    since_ts = parse_moment(since, now, "start") if since else now - 30 * 86400.0
    limit = max(1, min(limit, MAX_LIMIT))
    spans = clip_spans(get_spans(db, since_ts, now), since_ts, now)
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
    ordered = sorted(by_project.values(), key=lambda e: -e["time_s"])
    truncated = len(ordered) > limit
    items = ordered[:limit]
    for e in items:
        e["time_human"] = format_duration(e["time_s"])
        e["time_s"] = round(e["time_s"])
        # C9: last_touched was epoch-seconds-only for the agent (agent_view
        # turns it into an ISO string, not a phrase like the rest of the
        # API's "human"/"when" fields use).
        e["last_touched_human"] = human_moment(e["last_touched"], now)
    return {"since": since_ts, "items": items, "truncated": truncated, "has_more": truncated}


# ------------------------------------------------------------ agent view --
_TIME_KEYS = {
    "start", "end", "ts", "since", "now", "before", "until", "paused_until",
    "first_activity", "last_activity", "last_touched",
}
_TEXT_KEYS = {"title", "text"}


def agent_view(value: Any) -> Any:
    """Shape a query result for the model: local ISO times, short titles."""
    if isinstance(value, list):
        return [agent_view(v) for v in value]
    if not isinstance(value, dict):
        return value
    out: Dict[str, Any] = {}
    for k, v in value.items():
        if k in _TIME_KEYS and isinstance(v, (int, float)) and not isinstance(v, bool):
            out[k] = iso_local(v)
        elif k in _TEXT_KEYS and isinstance(v, str) and len(v) > TITLE_MAX:
            out[k] = v[: TITLE_MAX - 1] + "…"
            out[f"{k}_truncated"] = True
        else:
            out[k] = agent_view(v)
    return out
