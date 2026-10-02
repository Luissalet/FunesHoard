"""The family's speech-to-text jobs (`transcribe_file`, `transcribe_status`, `transcribe_cancel`).

Stateless on purpose: no session, no row in Funes's database and no copy of the file. A job lives in memory, is kept for an hour
after it finishes (and only the newest fifty finished ones), and runs on the lane the importer uses (one transcription at a time,
one model in memory) through the shared `hoard_link.media.stt.Transcriber`.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from funes_hoard.hoard_link.media import stt
from funes_hoard.hoard_link.proc import Cancelled
from funes_hoard.hoard_link.waiting import DONE_STATES, wait_for

from .importer import ALLOWED_EXT, MAX_UPLOAD_BYTES
from .worker import PRIORITY_FINAL, TranscriptionWorker

log = logging.getLogger("scribe.stt")

KEEP_FINISHED_S = 3600.0
KEEP_FINISHED_MAX = 50
LANGUAGE_RE = re.compile(r"^(auto|[A-Za-z]{2,3}(-[A-Za-z]{2,4})?)$")


class NoModel(RuntimeError):
    """faster-whisper cannot be loaded here: nothing was queued."""


@dataclass
class SttJob:
    id: str
    path: str
    language: str
    model: str | None
    word_timestamps: bool
    initial_prompt: str
    vad: bool
    status: str = "queued"  # queued | running | done | error | cancelled
    progress: float = 0.0
    result: dict[str, Any] | None = None
    error: str = ""
    finished_at: float = 0.0
    cancel: threading.Event = field(default_factory=threading.Event)


def check_audio_path(path: str) -> Path:
    """The checks `scribe_import_file` makes: absolute, exists, regular file, allowed extension, 1 byte to 2 GiB."""
    source = Path(path).expanduser()
    if not source.is_absolute():
        raise ValueError("path must be absolute (for example C:\\Users\\me\\Videos\\clase.mp4).")
    if not source.exists():
        raise ValueError(f"No such file: {source}")
    if not source.is_file():
        raise ValueError(f"Not a file: {source}")
    if source.suffix.lower() not in ALLOWED_EXT:
        raise ValueError(f"Unsupported file type {source.suffix.lower() or '(none)'}; allowed: {', '.join(sorted(ALLOWED_EXT))}.")
    size = source.stat().st_size
    if size == 0:
        raise ValueError("The file is empty.")
    if size > MAX_UPLOAD_BYTES:
        raise ValueError(f"The file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB, the import limit.")
    return source


class SttJobs:
    """``engine()`` returns the shared transcriber of the sessions (one model in memory); ``engine_for(size)`` a separate one for a
    request that names another Whisper size (closed after the job). ``available()`` says whether a model can be loaded at all."""

    def __init__(self, worker: TranscriptionWorker, engine: Callable[[], stt.Transcriber],
                 engine_for: Callable[[str], stt.Transcriber], available: Callable[[], bool], clock: Callable[[], float] = time.time):
        self.worker, self._engine, self._engine_for, self._available, self.clock = worker, engine, engine_for, available, clock
        self._jobs: dict[str, SttJob] = {}
        self._lock = threading.Lock()

    # ---------- the three tools ----------
    def submit(self, path: str, language: str = "auto", model: str | None = None, word_timestamps: bool = True, initial_prompt: str = "",
               vad: bool = True, wait_s: float = 0.0) -> dict[str, Any]:
        source = check_audio_path(path)
        if not LANGUAGE_RE.match(language or "auto"):
            raise ValueError("language must be 'auto' or a language code such as 'es' or 'en'.")
        if not self._available():
            raise NoModel("No speech model is available: install faster-whisper (pip install faster-whisper) and try again.")
        job = SttJob(uuid.uuid4().hex[:12], str(source), language or "auto", model or None, word_timestamps, initial_prompt, vad)
        with self._lock:
            self._prune()
            self._jobs[job.id] = job
        self.worker.submit(f"transcribe {job.id}", lambda: self._run(job), PRIORITY_FINAL)
        return self.status(job.id, wait_s)

    def status(self, job_id: str, wait_s: float = 0.0) -> dict[str, Any]:
        with self._lock:
            self._prune()
            if job_id not in self._jobs:
                raise LookupError(f"no such job: {job_id}")
        view = wait_for(lambda: self._view(job_id), wait_s)
        if view.get("state") == "missing":
            raise LookupError(f"no such job: {job_id}")
        view.pop("waited_s", None)
        view.pop("still_running", None)
        return view

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise LookupError(f"no such job: {job_id}")
            if job.status in DONE_STATES:
                return {"job_id": job.id, "status": job.status}
            job.cancel.set()
            if job.status == "queued":
                self._finish(job, "cancelled")
        return {"job_id": job.id, "status": "cancelled"}

    # ---------- internals ----------
    def _view(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            view: dict[str, Any] = {"job_id": job.id, "status": job.status, "progress": round(job.progress, 3)}
            if job.status == "done" and job.result is not None:
                view.update(job.result)
                view["progress"] = 1.0
            if job.error:
                view["error"] = job.error
            return view

    def _finish(self, job: SttJob, status: str, *, result: dict[str, Any] | None = None, error: str = "") -> None:
        job.status, job.result, job.error, job.finished_at = status, result, error, self.clock()

    def _prune(self) -> None:
        now = self.clock()
        finished = sorted((j for j in self._jobs.values() if j.status in DONE_STATES), key=lambda j: j.finished_at)
        for job in finished:
            if now - job.finished_at > KEEP_FINISHED_S:
                del self._jobs[job.id]
        finished = [j for j in finished if j.id in self._jobs]
        for job in finished[: max(0, len(finished) - KEEP_FINISHED_MAX)]:
            del self._jobs[job.id]

    def _run(self, job: SttJob) -> None:
        with self._lock:
            if job.status != "queued":  # cancelled while it waited
                return
            job.status = "running"

        def progress(fraction: float) -> None:
            job.progress = max(job.progress, min(0.99, float(fraction)))

        engine = self._engine_for(job.model) if job.model else self._engine()
        own = bool(job.model)
        try:
            result = engine.transcribe(job.path, language=None if job.language == "auto" else job.language, word_timestamps=job.word_timestamps,
                                       vad=job.vad, initial_prompt=job.initial_prompt, progress=progress, cancel=job.cancel)
            with self._lock:
                self._finish(job, "done", result=result.as_dict())
        except Cancelled:
            with self._lock:
                self._finish(job, "cancelled")
        except Exception as error:  # noqa: BLE001 - a failed job is reported, never raised into the worker
            log.exception("transcription job %s failed", job.id)
            with self._lock:
                self._finish(job, "error", error=str(error)[:500])
        finally:
            if own:
                engine.close()
