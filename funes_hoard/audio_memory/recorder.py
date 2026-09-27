"""The active recording: capture thread, per-track WAV writers, VAD chunks → live transcription."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .audio import AudioBackend, CaptureStream, TRACK_MIC, TRACK_SYSTEM
from .audio.wav import SAMPLE_RATE, WavWriter, rms_level
from .events import EventBus
from .merge import Labelled, label_for
from .settings import SettingsStore
from .store import SessionStore
from .transcribe.base import Transcriber
from .transcribe.filter import FilterStats, guarded_transcribe
from .vad import Chunk, Chunker
from .worker import PRIORITY_LIVE, TranscriptionWorker

log = logging.getLogger("scribe.recorder")
PAD_THRESHOLD_S = 0.4  # wall-clock gap that gets filled with silence (loopback delivers nothing when idle)


class TrackWriter:
    def __init__(self, path: Path, chunker: Chunker, t0: float):
        self.wav = WavWriter(path)
        self.chunker = chunker
        self.t0 = t0
        self.level = 0.0

    def on_block(self, samples: np.ndarray, now: float) -> list[Chunk]:
        expected_start = int((now - self.t0) * SAMPLE_RATE) - len(samples)
        gap = expected_start - self.wav.frames
        chunks: list[Chunk] = []
        if gap > SAMPLE_RATE * PAD_THRESHOLD_S:
            silence = np.zeros(gap, dtype=np.int16)
            self.wav.write(silence)
            chunks += self.chunker.feed(silence)
        self.wav.write(samples)
        self.level = rms_level(samples)
        chunks += self.chunker.feed(samples)
        return chunks

    def finish(self) -> list[Chunk]:
        chunks = self.chunker.flush()
        self.wav.close()
        return chunks


@dataclass
class ActiveRecording:
    session_id: str
    stream: CaptureStream
    tracks: dict[str, TrackWriter]
    language: str
    started_mono: float = field(default_factory=time.monotonic)
    stop_event: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    chunks_sent: int = 0
    live_segments: int = 0
    sensitivity: int = 2
    stats: FilterStats = field(default_factory=FilterStats)

    @property
    def elapsed(self) -> float:
        return max((w.wav.seconds for w in self.tracks.values()), default=0.0)

    @property
    def levels(self) -> dict[str, float]:
        return {name: round(w.level, 3) for name, w in self.tracks.items()}


class Recorder:
    def __init__(self, backend: AudioBackend, settings: SettingsStore, store: SessionStore, transcriber: Transcriber, worker: TranscriptionWorker, bus: EventBus):
        self.backend, self.settings, self.store = backend, settings, store
        self.transcriber, self.worker, self.bus = transcriber, worker, bus
        self.current: ActiveRecording | None = None
        self.last_error = ""
        self._lock = threading.Lock()
        self.on_stopped = None  # callback(session_id) set by Services: enqueue the final pass

    # ---------- control ----------
    def start(self, title: str, kind: str, mic: bool, system: bool, language: str) -> dict:
        with self._lock:
            if self.current is not None:
                raise ValueError("A recording is already in progress; stop it first.")
            if not mic and not system:
                raise ValueError("Select at least one source (mic or system).")
            stream = self.backend.open(mic=mic, system=system)  # raises when the backend cannot capture
            session = self.store.create(title, kind, mic=TRACK_MIC in stream.tracks, system=TRACK_SYSTEM in stream.tracks, language=language)
            folder = self.store.session_dir(session["id"])
            s = self.settings.get()
            t0 = time.monotonic()
            tracks = {
                name: TrackWriter(folder / f"{name}.wav", Chunker(sensitivity=s.vad_sensitivity, max_chunk_s=float(s.live_chunk_max_s)), t0)
                for name in stream.tracks
            }
            rec = ActiveRecording(session["id"], stream, tracks, language, started_mono=t0, sensitivity=s.vad_sensitivity)
            rec.thread = threading.Thread(target=self._capture_loop, args=(rec,), name="scribe-capture", daemon=True)
            self.current = rec
            rec.thread.start()
            self.last_error = ""
            log.info("recording %s started (%s)", session["id"], ", ".join(stream.tracks))
            return session

    def stop(self, session_id: str | None = None) -> dict:
        with self._lock:
            rec = self.current
            if rec is None:
                raise LookupError("No recording in progress.")
            if session_id and session_id != rec.session_id:
                raise LookupError(f"The active recording is {rec.session_id}, not {session_id}.")
            rec.stop_event.set()
            rec.stream.close()
            self.current = None
        if rec.thread:
            rec.thread.join(timeout=5)
        duration = rec.elapsed
        audio = self._single_audio(rec)
        session = self.store.update(rec.session_id, status="processing", ended_at=time.time(), duration_s=duration, audio_path=audio, stats={"live": rec.stats.as_dict()})
        self.bus.publish(rec.session_id, "status", {"status": "processing", "duration_s": duration})
        if self.on_stopped:
            self.on_stopped(rec.session_id)
        return session

    def _single_audio(self, rec: ActiveRecording) -> str:
        if len(rec.tracks) == 1:
            return str(next(iter(rec.tracks.values())).wav.path)
        return ""  # the final pass mixes the tracks into audio.wav

    # ---------- capture ----------
    def _capture_loop(self, rec: ActiveRecording) -> None:
        live = self.settings.get().live_transcription
        try:
            while not rec.stop_event.is_set():
                block = rec.stream.read(timeout=0.3)
                if rec.stream.error and rec.stream.error != self.last_error:
                    self.last_error = rec.stream.error
                    log.error("capture: %s", rec.stream.error)
                if block is None:
                    if rec.stream.closed and rec.stream.queue.empty():
                        time.sleep(0.1)  # fixture ended: wait for stop()
                    continue
                writer = rec.tracks.get(block.track)
                if writer is None or len(block.samples) == 0:
                    continue
                for chunk in writer.on_block(block.samples, time.monotonic()):
                    if live:
                        self._submit_chunk(rec, block.track, chunk)
        except Exception as error:  # pragma: no cover - device failure
            self.last_error = str(error)
            log.exception("capture loop failed")
        finally:
            for track, writer in rec.tracks.items():
                for chunk in writer.finish():
                    if live and chunk.duration >= 1.0:
                        self._submit_chunk(rec, track, chunk)

    def _submit_chunk(self, rec: ActiveRecording, track: str, chunk: Chunk) -> None:
        rec.chunks_sent += 1
        label = label_for(track, len(rec.tracks))

        def job():
            segments = guarded_transcribe(self.transcriber, chunk.samples, rec.language, rec.sensitivity, rec.stats)
            labelled = [Labelled(round(chunk.start_s + s.start, 2), round(chunk.start_s + s.end, 2), label, s.text.strip(), s.confidence) for s in segments if s.text.strip()]
            if not labelled:
                return
            stored = self.store.add_segments(rec.session_id, labelled, live=True)
            rec.live_segments += len(stored)
            for seg in stored:
                self.bus.publish(rec.session_id, "segment", seg)

        self.worker.submit(f"live {rec.session_id} {track} {chunk.start_s:.1f}s", job, PRIORITY_LIVE)

    # ---------- status ----------
    def status(self) -> dict | None:
        rec = self.current
        if rec is None:
            return None
        session = self.store.get(rec.session_id) or {}
        return {
            "session_id": rec.session_id,
            "title": session.get("title", ""),
            "kind": session.get("kind", "other"),
            "elapsed_s": round(rec.elapsed, 1),
            "tracks": list(rec.tracks),
            "levels": rec.levels,
            "chunks_sent": rec.chunks_sent,
            "live_segments": rec.live_segments,
            "skipped_silent": rec.stats.skipped_silent,
            "dropped": rec.stats.dropped,
        }
