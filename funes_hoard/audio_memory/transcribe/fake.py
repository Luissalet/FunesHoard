"""Deterministic transcriber for tests: fictional Spanish phrases, one segment per ~2 s of speech."""

from __future__ import annotations

import numpy as np

from ..audio.wav import SAMPLE_RATE
from .base import Segment, Transcriber

DEFAULT_PHRASES = [
    "Hola, empezamos la reunión de prueba del proyecto Ficticio.",
    "El presupuesto propuesto son cien monedas de oro al mes.",
    "Sobre el salario, la banda estaría entre ochenta y noventa.",
    "Quedamos en revisar los minutos la semana que viene.",
    "Gracias a todos, cerramos la sesión.",
]


class FakeTranscriber(Transcriber):
    name = "fake"

    def __init__(self, phrases: list[str] | None = None, segment_s: float = 2.0):
        self.phrases = list(phrases or DEFAULT_PHRASES)
        self.segment_s = segment_s
        self.calls = 0
        self._cursor = 0

    def transcribe(self, samples: np.ndarray, language: str | None = None) -> list[Segment]:
        self.calls += 1
        duration = len(samples) / SAMPLE_RATE
        if duration <= 0:
            return []
        segments: list[Segment] = []
        start = 0.0
        while start < duration:
            end = min(duration, start + self.segment_s)
            if end - start < 0.2 and segments:
                segments[-1].end = end
                break
            text = self.phrases[self._cursor % len(self.phrases)]
            self._cursor += 1
            segments.append(Segment(round(start, 2), round(end, 2), text, 0.9, no_speech_prob=0.05, avg_logprob=-0.3, compression_ratio=1.2))
            start = end
        return segments

    def info(self) -> dict:
        return {"name": self.name, "model": "fake", "device": "cpu", "compute_type": "none", "loaded": True, "download": "ready", "gpu_lease": "disabled"}
