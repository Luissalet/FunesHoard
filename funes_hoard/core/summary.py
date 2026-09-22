"""Derived knowledge over a list of (already classified) spans.

Everything here takes plain `SpanRow`-like objects (see below) so it can be
unit-tested with hand-built fixtures instead of a database.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

FOCUS_MIN_S = 25 * 60
FOCUS_MAX_GAP_S = 2 * 60
SWITCH_MIN_DWELL_S = 10


@dataclass
class SpanRow:
    id: int
    start_ts: float
    end_ts: float
    kind: str
    app: str
    exe: str
    title: str
    pid: Optional[int]
    category: str
    project: Optional[str]

    @property
    def duration_s(self) -> float:
        return max(0.0, self.end_ts - self.start_ts)


@dataclass
class FocusBlock:
    start_ts: float
    end_ts: float
    key: str  # project or category
    duration_s: float


def day_totals(spans: List[SpanRow]) -> Dict[str, object]:
    active = [s for s in spans if s.kind == "active"]
    away = [s for s in spans if s.kind in ("away", "locked")]

    active_time = sum(s.duration_s for s in active)
    away_time = sum(s.duration_s for s in away)

    by_category: Dict[str, float] = {}
    by_app: Dict[str, float] = {}
    by_project: Dict[str, float] = {}
    for s in active:
        by_category[s.category] = by_category.get(s.category, 0.0) + s.duration_s
        by_app[s.app] = by_app.get(s.app, 0.0) + s.duration_s
        if s.project:
            by_project[s.project] = by_project.get(s.project, 0.0) + s.duration_s

    first_activity = min((s.start_ts for s in spans), default=None)
    last_activity = max((s.end_ts for s in spans), default=None)

    return {
        "active_s": active_time,
        "away_s": away_time,
        "by_category": dict(sorted(by_category.items(), key=lambda kv: -kv[1])),
        "by_app": dict(sorted(by_app.items(), key=lambda kv: -kv[1])),
        "by_project": dict(sorted(by_project.items(), key=lambda kv: -kv[1])),
        "first_activity": first_activity,
        "last_activity": last_activity,
        "context_switches": count_context_switches(spans),
    }


def count_context_switches(spans: List[SpanRow]) -> int:
    """Count app changes among active spans that dwelled >= 10s."""
    active = [s for s in spans if s.kind == "active" and s.duration_s >= SWITCH_MIN_DWELL_S]
    active.sort(key=lambda s: s.start_ts)
    switches = 0
    prev_app = None
    for s in active:
        if prev_app is not None and s.app != prev_app:
            switches += 1
        prev_app = s.app
    return switches


def focus_blocks(spans: List[SpanRow]) -> List[FocusBlock]:
    """Blocks of >= 25 min in one project (or category, if no project) with
    interruptions of at most 2 min (away spans, or a different key) between
    consecutive matching active spans."""
    ordered = sorted(spans, key=lambda s: s.start_ts)
    blocks: List[FocusBlock] = []

    def key_of(s: SpanRow) -> Optional[str]:
        if s.kind != "active":
            return None
        return s.project or s.category

    i = 0
    n = len(ordered)
    while i < n:
        s = ordered[i]
        key = key_of(s)
        if key is None:
            i += 1
            continue
        block_start = s.start_ts
        block_end = s.end_ts
        active_duration = s.duration_s
        j = i + 1
        while j < n:
            nxt = ordered[j]
            gap = nxt.start_ts - block_end
            if gap > FOCUS_MAX_GAP_S:
                break
            nxt_key = key_of(nxt)
            if nxt_key is None:
                # away/locked interruption within budget: skip over it
                if nxt.duration_s > FOCUS_MAX_GAP_S:
                    break
                j += 1
                continue
            if nxt_key != key:
                break
            block_end = nxt.end_ts
            active_duration += nxt.duration_s
            j += 1
        if active_duration >= FOCUS_MIN_S:
            blocks.append(FocusBlock(start_ts=block_start, end_ts=block_end, key=key, duration_s=active_duration))
            i = j
        else:
            i += 1
    return blocks


@dataclass
class Context:
    key: str  # project or category+app
    app: str
    project: Optional[str]
    start_ts: float
    end_ts: float
    duration_s: float


def where_was_i(spans: List[SpanRow], before: float, contexts: int = 5) -> List[Context]:
    """Last N distinct work contexts before `before`, skipping away/locked,
    merging consecutive active spans that share a project (or app when no
    project is set)."""
    active = sorted(
        (s for s in spans if s.kind == "active" and s.start_ts < before),
        key=lambda s: s.start_ts,
    )
    merged: List[Context] = []
    for s in active:
        key = s.project or s.app
        if merged and merged[-1].key == key and (s.start_ts - merged[-1].end_ts) <= FOCUS_MAX_GAP_S:
            merged[-1].end_ts = s.end_ts
            merged[-1].duration_s += s.duration_s
        else:
            merged.append(Context(key=key, app=s.app, project=s.project, start_ts=s.start_ts, end_ts=s.end_ts, duration_s=s.duration_s))
    merged.sort(key=lambda c: c.start_ts, reverse=True)
    return merged[:contexts]
