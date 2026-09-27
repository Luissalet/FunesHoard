"""Fake backend: streams WAV fixtures as if they were live devices (tests, selftest --fake)."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import numpy as np

from .base import TRACK_MIC, TRACK_SYSTEM, AudioBackend, Block, CaptureStream, Device, DeviceList
from .wav import SAMPLE_RATE, read_wav


def synth_speechlike(seconds: float, sample_rate: int = SAMPLE_RATE, seed: int = 1, pattern: list[tuple[float, float]] | None = None) -> np.ndarray:
    """Deterministic 'speech-like' signal: bursts of modulated harmonics separated by silence.

    `pattern` is a list of (start_s, end_s) speech intervals; default = 1 s on / 0.7 s off.
    """
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * sample_rate)) / sample_rate
    signal = np.zeros_like(t, dtype=np.float32)
    if pattern is None:
        pattern, cursor = [], 0.3
        while cursor < seconds:
            pattern.append((cursor, min(seconds, cursor + 1.0)))
            cursor += 2.0
    for start, end in pattern:
        lo, hi = int(start * sample_rate), int(min(end, seconds) * sample_rate)
        if hi <= lo:
            continue
        seg_t = t[lo:hi] - t[lo]
        pitch = 110 + 40 * rng.random()
        voice = np.zeros_like(seg_t)
        for harmonic in range(1, 6):
            voice += np.sin(2 * np.pi * pitch * harmonic * seg_t) / harmonic
        envelope = 0.5 + 0.5 * np.sin(2 * np.pi * 4 * seg_t)  # syllable-ish modulation
        voice = voice * envelope * 0.3 + rng.normal(0, 0.01, len(seg_t))
        signal[lo:hi] = voice
    return (np.clip(signal, -1, 1) * 32767).astype(np.int16)


class FakeBackend(AudioBackend):
    name = "fake"

    def __init__(self, fixture: str = "", speed: float = 1.0):
        """`fixture`: path to a WAV, or "mic.wav,system.wav". Empty = synthetic 8 s signal."""
        self.speed = speed
        self.sources: dict[str, Path | None] = {TRACK_MIC: None, TRACK_SYSTEM: None}
        parts = [p.strip() for p in fixture.split(",") if p.strip()]
        if len(parts) >= 1:
            self.sources[TRACK_MIC] = Path(parts[0])
        if len(parts) >= 2:
            self.sources[TRACK_SYSTEM] = Path(parts[1])
        elif len(parts) == 1:
            self.sources[TRACK_SYSTEM] = Path(parts[0])

    def devices(self) -> DeviceList:
        return DeviceList(
            mic=[Device("fake-mic", "Micrófono simulado", "mic", True)],
            system=[Device("fake-loopback", "Altavoces simulados (loopback)", "system", True)],
            notes=["fake backend: streams a WAV fixture"],
        )

    def _samples(self, track: str) -> np.ndarray:
        path = self.sources.get(track)
        if path and path.is_file():
            data, _ = read_wav(path)
            return data
        return synth_speechlike(8.0, seed=1 if track == TRACK_MIC else 2)

    def open(self, mic: bool, system: bool, block_ms: int = 30) -> CaptureStream:
        tracks = [name for name, enabled in ((TRACK_MIC, mic), (TRACK_SYSTEM, system)) if enabled]
        if not tracks:
            raise ValueError("At least one source (mic or system) must be enabled.")
        stream = CaptureStream(tracks)
        data = {track: self._samples(track) for track in tracks}
        block = int(SAMPLE_RATE * block_ms / 1000)
        thread = threading.Thread(target=self._pump, args=(stream, data, block), name="fake-audio", daemon=True)
        thread.start()
        return stream

    def _pump(self, stream: CaptureStream, data: dict[str, np.ndarray], block: int) -> None:
        longest = max(len(d) for d in data.values())
        offset = 0
        started = time.monotonic()
        while offset < longest and not stream.closed:
            last = offset + block >= longest
            for track, samples in data.items():
                chunk = samples[offset : offset + block]
                if len(chunk) < block:
                    chunk = np.concatenate([chunk, np.zeros(block - len(chunk), dtype=np.int16)])
                stream.push(Block(track, chunk, ended=last))
            offset += block
            if self.speed > 0:
                due = started + (offset / SAMPLE_RATE) / self.speed
                delay = due - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
        stream.close()
