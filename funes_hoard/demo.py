"""`--demo` synthetic data: three plausible days so every screen is
populated without any real window titles ever being recorded.

Generation reuses the *real* collector pipeline (SpanBuilder, privacy,
classification) via `Collector.tick(now=...)`, driven by a scripted probe
and a simulated clock stepped in 60s increments -- so the demo data is
produced by the same code path that would run in production, not a
separate hand-rolled shortcut.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from funes_hoard.collector import Collector
from funes_hoard.core.spans import Sample
from funes_hoard.db import Database

STEP_S = 60.0

PROJECTS = ["Atlas", "Lumen"]


@dataclass
class _Block:
    minutes: int
    app: str
    exe: str
    title: str
    idle_s: float = 0.0
    locked: bool = False


def _day_script(day_index: int) -> List[_Block]:
    project = PROJECTS[day_index % len(PROJECTS)]
    other = PROJECTS[(day_index + 1) % len(PROJECTS)]
    return [
        _Block(90, "Code.exe", "C:/Prog/Code.exe", f"main.py - {project} - Visual Studio Code"),
        _Block(10, "chrome.exe", "C:/Prog/chrome.exe", "DuckDB documentation - Google Chrome"),
        _Block(60, "Code.exe", "C:/Prog/Code.exe", f"api.py - {project} - Visual Studio Code"),
        _Block(15, "WindowsTerminal.exe", "C:/Prog/wt.exe", f"pwsh - {project}"),
        _Block(60, "Code.exe", "C:/Prog/Code.exe", f"tests.py - {project} - Visual Studio Code"),
        _Block(60, "unknown", "", "", idle_s=300.0),  # lunch, away
        _Block(45, "Teams.exe", "C:/Prog/Teams.exe", f"{project} weekly sync | Microsoft Teams"),
        _Block(75, "WINWORD.EXE", "C:/Prog/WINWORD.EXE", f"{project} design notes.docx - Word"),
        _Block(20, "chrome.exe", "C:/Prog/chrome.exe", "Episodic memory - Wikipedia"),
        _Block(70, "Code.exe", "C:/Prog/Code.exe", f"models.py - {other} - Visual Studio Code"),
        _Block(30, "Spotify.exe", "C:/Prog/Spotify.exe", "Focus playlist"),
        _Block(40, "Code.exe", "C:/Prog/Code.exe", f"README.md - {other} - Visual Studio Code"),
    ]


class _ScriptedProbe:
    def __init__(self, blocks: List[_Block]) -> None:
        self._blocks = blocks
        self._bi = 0
        self._minute_in_block = 0

    def sample(self) -> Sample:
        while self._bi < len(self._blocks) and self._minute_in_block >= self._blocks[self._bi].minutes:
            self._bi += 1
            self._minute_in_block = 0
        if self._bi >= len(self._blocks):
            b = self._blocks[-1]
        else:
            b = self._blocks[self._bi]
        self._minute_in_block += 1
        return Sample(ts=0.0, app=b.app, exe=b.exe, title=b.title, pid=4242, idle_s=b.idle_s, locked=b.locked)

    def status(self) -> str:
        return "demo probe"


def seed_demo_data(db: Database, now: Optional[float] = None) -> int:
    """Writes ~3 synthetic days ending at `now` (default: real now).

    Returns the number of spans created.
    """
    now = now if now is not None else time.time()
    today_local = datetime.fromtimestamp(now).date()
    first_day = today_local - timedelta(days=2)
    day_start = datetime(first_day.year, first_day.month, first_day.day, 9, 0, 0).timestamp()

    total_ticks = 0
    for day in range(3):
        blocks = _day_script(day)
        probe = _ScriptedProbe(blocks)
        collector = Collector(db, probe, interval_s=STEP_S)
        ts = day_start + day * 86400.0
        n_minutes = sum(b.minutes for b in blocks)
        for _ in range(n_minutes):
            if ts > now:
                break
            collector.tick(now=ts)
            ts += STEP_S
            total_ticks += 1
        final_span = collector.builder.close_open(at_ts=ts)
        if final_span is not None:
            collector._persist_close(final_span)  # noqa: SLF001 -- same-module helper

        project = PROJECTS[day % len(PROJECTS)]
        commit_ts = day_start + day * 86400.0 + 3 * 3600.0
        db.execute(
            "INSERT OR IGNORE INTO commits(ts, repo, sha, subject, author) VALUES (?, ?, ?, ?, ?)",
            (commit_ts, project, f"demo{day}sha{random.randint(1000,9999)}", f"feat: iterate on {project} demo module", "Luissalet"),
        )
        db.execute(
            "INSERT INTO file_events(ts, path, app_hint) VALUES (?, ?, ?)",
            (commit_ts - 600, f"C:/Users/demo/Desktop/Side projects/{project}/README.md", "md"),
        )

    return total_ticks


class LiveDemoProbe:
    """Keeps `activity_now` alive during a live demo session without ever
    touching the real desktop -- always "currently coding on Atlas"."""

    def sample(self) -> Sample:
        return Sample(
            ts=time.time(), app="Code.exe", exe="C:/Prog/Code.exe",
            title="main.py - Atlas - Visual Studio Code", pid=4242, idle_s=0.0, locked=False,
        )

    def status(self) -> str:
        return "demo probe (live)"
