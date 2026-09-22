"""Derived knowledge over a list of (already classified) spans.

Everything here takes plain `SpanRow`-like objects (see below) so it can be
unit-tested with hand-built fixtures instead of a database.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
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


def clip_spans(spans: List[SpanRow], start_ts: float, end_ts: float) -> List[SpanRow]:
    """Copies of `spans` cut to [start_ts, end_ts); spans outside are dropped.

    A span that crosses midnight (or the edge of any requested window) must
    only count the part inside the window, or day totals double-count it.
    """
    out: List[SpanRow] = []
    for s in spans:
        a, b = max(s.start_ts, start_ts), min(s.end_ts, end_ts)
        if b > a or (b == a and s.start_ts == s.end_ts and start_ts <= a < end_ts):
            out.append(replace(s, start_ts=a, end_ts=b))
    return out


def focus_blocks(spans: List[SpanRow]) -> List[FocusBlock]:
    """Blocks of >= 25 min in one project (or category, if no project).

    Interruptions are allowed as long as each one -- the whole stretch of
    time away from the block's key, whatever it was spent on (away, locked,
    another app or project, or no samples at all) -- lasts at most 2 min.
    The block's duration counts only the time actually spent on its key.
    """
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
        last_match = i
        j = i + 1
        while j < n:
            nxt = ordered[j]
            if key_of(nxt) == key:
                if nxt.start_ts - block_end > FOCUS_MAX_GAP_S:
                    break
                block_end = max(block_end, nxt.end_ts)
                active_duration += nxt.duration_s
                last_match = j
            elif nxt.end_ts - block_end > FOCUS_MAX_GAP_S:
                break  # this interruption (so far) is longer than the budget
            j += 1
        if active_duration >= FOCUS_MIN_S:
            blocks.append(FocusBlock(start_ts=block_start, end_ts=block_end, key=key, duration_s=active_duration))
            i = last_match + 1
        else:
            i += 1
    return blocks


@dataclass
class Context:
    key: str  # project, or app when no project is known
    app: str
    project: Optional[str]
    start_ts: float
    end_ts: float
    duration_s: float
    title: str = ""  # the last window title seen in this context


def where_was_i(spans: List[SpanRow], before: float, contexts: int = 5) -> List[Context]:
    """Last N *distinct* work contexts before `before`, most recent first.

    Skips away/locked time and alt-tab blips (< 10 s), merges consecutive
    active spans that share a project (or app when no project is set), and
    keeps only the most recent occurrence of each context so that bouncing
    between two windows does not fill every slot with the same two entries.
    """
    active = sorted(
        (
            replace(s, end_ts=min(s.end_ts, before))
            for s in spans
            if s.kind == "active" and s.start_ts < before and s.duration_s >= SWITCH_MIN_DWELL_S
        ),
        key=lambda s: s.start_ts,
    )
    merged: List[Context] = []
    for s in active:
        key = s.project or s.app
        if merged and merged[-1].key == key and (s.start_ts - merged[-1].end_ts) <= FOCUS_MAX_GAP_S:
            merged[-1].end_ts = s.end_ts
            merged[-1].duration_s += s.duration_s
            merged[-1].title = s.title
        else:
            merged.append(Context(key=key, app=s.app, project=s.project, start_ts=s.start_ts,
                                  end_ts=s.end_ts, duration_s=s.duration_s, title=s.title))
    seen: set = set()
    out: List[Context] = []
    for c in reversed(merged):
        if c.key in seen:
            continue
        seen.add(c.key)
        out.append(c)
        if len(out) >= contexts:
            break
    return out
