"""Funes as the single timeline of the day: merge its own episodes with
Argus (screen), Echo (clipboard) and Scribe (audio) into one time-sorted,
citable list.

`recall()` answers "what was I doing at time X": every enabled source is
asked, in parallel with a short per-source timeout, for what it has around
that moment; a source that is down, unauthorized or slow never fails the
call, it shows up in `summary.unavailable` with a short reason instead.
`recall_search()` does the same across a range for a text query.

Every item is `{time, source, kind, text, citation, ref}`: `citation` is a
short bracket tag the assistant can quote verbatim (e.g.
`[argus:moment 88 16:02]`), `ref` is the payload needed to open it with that
source's own tools for more detail.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any, Optional

import httpx

from funes_hoard.core.summary import clip_spans
from funes_hoard.errors import BadInput
from funes_hoard.queries import activity_search as _funes_activity_search
from funes_hoard.queries import get_spans, human_moment
from funes_hoard.sources import DEFAULT_TIMEOUT, Source, SourceCallError, SourceRegistry, call_source_tool, check_health
from funes_hoard.timeparse import iso_local, parse_moment

DEFAULT_WINDOW_MINUTES = 15
MAX_WINDOW_MINUTES = 24 * 60
DEFAULT_LIMIT_PER_SOURCE = 20
MAX_LIMIT_PER_SOURCE = 100
DEFAULT_SEARCH_WINDOW_S = 7 * 86400.0

SCRIBE_LOOKBACK_S = 6 * 3600.0  # a meeting/session rarely runs longer than this
SCRIBE_MAX_SESSIONS = 5

FUNES_TITLE_MAX = 200


def _clip_limit(limit: Optional[int]) -> int:
    return max(1, min(int(limit or DEFAULT_LIMIT_PER_SOURCE), MAX_LIMIT_PER_SOURCE))


def _hhmm_from_ts(ts: Optional[float]) -> str:
    if ts is None:
        return ""
    return datetime.fromtimestamp(ts).strftime("%H:%M")


def _hhmm_from_iso(value: Optional[str]) -> str:
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value).strftime("%H:%M")
    except (TypeError, ValueError):
        return ""


def _parse_iso(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except (TypeError, ValueError):
        return None


def _select_sources(registry: SourceRegistry, wanted: Optional[list[str]]) -> tuple[list[Source], list[Source]]:
    """-> (sources to try, sources explicitly named but unknown to the registry)."""
    all_sources = registry.list()
    if wanted is None:
        return all_sources, []
    known = {s.id: s for s in all_sources}
    selected = [known[sid] for sid in wanted if sid in known]
    unknown = [sid for sid in wanted if sid not in known]
    return selected, unknown


# ------------------------------------------------------------------ Funes --
def _funes_window_items(db, start_ts: float, end_ts: float, limit: int, now: float) -> list[dict]:
    spans = clip_spans(get_spans(db, start_ts, end_ts), start_ts, end_ts)
    spans = [s for s in spans if s.kind == "active" and s.title]
    spans.sort(key=lambda s: -s.start_ts)
    items = []
    for s in spans[:limit]:
        title = (s.title or s.app or "?")[:FUNES_TITLE_MAX]
        text = f"{s.app}: {title}" if s.app and title != s.app else title
        items.append({
            "time": iso_local(s.start_ts),
            "source": "funes",
            "kind": "episode",
            "text": text,
            "citation": f"[funes:episode {s.id} {_hhmm_from_ts(s.start_ts)}]",
            "ref": {"id": s.id, "start": iso_local(s.start_ts), "end": iso_local(s.end_ts), "app": s.app, "project": s.project},
        })
    return items


def _funes_search_items(db, query: str, since_ts: float, until_ts: float, limit: int, now: float) -> list[dict]:
    raw = _funes_activity_search(db, query, iso_local(since_ts), iso_local(until_ts), limit, now=now)
    items = []
    for it in raw.get("items", []):
        ts = it.get("ts")
        source_kind = it.get("source", "item")
        hhmm = _hhmm_from_ts(ts) if isinstance(ts, (int, float)) else ""
        tag = f"[funes:{source_kind} {it.get('ref_id')} {hhmm}]" if hhmm else f"[funes:{source_kind} {it.get('ref_id')}]"
        items.append({
            "time": iso_local(ts) if isinstance(ts, (int, float)) else ts,
            "source": "funes",
            "kind": source_kind,
            "text": it.get("text", ""),
            "citation": tag,
            "ref": {"id": it.get("ref_id"), "source": source_kind},
        })
    return items


# ------------------------------------------------------------------ Argus --
def _argus_label(frame: dict) -> str:
    label = frame.get("window_title") or frame.get("app") or "(unknown window)"
    excerpt = (frame.get("excerpt") or frame.get("text") or "").strip()
    return f"{label} — {excerpt}" if excerpt else label


async def _fetch_argus(source: Source, start_ts: float, end_ts: float, limit: int, client: httpx.AsyncClient) -> list[dict]:
    result = await call_source_tool(
        source, "screen_timeline", {"from": iso_local(start_ts), "to": iso_local(end_ts), "limit": limit}, client=client,
    )
    items = []
    for f in result.get("frames", [])[:limit]:
        items.append({
            "time": f.get("time"),
            "source": "argus",
            "kind": "moment",
            "text": _argus_label(f),
            "citation": f"[argus:moment {f.get('id')} {_hhmm_from_iso(f.get('time'))}]",
            "ref": {"id": f.get("id"), "app": f.get("app"), "window_title": f.get("window_title")},
        })
    return items


async def _search_argus(source: Source, query: str, since_ts: float, until_ts: float, limit: int, client: httpx.AsyncClient) -> list[dict]:
    result = await call_source_tool(
        source, "screen_search", {"q": query, "from": iso_local(since_ts), "to": iso_local(until_ts), "limit": limit}, client=client,
    )
    items = []
    for h in result.get("hits", [])[:limit]:
        label = h.get("window_title") or h.get("app") or "(unknown window)"
        snippet = (h.get("snippet") or "").strip()
        items.append({
            "time": h.get("time"),
            "source": "argus",
            "kind": "moment",
            "text": f"{label} — {snippet}" if snippet else label,
            "citation": f"[argus:moment {h.get('id')} {_hhmm_from_iso(h.get('time'))}]",
            "ref": {"id": h.get("id"), "app": h.get("app")},
        })
    return items


# ------------------------------------------------------------------- Echo --
def _echo_text(clip: dict) -> str:
    return (clip.get("preview") or clip.get("snippet") or "")[:200]


async def _fetch_echo(source: Source, start_ts: float, end_ts: float, limit: int, client: httpx.AsyncClient) -> list[dict]:
    # clip_recent has no time filter of its own: over-fetch, then keep only
    # the clips last seen inside the window.
    result = await call_source_tool(source, "clip_recent", {"n": min(MAX_LIMIT_PER_SOURCE, max(limit * 3, 50))}, client=client)
    items = []
    for c in result.get("clips", []):
        ts = c.get("last_seen_at")
        if ts is None or not (start_ts <= float(ts) <= end_ts):
            continue
        items.append({
            "time": iso_local(float(ts)),
            "source": "echo",
            "kind": "clip",
            "text": _echo_text(c),
            "citation": f"[echo:clip {c.get('id')}]",
            "ref": {"id": c.get("id"), "kind": c.get("kind")},
        })
    items.sort(key=lambda it: it["time"] or "", reverse=True)
    return items[:limit]


async def _search_echo(source: Source, query: str, since_ts: float, until_ts: float, limit: int, client: httpx.AsyncClient) -> list[dict]:
    result = await call_source_tool(source, "clip_search", {"q": query, "since": since_ts, "limit": limit}, client=client)
    items = []
    for h in result.get("hits", []):
        ts = h.get("last_seen_at")
        if ts is not None and float(ts) > until_ts:
            continue
        items.append({
            "time": iso_local(float(ts)) if ts is not None else None,
            "source": "echo",
            "kind": "clip",
            "text": _echo_text(h),
            "citation": f"[echo:clip {h.get('id')}]",
            "ref": {"id": h.get("id"), "kind": h.get("kind")},
        })
        if len(items) >= limit:
            break
    return items


# ----------------------------------------------------------------- Scribe --
def _scribe_overlaps(session: dict, start_ts: float, end_ts: float) -> Optional[float]:
    started = _parse_iso(session.get("started_at"))
    if started is None:
        return None
    ended = started + float(session.get("duration_s") or 0)
    return started if started <= end_ts and ended >= start_ts else None


async def _fetch_scribe(source: Source, start_ts: float, end_ts: float, limit: int, client: httpx.AsyncClient) -> list[dict]:
    sessions_result = await call_source_tool(
        source, "scribe_sessions",
        {"from": iso_local(start_ts - SCRIBE_LOOKBACK_S), "to": iso_local(end_ts), "limit": 50},
        client=client,
    )
    overlapping = []
    for s in sessions_result.get("sessions", []):
        started = _scribe_overlaps(s, start_ts, end_ts)
        if started is not None:
            overlapping.append((s, started))
    overlapping.sort(key=lambda pair: -pair[1])

    items: list[dict] = []
    for s, started in overlapping[:SCRIBE_MAX_SESSIONS]:
        if len(items) >= limit:
            break
        from_s = max(0.0, start_ts - started)
        to_s = max(from_s, end_ts - started)
        transcript = await call_source_tool(
            source, "scribe_transcript", {"session_id": s["id"], "from_s": from_s, "to_s": to_s, "max_chars": 4000}, client=client,
        )
        for i, seg in enumerate(transcript.get("segments", []), start=1):
            if len(items) >= limit:
                break
            seg_time = started + float(seg.get("start_s") or 0)
            items.append({
                "time": iso_local(seg_time),
                "source": "scribe",
                "kind": "segment",
                "text": f"{seg.get('speaker', '?')}: {seg.get('text', '')}"[:220],
                "citation": f"[scribe:seg {i} {_hhmm_from_ts(seg_time)}]",
                "ref": {"session_id": s.get("id"), "title": s.get("title"), "start_s": seg.get("start_s")},
            })
    return items


async def _search_scribe(source: Source, query: str, since_ts: float, until_ts: float, limit: int, client: httpx.AsyncClient) -> list[dict]:
    result = await call_source_tool(
        source, "scribe_search", {"q": query, "from": iso_local(since_ts), "to": iso_local(until_ts), "limit": limit}, client=client,
    )
    items: list[dict] = []
    for entry in result.get("sessions", []):
        session = entry.get("session", {})
        started = _parse_iso(session.get("started_at"))
        for hit in entry.get("hits", []):
            if len(items) >= limit:
                break
            seg_time = started + float(hit.get("start_s") or 0) if started is not None else None
            items.append({
                "time": iso_local(seg_time) if seg_time is not None else None,
                "source": "scribe",
                "kind": "segment",
                "text": f"{hit.get('speaker', '?')}: {hit.get('snippet') or hit.get('text', '')}"[:220],
                "citation": f"[scribe:seg {hit.get('segment_id')} {_hhmm_from_ts(seg_time)}]",
                "ref": {"session_id": session.get("id"), "title": session.get("title"), "start_s": hit.get("start_s")},
            })
        if len(items) >= limit:
            break
    return items


_WINDOW_FETCHERS = {"argus": _fetch_argus, "echo": _fetch_echo, "scribe": _fetch_scribe}
_SEARCH_FETCHERS = {"argus": _search_argus, "echo": _search_echo, "scribe": _search_scribe}


async def _fetch_one_window(source: Source, start_ts: float, end_ts: float, limit: int, client: httpx.AsyncClient) -> list[dict]:
    fetcher = _WINDOW_FETCHERS.get(source.id)
    if fetcher is None:
        raise SourceCallError("unsupported_source")
    health = await check_health(source, client=client)
    if not health.ok:
        raise SourceCallError(health.reason or "unavailable")
    return await fetcher(source, start_ts, end_ts, limit, client)


async def _fetch_one_search(source: Source, query: str, since_ts: float, until_ts: float, limit: int, client: httpx.AsyncClient) -> list[dict]:
    fetcher = _SEARCH_FETCHERS.get(source.id)
    if fetcher is None:
        raise SourceCallError("unsupported_source")
    health = await check_health(source, client=client)
    if not health.ok:
        raise SourceCallError(health.reason or "unavailable")
    return await fetcher(source, query, since_ts, until_ts, limit, client)


def _unavailable_entry(source: Source, error: BaseException) -> dict:
    reason = error.reason if isinstance(error, SourceCallError) else type(error).__name__
    return {"id": source.id, "name": source.name, "reason": reason}


async def _gather(sources: list[Source], run_one, client: httpx.AsyncClient) -> tuple[list[dict], dict[str, int], list[dict]]:
    items: list[dict] = []
    counts: dict[str, int] = {}
    unavailable: list[dict] = []
    if not sources:
        return items, counts, unavailable
    results = await asyncio.gather(*(run_one(s, client) for s in sources), return_exceptions=True)
    for source, result in zip(sources, results):
        if isinstance(result, BaseException):
            unavailable.append(_unavailable_entry(source, result))
            counts[source.id] = 0
        else:
            items.extend(result)
            counts[source.id] = len(result)
    return items, counts, unavailable


async def recall(
    db, registry: SourceRegistry, at: Optional[str] = None, window_minutes: float = DEFAULT_WINDOW_MINUTES,
    sources: Optional[list[str]] = None, limit_per_source: int = DEFAULT_LIMIT_PER_SOURCE,
    now: Optional[float] = None, http_client: Optional[httpx.AsyncClient] = None,
) -> dict:
    """Merged, time-sorted timeline around `at` +/- `window_minutes`, across
    Funes's own episodes and every enabled/selected source."""
    now = now if now is not None else time.time()
    window_minutes = max(1.0, min(float(window_minutes or DEFAULT_WINDOW_MINUTES), MAX_WINDOW_MINUTES))
    limit = _clip_limit(limit_per_source)
    center = parse_moment(at, now, "point")
    start_ts, end_ts = center - window_minutes * 60.0, center + window_minutes * 60.0

    selected, unknown_ids = _select_sources(registry, sources)

    items = _funes_window_items(db, start_ts, end_ts, limit, now)
    counts = {"funes": len(items)}
    unavailable: list[dict] = [{"id": sid, "name": sid, "reason": "unknown_source"} for sid in unknown_ids]

    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=DEFAULT_TIMEOUT)
    try:
        remote_items, remote_counts, remote_unavailable = await _gather(
            [s for s in selected if s.enabled], lambda s, c: _fetch_one_window(s, start_ts, end_ts, limit, c), client,
        )
    finally:
        if owns_client:
            await client.aclose()

    for s in selected:
        if not s.enabled:
            unavailable.append({"id": s.id, "name": s.name, "reason": "disabled"})
    items.extend(remote_items)
    counts.update(remote_counts)
    unavailable.extend(remote_unavailable)

    items.sort(key=lambda it: it.get("time") or "", reverse=True)
    return {
        "at": iso_local(center),
        "window_minutes": window_minutes,
        "start": iso_local(start_ts),
        "end": iso_local(end_ts),
        "items": items,
        "summary": {"counts": counts, "total": len(items), "unavailable": unavailable},
    }


async def recall_search(
    db, registry: SourceRegistry, query: str, since: Optional[str] = None, until: Optional[str] = None,
    sources: Optional[list[str]] = None, limit_per_source: int = DEFAULT_LIMIT_PER_SOURCE,
    now: Optional[float] = None, http_client: Optional[httpx.AsyncClient] = None,
) -> dict:
    """Text search for `query` across Funes's own history and every enabled/
    selected source, merged and time-sorted, defaulting to the last 7 days."""
    now = now if now is not None else time.time()
    query = (query or "").strip()
    if not query:
        raise BadInput("empty_query", "query must contain at least one word, e.g. 'DuckDB' or 'invoice'.")
    since_ts = parse_moment(since, now, "start") if since else now - DEFAULT_SEARCH_WINDOW_S
    until_ts = parse_moment(until, now, "end") if until else now + 1
    limit = _clip_limit(limit_per_source)

    selected, unknown_ids = _select_sources(registry, sources)

    items = _funes_search_items(db, query, since_ts, until_ts, limit, now)
    counts = {"funes": len(items)}
    unavailable: list[dict] = [{"id": sid, "name": sid, "reason": "unknown_source"} for sid in unknown_ids]

    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=DEFAULT_TIMEOUT)
    try:
        remote_items, remote_counts, remote_unavailable = await _gather(
            [s for s in selected if s.enabled],
            lambda s, c: _fetch_one_search(s, query, since_ts, until_ts, limit, c), client,
        )
    finally:
        if owns_client:
            await client.aclose()

    for s in selected:
        if not s.enabled:
            unavailable.append({"id": s.id, "name": s.name, "reason": "disabled"})
    items.extend(remote_items)
    counts.update(remote_counts)
    unavailable.extend(remote_unavailable)

    items.sort(key=lambda it: it.get("time") or "", reverse=True)
    return {
        "query": query,
        "since": iso_local(since_ts),
        "until": iso_local(until_ts),
        "items": items,
        "summary": {"counts": counts, "total": len(items), "unavailable": unavailable},
    }


async def sources_status(registry: SourceRegistry, http_client: Optional[httpx.AsyncClient] = None) -> dict:
    """Health of every registered source, in parallel."""
    sources_list = registry.list()
    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=DEFAULT_TIMEOUT)
    try:
        healths = await asyncio.gather(*(check_health(s, client=client) for s in sources_list))
    finally:
        if owns_client:
            await client.aclose()
    return {
        "sources": [
            {
                "id": s.id, "name": s.name, "base_url": s.base_url, "enabled": s.enabled,
                "ok": h.ok, "reason": h.reason, "detail": h.detail,
            }
            for s, h in zip(sources_list, healths)
        ],
    }
