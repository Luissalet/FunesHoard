"""A tiny in-memory background job runner.

Long work (bulk reclassify, retention purge) runs on a single worker
thread so HTTP handlers stay responsive; `/api/jobs` exposes progress.
Jobs are process-lifetime only (not persisted) -- acceptable for a local
single-user tool where a restart is rare and jobs are short.
"""
from __future__ import annotations

import itertools
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional


@dataclass
class Job:
    id: str
    kind: str
    status: str = "queued"  # queued | running | done | error
    progress: int = 0
    total: int = 0
    error: Optional[str] = None
    created_ts: float = field(default_factory=time.time)
    updated_ts: float = field(default_factory=time.time)
    result: Optional[dict] = None


class JobManager:
    def __init__(self) -> None:
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()

    def list(self) -> list[Job]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created_ts, reverse=True)

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def submit(self, kind: str, fn: Callable[[Callable[[int, int], None]], dict]) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind)
        with self._lock:
            self._jobs[job.id] = job

        def progress_cb(done: int, total: int) -> None:
            with self._lock:
                job.progress = done
                job.total = total
                job.updated_ts = time.time()

        def runner() -> None:
            with self._lock:
                job.status = "running"
                job.updated_ts = time.time()
            try:
                result = fn(progress_cb)
                with self._lock:
                    job.status = "done"
                    job.result = result
                    job.updated_ts = time.time()
            except Exception as exc:  # pragma: no cover - defensive
                with self._lock:
                    job.status = "error"
                    job.error = str(exc)
                    job.updated_ts = time.time()

        threading.Thread(target=runner, name=f"funes-job-{kind}", daemon=True).start()
        return job
