"""Transcriber interface: mono int16/float audio in, timed segments out."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class Word:
    start: float
    end: float
    word: str
    probability: float = 0.0


@dataclass
class Segment:
    start: float
    end: float
    text: str
    confidence: float = 0.0
    words: list[Word] = field(default_factory=list)
    # decoder quality signals (faster-whisper exposes them per segment); None when the backend has none
    no_speech_prob: float | None = None
    avg_logprob: float | None = None
    compression_ratio: float | None = None


class Transcriber:
    name = "base"

    def transcribe(self, samples: np.ndarray, language: str | None = None) -> list[Segment]:  # pragma: no cover - interface
        raise NotImplementedError

    def info(self) -> dict:
        return {"name": self.name}

    def ensure_loaded(self) -> None:
        return None

    def reconfigure(self, **_: object) -> None:
        return None
