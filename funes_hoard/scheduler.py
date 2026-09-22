"""A single housekeeping thread that drives the low-frequency pollers.

Kept apart from `Collector` (which samples every second) since these run
far less often: recent files every 60s, git commits every 10 min,
retention purge once a day.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from funes_hoard.db import Database
from funes_hoard.git_watch import GitCommitsPoller
from funes_hoard.recent_files import RecentFilesPoller
from funes_hoard.retention import run_retention

logger = logging.getLogger("funes_hoard.scheduler")

RECENT_FILES_INTERVAL_S = 60.0
GIT_INTERVAL_S = 600.0
RETENTION_INTERVAL_S = 86400.0
TICK_S = 5.0


class BackgroundScheduler:
    def __init__(self, db: Database, collector, recent_poller: RecentFilesPoller, git_poller: GitCommitsPoller) -> None:
        self.db = db
        self.collector = collector
        self.recent_poller = recent_poller
        self.git_poller = git_poller
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._last_recent = 0.0
        self._last_git = 0.0
        self._last_retention = 0.0

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="funes-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            now = time.time()
            try:
                if now - self._last_recent >= RECENT_FILES_INTERVAL_S:
                    self.recent_poller.poll_once(now)
                    self._last_recent = now
                if now - self._last_git >= GIT_INTERVAL_S:
                    self.git_poller.poll_once()
                    self._last_git = now
                if now - self._last_retention >= RETENTION_INTERVAL_S:
                    run_retention(self.db, now)
                    self._last_retention = now
            except Exception:  # pragma: no cover - defensive
                logger.exception("scheduler tick failed")
            self._stop.wait(TICK_S)
