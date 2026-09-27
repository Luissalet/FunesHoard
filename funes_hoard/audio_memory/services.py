"""Wiring of database, stores, backends, recorder, worker and pipeline."""

from __future__ import annotations

import logging
import secrets
import shutil
import time

from . import SERVICE, __version__
from .audio import select_backend
from .config import Config
from .db import Database
from .events import EventBus
from .importer import Importer, ffmpeg_path, pyav_available
from .pipeline import Pipeline
from .recorder import Recorder
from .settings import SettingsPatch, SettingsStore
from .store import SessionStore
from .transcribe import select_transcriber, whisper_available
from .vad import HAVE_WEBRTCVAD
from .worker import TranscriptionWorker

log = logging.getLogger("scribe")


def write_token(config: Config) -> str:
    config.data_dir.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(32)
    config.token_path.write_text(token, encoding="utf-8")
    try:
        config.token_path.chmod(0o600)
    except OSError:
        pass
    return token


class Services:
    def __init__(self, config: Config):
        self.config = config
        self.started_at = time.time()
        config.data_dir.mkdir(parents=True, exist_ok=True)
        config.sessions_dir.mkdir(parents=True, exist_ok=True)
        config.models_dir.mkdir(parents=True, exist_ok=True)
        self.token = write_token(config)
        self.db = Database(config.db_path)
        self.settings = SettingsStore(self.db)
        self.sessions = SessionStore(self.db, config.sessions_dir)
        self.bus = EventBus()
        self.worker = TranscriptionWorker()
        s = self.settings.get()
        self.backend, self.notes = select_backend(config.audio_backend, config.fake_fixture, config.fake_speed)
        self.transcriber, notes = select_transcriber(config.transcriber, config.models_dir, s.model_size, s.device, s.compute_type)
        self.notes += notes
        for note in self.notes:
            log.info("backend: %s", note)
        self.pipeline = Pipeline(self.sessions, self.transcriber, self.worker, self.bus, self.settings)
        self.importer = Importer(self.sessions, self.pipeline)
        self.recorder = Recorder(self.backend, self.settings, self.sessions, self.transcriber, self.worker, self.bus)
        self.recorder.on_stopped = self.pipeline.enqueue_final

    # ---------- lifecycle ----------
    def start(self) -> None:
        self.worker.start()
        self._recover()

    def _recover(self) -> None:
        """Sessions left 'recording' by a crash get finalised from whatever audio was written."""
        for session in self.sessions.list(status="recording", limit=50) + self.sessions.list(status="processing", limit=50):
            log.info("recovering session %s", session["id"])
            self.pipeline.enqueue_final(session["id"])

    def stop(self) -> None:
        if self.recorder.current is not None:
            try:
                self.recorder.stop()
            except Exception:  # pragma: no cover
                pass
        self.worker.stop()
        self.db.close()

    # ---------- settings ----------
    def update_settings(self, patch: SettingsPatch | dict):
        merged = self.settings.update(patch)
        self.transcriber.reconfigure(size=merged.model_size, device=merged.device, compute_type=merged.compute_type)
        return merged

    def preload_model(self) -> None:
        self.worker.submit("load model", self.transcriber.ensure_loaded)

    # ---------- status ----------
    def status(self) -> dict:
        s = self.settings.get()
        try:
            free = shutil.disk_usage(self.config.data_dir).free
        except OSError:
            free = None
        return {
            "service": SERVICE,
            "version": __version__,
            "backend": self.backend.name,
            "backend_notes": self.notes,
            "devices": self.backend.devices().as_dict(),
            "transcriber": self.transcriber.info(),
            "whisper_installed": whisper_available(),
            "vad": "webrtcvad" if HAVE_WEBRTCVAD else "energy",
            "ffmpeg": ffmpeg_path(),
            "pyav": pyav_available(),
            "recording": self.recorder.status(),
            "queue_depth": self.worker.depth,
            "worker_busy": self.worker.busy,
            "last_error": self.recorder.last_error or self.worker.last_error,
            "sessions_total": self.sessions.count(),
            "storage_bytes": self.sessions.storage_bytes(),
            "disk_free_bytes": free,
            "data_dir": str(self.config.data_dir),
            "models_dir": str(self.config.models_dir),
            "settings": s.model_dump(),
        }
