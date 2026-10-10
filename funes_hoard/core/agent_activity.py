"""A chronological view of recorded agent events, not inferred working time.

Pure: callers supply the Hub receipts, Agora tasks and local commit rows.
Only selected metadata reaches the view; transcripts and arbitrary event
payloads are never copied into it.
"""
from __future__ import annotations

import math
from collections import Counter
from typing import Any, Iterable, Mapping

TURN_TYPES = frozenset({"faustus.turn.finished", "faustus.turn.completed", "faustus.turn.failed", "faustus.turn.cancelled"})
KINDS = ("turn", "task", "commit")


def _text(value: Any, limit: int = 320) -> str:
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def _time(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        n = float(value)
    except (ValueError, TypeError):
        return None
    return n if math.isfinite(n) else None


def _rows(values: Iterable[Any]):
    for value in values:
        if isinstance(value, Mapping):
            yield value


def assemble(start: float, end: float, *, events: Iterable[Any] = (), tasks: Iterable[Any] = (),
             commits: Iterable[Any] = (), limit: int = 50, offset: int = 0,
             kind: str | None = None) -> dict:
    """Merge point events in [start,end), deduplicated by their source identity.

    A Git author is not an agent identity. A closed task is the Agora's
    recorded state, not independent verification that its objective succeeded.
    """
    points: dict[str, dict] = {}

    def add(ref: str, ts: Any, **fields):
        at = _time(ts)
        if ref and at is not None and start <= at < end:
            points[ref] = {"ref": ref, "ts": at, **fields}

    for event in _rows(events):
        if event.get("source") != "faustus" or event.get("type") not in TURN_TYPES:
            continue
        data = event.get("data")
        if not isinstance(data, Mapping) or data.get('incognito') is True:
            continue
        session = _text(data.get("session_id"), 100)
        receipt = _text(data.get("id") or data.get("receipt_id"), 250)
        event_id = event.get("id")
        if not receipt and not isinstance(event_id, (str, int)):
            continue
        state = _text(data.get("status"), 20)
        state = {"ok": "completed", "error": "failed"}.get(state, state)
        if state not in ("finished", "completed", "failed", "cancelled"):
            state = {"faustus.turn.failed": "failed", "faustus.turn.cancelled": "cancelled"}.get(event["type"], "finished")
        ref = "turn:" + session + ":" + receipt if receipt else "bus:" + str(event_id)
        add(ref, event.get("ts"), kind="turn", source="bus", source_id=str(event_id or ""),
            agent="faustus", title="Faustus turn", status=state, session_id=session,
            repo=_text(data.get("repo"), 120))

    for task in _rows(tasks):
        if task.get("status") != "done" or not task.get("id"):
            continue
        tid = str(task["id"])
        add("agora:" + tid, task.get("closed_at"), kind="task", source="agora", source_id=tid,
            agent=_text(task.get("owner"), 80), title=_text(task.get("title")) or "Closed task",
            repo=_text(task.get("repo"), 120), status="closed")

    for commit in _rows(commits):
        sha = _text(commit.get("sha"), 80)
        repo = _text(commit.get("repo"), 120)
        if not sha or not repo:
            continue
        add("git:" + repo + ":" + sha, commit.get("ts"), kind="commit", source="git", source_id=sha,
            agent="", author=_text(commit.get("author"), 120), title=_text(commit.get("subject")) or sha[:12],
            repo=repo, status="recorded")

    ordered = sorted(points.values(), key=lambda point: (point["ts"], point["ref"]))
    totals = dict(Counter(point["kind"] for point in ordered))
    if kind:
        ordered = [point for point in ordered if point["kind"] == kind]
    offset, limit = max(0, int(offset)), max(1, min(100, int(limit)))
    page = ordered[offset:offset + limit]
    more = offset + limit < len(ordered)
    return {"start": start, "end": end, "items": page, "total": len(ordered),
            "totals": {name: totals.get(name, 0) for name in KINDS}, "has_more": more,
            "next_offset": offset + len(page) if more else None}
