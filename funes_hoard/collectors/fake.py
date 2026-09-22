"""A scripted probe for tests and for driving the --demo data generator."""
from __future__ import annotations

from typing import List, Optional

from funes_hoard.core.spans import Sample


class FakeProbe:
    """Replays a fixed list of samples, then repeats the last one forever."""

    def __init__(self, samples: List[Sample]) -> None:
        if not samples:
            raise ValueError("FakeProbe needs at least one sample")
        self._samples = samples
        self._i = 0

    def sample(self) -> Sample:
        s = self._samples[min(self._i, len(self._samples) - 1)]
        self._i += 1
        return s

    def status(self) -> str:
        return "fake probe (tests/demo)"


class ClockFakeProbe:
    """Advances a fake clock and lets a callback decide app/title/idle per tick.

    Handy for generating long synthetic days without materializing every
    sample up front.
    """

    def __init__(self, start_ts: float, step_s: float, gen) -> None:
        self.ts = start_ts
        self.step_s = step_s
        self._gen = gen  # callable(ts) -> (app, exe, title, pid, idle_s, locked)

    def sample(self) -> Sample:
        app, exe, title, pid, idle_s, locked = self._gen(self.ts)
        s = Sample(ts=self.ts, app=app, exe=exe, title=title, pid=pid, idle_s=idle_s, locked=locked)
        self.ts += self.step_s
        return s

    def status(self) -> str:
        return "fake probe (tests/demo)"
