"""Final pass over a whole session: transcribe every track, merge by time, replace the live segments."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import numpy as np

from .audio import TRACK_MIC, TRACK_SYSTEM
from .audio.wav import SAMPLE_RATE, mix_tracks, read_wav, write_wav
from .events import EventBus
from .merge import coalesce, merge_tracks
from .settings import SettingsStore
from .store import SessionStore
from .transcribe.base import Transcriber
from .transcribe.filter import FilterStats, guarded_transcribe
from .worker import PRIORITY_FINAL, TranscriptionWorker

log = logging.getLogger("scribe.pipeline")
TRACK_FILES = (TRACK_MIC, TRACK_SYSTEM)


def load_tracks(folder: Path) -> dict[str, np.ndarray]:
    """Per-track audio of a session folder: mic.wav/system.wav, or audio.wav for imports."""
    tracks: dict[str, np.ndarray] = {}
    for name in TRACK_FILES:
        path = folder / f"{name}.wav"
        if path.is_file():
            samples, _ = read_wav(path)
            tracks[name] = samples
    if not tracks and (folder / "audio.wav").is_file():
        samples, _ = read_wav(folder / "audio.wav")
        tracks["audio"] = samples
    return tracks


class Pipeline:
    def __init__(self, store: SessionStore, transcriber: Transcriber, worker: TranscriptionWorker, bus: EventBus, settings: SettingsStore | None = None):
        self.store, self.transcriber, self.worker, self.bus = store, transcriber, worker, bus
        self.settings = settings

    def _sensitivity(self) -> int:
        return self.settings.get().vad_sensitivity if self.settings else 2

    def enqueue_final(self, session_id: str) -> None:
        self.store.update(session_id, status="processing", error="")
        self.worker.submit(f"final {session_id}", lambda: self.run_final(session_id), PRIORITY_FINAL)

    def run_final(self, session_id: str) -> dict | None:
        session = self.store.get(session_id)
        if session is None:
            return None
        folder = self.store.session_dir(session_id)
        started = time.time()
        try:
            tracks = load_tracks(folder)
            if not tracks:
                raise FileNotFoundError("No audio tracks found for this session.")
            stats = FilterStats()
            sensitivity = self._sensitivity()
            per_track = {name: guarded_transcribe(self.transcriber, samples, session["language"], sensitivity, stats) for name, samples in tracks.items()}
            merged = coalesce(merge_tracks(per_track))
            duration = max(len(s) for s in tracks.values()) / SAMPLE_RATE
            audio_path = folder / "audio.wav"
            if len(tracks) > 1:
                write_wav(audio_path, mix_tracks(list(tracks.values())))
            elif "audio" not in tracks:
                audio_path = folder / f"{next(iter(tracks))}.wav"
            stored = self.store.replace_segments(session_id, merged)
            all_stats = {**session.get("stats", {}), "final": stats.as_dict(), "no_speech": not stored}
            updated = self.store.update(session_id, status="done", duration_s=round(duration, 2), audio_path=str(audio_path), error="", ended_at=session["ended_at"] or time.time(), stats=all_stats)
            log.info("final pass %s: %d segments in %.1fs (silent chunks %d, dropped %d)", session_id, len(stored), time.time() - started, stats.skipped_silent, stats.dropped)
            self.bus.publish(session_id, "done", {"status": "done", "segments": len(stored), "duration_s": round(duration, 2), "no_speech": not stored})
            return updated
        except Exception as error:
            log.exception("final pass failed for %s", session_id)
            self.store.update(session_id, status="failed", error=str(error)[:500])
            self.bus.publish(session_id, "done", {"status": "failed", "error": str(error)[:500]})
            raise
