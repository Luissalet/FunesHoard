"""Turn a stream of raw samples into spans.

This is the heart of the collector and is pure/deterministic so it can be
unit-tested without a real probe: feed it `Sample`s with a fake clock and
check the `Span`s it produces. No FastAPI, no sqlite here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Sample:
    ts: float
    app: str
    exe: str
    title: str
    pid: Optional[int]
    idle_s: float
    locked: bool


@dataclass
class Span:
    start_ts: float
    end_ts: float
    kind: str  # "active" | "away" | "locked"
    app: str
    exe: str
    title: str
    pid: Optional[int]

    @property
    def duration_s(self) -> float:
        return max(0.0, self.end_ts - self.start_ts)


DEFAULT_AWAY_AFTER_S = 120.0
DEFAULT_SLEEP_GAP_S = 90.0


class SpanBuilder:
    """Incrementally folds samples into spans.

    - Consecutive active samples with the same (app, title) extend the span.
    - idle_s >= away_after_s closes the active span and opens an "away"
      span, back-dated to when input actually stopped (ts - idle_s).
    - locked=True closes whatever was open and opens a "locked" span.
    - A gap between samples larger than sleep_gap_s (sleep/hibernate) closes
      the open span at the *last* sample's timestamp instead of bridging it.
    """

    def __init__(
        self,
        away_after_s: float = DEFAULT_AWAY_AFTER_S,
        sleep_gap_s: float = DEFAULT_SLEEP_GAP_S,
        floor_ts: Optional[float] = None,
    ) -> None:
        self.away_after_s = away_after_s
        self.sleep_gap_s = sleep_gap_s
        self._current: Optional[Span] = None
        self._last_ts: Optional[float] = None
        self._floor: Optional[float] = floor_ts

    def raise_floor(self, ts: float) -> None:
        """Never let a span this builder opens start before `ts`.

        Recording (re)started at `ts` -- the caller passes the end of the
        last persisted span at startup, and `interrupt()` raises it itself
        after a pause or an excluded sample. Without a floor, a deeply idle
        first sample back-dates its away span to `sample.ts - idle_s` with
        nothing bounding it: on a fresh run that can land before the app
        even existed, and on a restart during the same idle period it
        reproduces a span that was already recorded, double-counting it.
        """
        self._floor = ts if self._floor is None else max(self._floor, ts)

    @property
    def current(self) -> Optional[Span]:
        return self._current

    def add_sample(self, s: Sample, away_after_s: Optional[float] = None) -> List[Span]:
        """`away_after_s` overrides `self.away_after_s` for this one sample
        (A3: the caller passes a longer threshold while the foreground app
        is a meeting or a video, since no input for a while there is normal,
        not idleness)."""
        closed: List[Span] = []
        threshold = self.away_after_s if away_after_s is None else away_after_s

        if self._last_ts is not None and (s.ts - self._last_ts) > self.sleep_gap_s:
            if self._current is not None:
                self._current.end_ts = self._last_ts
                closed.append(self._current)
                self._current = None

        kind = "locked" if s.locked else ("away" if s.idle_s >= threshold else "active")

        if kind == "away" and (self._current is None or self._current.kind != "away"):
            if self._current is not None:
                lower_bound = self._current.start_ts
            elif self._floor is not None:
                lower_bound = self._floor
            else:
                # Nothing recorded yet and no floor set: this sample is the
                # earliest thing we know about, so it cannot predate itself.
                lower_bound = s.ts
            start = max(s.ts - max(0.0, s.idle_s), lower_bound)
            start = min(start, s.ts)
            if self._current is not None:
                self._current.end_ts = start
                closed.append(self._current)
            self._current = Span(start_ts=start, end_ts=s.ts, kind="away", app=s.app, exe=s.exe, title=s.title, pid=s.pid)

        elif kind == "locked" and (self._current is None or self._current.kind != "locked"):
            if self._current is not None:
                self._current.end_ts = s.ts
                closed.append(self._current)
            self._current = Span(start_ts=s.ts, end_ts=s.ts, kind="locked", app=s.app, exe=s.exe, title=s.title, pid=s.pid)

        elif kind == "active":
            same = (
                self._current is not None
                and self._current.kind == "active"
                and self._current.app == s.app
                and self._current.title == s.title
            )
            if same:
                self._current.end_ts = s.ts  # type: ignore[union-attr]
            else:
                if self._current is not None:
                    self._current.end_ts = s.ts
                    closed.append(self._current)
                self._current = Span(start_ts=s.ts, end_ts=s.ts, kind="active", app=s.app, exe=s.exe, title=s.title, pid=s.pid)

        else:
            # Same non-active kind continuing (away->away or locked->locked).
            self._current.end_ts = s.ts  # type: ignore[union-attr]

        self._last_ts = s.ts
        return closed

    def interrupt(self, ts: float) -> Optional[Span]:
        """Close the open span because recording stopped at `ts` (a sample was
        excluded by a privacy rule, or recording was paused).

        Without this, the next ordinary sample of the same window would
        simply extend the old span across the gap and bill the private
        interlude to whatever was open before it.
        """
        span = self._current
        if span is not None:
            gap_too_big = self._last_ts is not None and (ts - self._last_ts) > self.sleep_gap_s
            span.end_ts = max(span.end_ts, self._last_ts if gap_too_big else ts)
            self.raise_floor(span.end_ts)
        else:
            self.raise_floor(ts)
        self._current = None
        self._last_ts = None
        return span

    @property
    def last_sample_ts(self) -> Optional[float]:
        return self._last_ts

    def peek_open(self) -> Optional[Span]:
        """The still-open span, for periodic crash-safe flushing."""
        return self._current

    def close_open(self, at_ts: Optional[float] = None) -> Optional[Span]:
        """Force-close the open span (e.g. on shutdown)."""
        span = self._current
        if span is not None and at_ts is not None:
            span.end_ts = max(span.end_ts, at_ts)
        self._current = None
        return span
