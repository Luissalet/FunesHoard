"""Silence and hallucination handling shared by the live chunks and the final pass.

Whisper models invent text on silence or noise ("Subtítulos realizados por la comunidad de
Amara.org", "Thank you for watching"...). Three guards, applied in this order:

1. `speech_stats`: RMS/peak energy plus the VAD's voiced-frame ratio; audio with no speech is never
   sent to the model (`skipped_silent`).
2. Decoder signals per segment: `no_speech_prob` > NO_SPEECH_MAX, `avg_logprob` < LOGPROB_MIN or
   `compression_ratio` > COMPRESSION_MAX drop the segment.
3. A deny-list of well-known hallucinated phrases, matched case- and accent-insensitively as whole
   segments (or as their leading words for the "subtítulos por…" family).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field

import numpy as np

from ..audio.wav import SAMPLE_RATE, to_float32
from ..vad import EnergyDetector, HAVE_WEBRTCVAD, WebrtcDetector
from .base import Segment, Transcriber

NO_SPEECH_MAX = 0.6
LOGPROB_MIN = -1.0
COMPRESSION_MAX = 2.4
PEAK_MIN = 0.01  # below this the block is digital silence / mic noise floor
RMS_MIN = 0.0015
SPEECH_RATIO_MIN = 0.03  # voiced frames / total frames
SPEECH_MIN_MS = 240  # at least this much voiced audio, whatever the ratio
FRAME_MS = 30

HALLUCINATIONS = (
    "subtítulos realizados por la comunidad de amara.org",
    "subtitulado por la comunidad de amara.org",
    "subtítulos por la comunidad de amara.org",
    "subtítulos creados por la comunidad de amara.org",
    "gracias por ver el vídeo",
    "gracias por ver el video",
    "gracias por ver",
    "gracias por su atención",
    "suscríbete",
    "suscríbete al canal",
    "no olvides suscribirte",
    "dale like y suscríbete",
    "hasta la próxima",
    "nos vemos en el próximo vídeo",
    "este es el canal de subtítulos en español",
    "thank you for watching",
    "thanks for watching",
    "thank you so much for watching",
    "please subscribe",
    "like and subscribe",
    "subscribe to my channel",
    "see you in the next video",
    "subtitles by the amara.org community",
    "amara.org",
    "you",
    "so",
)
HALLUCINATION_PREFIXES = (
    "subtítulos realizados por",
    "subtítulos por",
    "subtitulado por",
    "subtítulos creados por",
    "este es el canal de subtítulos",
    "subtitles by",
    "transcribed by",
    "transcripción por",
    "gracias por ver",
    "thank you for watching",
    "thanks for watching",
)


def normalize(text: str) -> str:
    """Lowercase, strip accents and punctuation, collapse whitespace."""
    plain = unicodedata.normalize("NFKD", text.lower())
    plain = "".join(ch for ch in plain if not unicodedata.combining(ch))
    plain = re.sub(r"[^\w\s.]", " ", plain)
    return re.sub(r"\s+", " ", plain).strip(" .")


_DENY = {normalize(p) for p in HALLUCINATIONS}
_DENY_PREFIXES = tuple(normalize(p) for p in HALLUCINATION_PREFIXES)


def is_hallucination(text: str) -> bool:
    plain = normalize(text)
    if not plain:
        return True
    if plain in _DENY:
        return True
    return any(plain.startswith(prefix) for prefix in _DENY_PREFIXES)


@dataclass
class SpeechStats:
    rms: float
    peak: float
    speech_ratio: float
    speech_ms: int
    detector: str

    @property
    def has_speech(self) -> bool:
        if self.peak < PEAK_MIN or self.rms < RMS_MIN:
            return False
        return self.speech_ms >= SPEECH_MIN_MS and self.speech_ratio >= SPEECH_RATIO_MIN


def speech_stats(samples: np.ndarray, sensitivity: int = 2) -> SpeechStats:
    floats = to_float32(samples)
    if len(floats) == 0:
        return SpeechStats(0.0, 0.0, 0.0, 0, "none")
    rms = float(np.sqrt(np.mean(floats * floats)))
    peak = float(np.max(np.abs(floats)))
    detector = WebrtcDetector(sensitivity) if HAVE_WEBRTCVAD else EnergyDetector(sensitivity)
    frame = SAMPLE_RATE * FRAME_MS // 1000
    int16 = np.asarray(samples, dtype=np.int16) if np.asarray(samples).dtype == np.int16 else (np.clip(floats, -1, 1) * 32767).astype(np.int16)
    total = len(int16) // frame
    voiced = sum(1 for i in range(total) if detector.is_speech(int16[i * frame : (i + 1) * frame]))
    return SpeechStats(round(rms, 5), round(peak, 4), round(voiced / total, 3) if total else 0.0, voiced * FRAME_MS, "webrtcvad" if HAVE_WEBRTCVAD else "energy")


@dataclass
class FilterStats:
    """Counters accumulated over a live recording or a final pass; stored on the session."""

    chunks: int = 0
    skipped_silent: int = 0
    kept: int = 0
    dropped_no_speech: int = 0
    dropped_logprob: int = 0
    dropped_compression: int = 0
    dropped_denylist: int = 0
    dropped_texts: list[str] = field(default_factory=list)

    def add(self, other: "FilterStats") -> "FilterStats":
        for key in ("chunks", "skipped_silent", "kept", "dropped_no_speech", "dropped_logprob", "dropped_compression", "dropped_denylist"):
            setattr(self, key, getattr(self, key) + getattr(other, key))
        self.dropped_texts = (self.dropped_texts + other.dropped_texts)[-20:]
        return self

    @property
    def dropped(self) -> int:
        return self.dropped_no_speech + self.dropped_logprob + self.dropped_compression + self.dropped_denylist

    def as_dict(self) -> dict:
        data = asdict(self)
        data["dropped"] = self.dropped
        return data


def filter_segments(segments: list[Segment], stats: FilterStats | None = None) -> list[Segment]:
    stats = stats if stats is not None else FilterStats()
    kept: list[Segment] = []
    for seg in segments:
        text = seg.text.strip()
        if seg.no_speech_prob is not None and seg.no_speech_prob > NO_SPEECH_MAX:
            stats.dropped_no_speech += 1
        elif seg.avg_logprob is not None and seg.avg_logprob < LOGPROB_MIN:
            stats.dropped_logprob += 1
        elif seg.compression_ratio is not None and seg.compression_ratio > COMPRESSION_MAX:
            stats.dropped_compression += 1
        elif is_hallucination(text):
            stats.dropped_denylist += 1
        else:
            kept.append(seg)
            stats.kept += 1
            continue
        stats.dropped_texts = (stats.dropped_texts + [text[:80]])[-20:]
    return kept


def guarded_transcribe(transcriber: Transcriber, samples: np.ndarray, language: str | None, sensitivity: int = 2, stats: FilterStats | None = None) -> list[Segment]:
    """The one entry point both the live chunks and the final pass use: silence gate → model → filters."""
    stats = stats if stats is not None else FilterStats()
    stats.chunks += 1
    if not speech_stats(samples, sensitivity).has_speech:
        stats.skipped_silent += 1
        return []
    return filter_segments(transcriber.transcribe(samples, language), stats)
