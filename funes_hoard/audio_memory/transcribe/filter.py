"""Silence and hallucination handling shared by the live chunks and the final pass.

Whisper models invent text on silence or noise ("Subtítulos realizados por la comunidad de
Amara.org", "Thank you for watching"...). Three guards, applied in this order:

1. `speech_stats`: RMS/peak energy plus the VAD's voiced-frame ratio; audio with no speech is never
   sent to the model (`skipped_silent`).
2. Decoder signals per segment: `no_speech_prob` > NO_SPEECH_MAX, `avg_logprob` < LOGPROB_MIN or
   `compression_ratio` > COMPRESSION_MAX drop the segment.
3. The family's hallucination filter (`hoard_link.media.stt.clean_segments`): a deny-list of well-known
   hallucinated phrases, loops of repeated words collapsed and near-identical neighbours merged.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np

from funes_hoard.hoard_link.media.stt import COMPRESSION_MAX, LOGPROB_MIN, NO_SPEECH_MAX, CleanStats, clean_segments

from ..audio.wav import SAMPLE_RATE, to_float32
from ..vad import EnergyDetector, HAVE_WEBRTCVAD, WebrtcDetector
from .base import Segment, Transcriber

PEAK_MIN = 0.01  # below this the block is digital silence / mic noise floor
RMS_MIN = 0.0015
SPEECH_RATIO_MIN = 0.03  # voiced frames / total frames
SPEECH_MIN_MS = 240  # at least this much voiced audio, whatever the ratio
FRAME_MS = 30


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
    removed_duplicate: int = 0
    loops_collapsed: int = 0
    dropped_texts: list[str] = field(default_factory=list)

    def add(self, other: "FilterStats") -> "FilterStats":
        for key in ("chunks", "skipped_silent", "kept", "dropped_no_speech", "dropped_logprob", "dropped_compression", "dropped_denylist",
                    "removed_duplicate", "loops_collapsed"):
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
    """The family's hallucination filter (``clean_segments``) over Funes's segments: decoder doubts, known artifacts, word loops and
    near-identical neighbours. The counters accumulate on ``stats``."""
    stats = stats if stats is not None else FilterStats()
    raw = [{"start_s": s.start, "end_s": s.end, "text": s.text, "no_speech_prob": s.no_speech_prob, "avg_logprob": s.avg_logprob,
            "compression_ratio": s.compression_ratio, "_i": i} for i, s in enumerate(segments)]
    clean = CleanStats()
    kept_raw = clean_segments(raw, stats=clean)
    kept: list[Segment] = []
    for item in kept_raw:
        original = segments[item["_i"]]
        words = original.words if item["text"] == original.text.strip() else []  # a collapsed loop no longer matches its words
        kept.append(Segment(original.start, item["end_s"], item["text"], original.confidence, words, no_speech_prob=original.no_speech_prob,
                            avg_logprob=original.avg_logprob, compression_ratio=original.compression_ratio))
    stats.kept += len(kept)
    stats.dropped_no_speech += clean.dropped_no_speech
    stats.dropped_logprob += clean.dropped_logprob
    stats.dropped_compression += clean.dropped_compression
    stats.dropped_denylist += clean.dropped_hallucination
    stats.removed_duplicate += clean.removed_duplicate
    stats.loops_collapsed += clean.loops_collapsed
    stats.dropped_texts = (stats.dropped_texts + clean.dropped_texts)[-20:]
    return kept


def guarded_transcribe(transcriber: Transcriber, samples: np.ndarray, language: str | None, sensitivity: int = 2, stats: FilterStats | None = None) -> list[Segment]:
    """The one entry point both the live chunks and the final pass use: silence gate → model → filters."""
    stats = stats if stats is not None else FilterStats()
    stats.chunks += 1
    if not speech_stats(samples, sensitivity).has_speech:
        stats.skipped_silent += 1
        return []
    return filter_segments(transcriber.transcribe(samples, language), stats)
