"""Wiring of database, stores, backends, recorder, worker and pipeline."""

from __future__ import annotations

import logging
import shutil
import time

from . import SERVICE, __version__
from .audio import select_backend
from .config import Config
from .db import Database
from .events import EventBus
from .importer import Importer, ffmpeg_path, pyav_available
from .minutes import MinutesService
from .pipeline import Pipeline
from .recorder import Recorder
from .settings import SettingsPatch, SettingsStore
from .store import SessionStore
from funes_hoard.hoard_link.media import stt
from funes_hoard.hoard_link.tokens import read_or_create_token

from .stt_jobs import SttJobs
from .transcribe import select_transcriber, whisper_available
from .transcribe.fake import FakeWhisperModel
from .transcribe.whisper import WhisperTranscriber
from .vad import HAVE_WEBRTCVAD
from funes_hoard.hoard_link import family
from .worker import TranscriptionWorker

log = logging.getLogger("scribe")


def write_token(config: Config) -> str:
    """The access token is created once and kept: a restart must not lock out a bridge that is already connected."""
    config.data_dir.mkdir(parents=True, exist_ok=True)
    return read_or_create_token(config.token_path)


class Services:
    def __init__(self, config: Config, link_provider=None, emit=None):
        """`link_provider` returns the host app's Hoard Link (None: no minutes, `no_model`); `emit` posts family events."""
        self.config = config
        self.started_at = time.time()
        config.data_dir.mkdir(parents=True, exist_ok=True)
        config.sessions_dir.mkdir(parents=True, exist_ok=True)
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
        self._fake_engine: stt.Transcriber | None = None
        self.stt = SttJobs(self.worker, self._stt_engine, self._stt_engine_for, self._stt_available)
        self.pipeline = Pipeline(self.sessions, self.transcriber, self.worker, self.bus, self.settings)
        self.importer = Importer(self.sessions, self.pipeline)
        self.recorder = Recorder(self.backend, self.settings, self.sessions, self.transcriber, self.worker, self.bus)
        self.recorder.on_stopped = self.pipeline.enqueue_final
        self.minutes = MinutesService(self.db, self.sessions, self.settings, link_provider, emit=emit if emit is not None else family.emit)
        self.pipeline.on_done = self._transcribed

    # ---------- the family's speech-to-text service ----------
    def _fake_stt_engine(self) -> stt.Transcriber:
        if self._fake_engine is None:
            self._fake_engine = stt.Transcriber(self.config.models_dir, "fake", "cpu", "int8", lease=False, owner="funes",
                                                model_factory=lambda *a, **k: FakeWhisperModel())
        return self._fake_engine

    def _stt_engine(self) -> stt.Transcriber:
        """The sessions' own engine, so there is one model in memory; the fake one when sessions use the fake transcriber."""
        return self.transcriber.engine if isinstance(self.transcriber, WhisperTranscriber) else self._fake_stt_engine()

    def _stt_engine_for(self, size: str) -> stt.Transcriber:
        if not isinstance(self.transcriber, WhisperTranscriber):
            return self._fake_stt_engine()
        s = self.settings.get()
        return stt.Transcriber(self.config.models_dir, size, s.device, s.compute_type, owner="funes")

    def _stt_available(self) -> bool:
        return not isinstance(self.transcriber, WhisperTranscriber) or stt.available()

    def _transcribed(self, session_id: str) -> None:
        """A final pass finished: the old minutes (if any) describe replaced text, so write new ones when wanted."""
        self.minutes.discard(session_id)
        self.minutes.after_transcription(session_id)

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
        self.minutes.close()
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
