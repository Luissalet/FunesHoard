"""Voice-activity chunking of a live track: webrtcvad when available, energy-based otherwise.

Frames (10/20/30 ms) go in; `Chunk`s of speech with absolute start/end seconds come out, so the
live transcriber gets short utterances instead of a 20-minute buffer.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .audio.wav import SAMPLE_RATE, to_float32

try:  # optional dependency (webrtcvad-wheels)
    import webrtcvad  # type: ignore

    HAVE_WEBRTCVAD = True
except Exception:  # pragma: no cover - depends on the wheel
    webrtcvad = None
    HAVE_WEBRTCVAD = False


@dataclass
class Chunk:
    start_s: float
    end_s: float
    samples: np.ndarray  # mono int16

    @property
    def duration(self) -> float:
        return self.end_s - self.start_s


class EnergyDetector:
    """Adaptive RMS gate: speech when energy exceeds noise floor by a margin (per sensitivity)."""

    def __init__(self, sensitivity: int):
        self.margin = {0: 2.0, 1: 3.0, 2: 4.5, 3: 6.0}.get(sensitivity, 4.5)
        self.floor = 0.004
        self.absolute = 0.01

    def is_speech(self, frame: np.ndarray) -> bool:
        floats = to_float32(frame)
        rms = float(np.sqrt(np.mean(floats * floats))) if len(floats) else 0.0
        speech = rms > max(self.absolute, self.floor * self.margin)
        if not speech:
            # noise floor: drops quickly, rises very slowly and never above a sane ceiling,
            # so syllable dips inside speech cannot drag the threshold up to speech level
            rate = 0.1 if rms < self.floor else 0.002
            self.floor = min(0.03, (1 - rate) * self.floor + rate * rms)
        return speech


class WebrtcDetector:
    def __init__(self, sensitivity: int):
        self.vad = webrtcvad.Vad(max(0, min(3, sensitivity)))

    def is_speech(self, frame: np.ndarray) -> bool:
        return self.vad.is_speech(np.asarray(frame, dtype=np.int16).tobytes(), SAMPLE_RATE)


@dataclass
class Chunker:
    """Feed 30 ms frames (or any block; it is re-framed) and collect chunks."""

    sensitivity: int = 2
    frame_ms: int = 30
    min_speech_ms: int = 240
    silence_ms: int = 700
    max_chunk_s: float = 15.0
    pad_ms: int = 150
    use_webrtc: bool | None = None
    _detector: object = field(init=False, repr=False)
    _pending: np.ndarray = field(init=False, repr=False)
    _frames_seen: int = field(default=0, init=False)
    _speech: list[np.ndarray] = field(default_factory=list, init=False)
    _speech_start: int = field(default=-1, init=False)  # frame index
    _voiced: int = field(default=0, init=False)
    _silent: int = field(default=0, init=False)
    _tail: list[np.ndarray] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        webrtc = HAVE_WEBRTCVAD if self.use_webrtc is None else (self.use_webrtc and HAVE_WEBRTCVAD)
        self._detector = WebrtcDetector(self.sensitivity) if webrtc else EnergyDetector(self.sensitivity)
        self._pending = np.zeros(0, dtype=np.int16)

    @property
    def detector_name(self) -> str:
        return "webrtcvad" if isinstance(self._detector, WebrtcDetector) else "energy"

    @property
    def frame_len(self) -> int:
        return SAMPLE_RATE * self.frame_ms // 1000

    def feed(self, samples: np.ndarray) -> list[Chunk]:
        """Append a block; returns any chunks that closed with it."""
        out: list[Chunk] = []
        data = np.concatenate([self._pending, np.asarray(samples, dtype=np.int16)])
        n = self.frame_len
        cut = (len(data) // n) * n
        for offset in range(0, cut, n):
            chunk = self._frame(data[offset : offset + n])
            if chunk:
                out.append(chunk)
        self._pending = data[cut:]
        return out

    def flush(self) -> list[Chunk]:
        """End of stream: close an open utterance."""
        chunk = self._close() if self._speech_start >= 0 else None
        self._tail.clear()
        return [chunk] if chunk else []

    # ---------- internals ----------
    def _frame(self, frame: np.ndarray) -> Chunk | None:
        index = self._frames_seen
        self._frames_seen += 1
        speech = self._detector.is_speech(frame)
        keep = max(1, self.pad_ms // self.frame_ms) + max(1, self.min_speech_ms // self.frame_ms)
        if self._speech_start < 0:
            self._tail.append(frame)
            if len(self._tail) > keep:
                self._tail.pop(0)
            if speech:
                self._voiced += 1
                if self._voiced * self.frame_ms >= self.min_speech_ms:
                    # utterance confirmed: start it with the padded tail
                    self._speech_start = index - len(self._tail) + 1
                    self._speech = list(self._tail)
                    self._tail = []
                    self._silent = 0
            else:
                self._voiced = 0
            return None
        self._speech.append(frame)
        if speech:
            self._silent = 0
        else:
            self._silent += 1
        long_enough = (index - self._speech_start + 1) * self.frame_ms / 1000 >= self.max_chunk_s
        if self._silent * self.frame_ms >= self.silence_ms or long_enough:
            return self._close()
        return None

    def _close(self) -> Chunk | None:
        if self._speech_start < 0:
            return None
        samples = np.concatenate(self._speech) if self._speech else np.zeros(0, dtype=np.int16)
        start = self._speech_start * self.frame_ms / 1000
        end = start + len(samples) / SAMPLE_RATE
        self._speech_start, self._speech, self._voiced, self._silent = -1, [], 0, 0
        if len(samples) / SAMPLE_RATE * 1000 < self.min_speech_ms:
            return None
        return Chunk(round(start, 3), round(end, 3), samples)


def split_track(samples: np.ndarray, sensitivity: int = 2, max_chunk_s: float = 15.0, use_webrtc: bool | None = None) -> list[Chunk]:
    """Chunk a whole in-memory track (convenience for tests and the selftest)."""
    chunker = Chunker(sensitivity=sensitivity, max_chunk_s=max_chunk_s, use_webrtc=use_webrtc)
    chunks = chunker.feed(samples)
    chunks.extend(chunker.flush())
    return chunks
