"""Best-effort Linux probe (used for manual/dev testing, not production).

Uses `xdotool` for the active window and `xprintidle` for idle time when
present on PATH; otherwise reports itself unavailable via `status()` and
`sample()` returns a degraded sample rather than raising.
"""
from __future__ import annotations

import shutil
import subprocess
import time
from typing import Optional

from funes_hoard.core.spans import Sample


class LinuxProbe:
    def __init__(self) -> None:
        self._xdotool = shutil.which("xdotool")
        self._xprintidle = shutil.which("xprintidle")

    def _run(self, args: list[str]) -> Optional[str]:
        try:
            out = subprocess.run(args, capture_output=True, timeout=1, text=True)
            if out.returncode == 0:
                return out.stdout.strip()
        except Exception:
            pass
        return None

    def sample(self) -> Sample:
        ts = time.time()
        title = ""
        app = "unknown"
        if self._xdotool:
            title = self._run([self._xdotool, "getactivewindow", "getwindowname"]) or ""
            app = self._run([self._xdotool, "getactivewindow", "getwindowclassname"]) or "unknown"
        idle_s = 0.0
        if self._xprintidle:
            raw = self._run([self._xprintidle])
            if raw and raw.isdigit():
                idle_s = int(raw) / 1000.0
        return Sample(ts=ts, app=app, exe="", title=title, pid=None, idle_s=idle_s, locked=False)

    def status(self) -> str:
        if self._xdotool:
            return "best effort (xdotool" + ("+xprintidle)" if self._xprintidle else ", no xprintidle)")
        return "collector unavailable on this platform"
