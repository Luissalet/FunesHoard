"""faster-whisper transcriber for the sessions: Funes's segments on top of the family's shared engine.

The model loading, the GPU lease, the CPU fallback and the decoding live in ``hoard_link.media.stt.Transcriber`` (one place for the
whole family). This module keeps what is Funes's: ``Segment`` objects with a confidence, the status dictionary the UI reads and the
model folder (``<DATA_DIR>/models``, or the family's ``$HOARD_HOME/models/whisper`` when nothing was downloaded here yet).
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Callable

import numpy as np

from funes_hoard.hoard_link.media import stt

from .base import Segment, Transcriber, Word

model_present = stt.model_present
model_repo = stt.model_repo
whisper_vram_mb = stt.whisper_vram_mb
lease_timeout_s = stt.lease_timeout_s
gpu_leasing_enabled = stt.gpu_leasing_enabled
cuda_available = stt.cuda_available


def default_models_dir(data_dir: Path) -> Path:
    """Where the Whisper models live: ``<DATA_DIR>/models`` while that folder already holds a downloaded model (so nothing is
    downloaded twice), otherwise the family's shared ``$HOARD_HOME/models/whisper``."""
    legacy = Path(data_dir) / "models"
    if legacy.is_dir() and any(legacy.glob("models--*/snapshots/*/model.bin")):
        return legacy
    return stt.default_models_dir()


def _segment(raw: dict[str, Any]) -> Segment:
    avg = raw.get("avg_logprob")
    return Segment(
        float(raw["start_s"]), float(raw["end_s"]), str(raw["text"]).strip(),
        round(math.exp(avg), 3) if avg is not None else 0.0,
        [Word(float(w["start_s"]), float(w["end_s"]), str(w["word"]), float(w.get("p") or 0.0)) for w in raw.get("words") or []],
        no_speech_prob=raw.get("no_speech_prob"), avg_logprob=avg, compression_ratio=raw.get("compression_ratio"),
    )


class WhisperTranscriber(Transcriber):
    """The session transcriber. ``engine`` is the shared ``Transcriber``: the family tools (``transcribe_file``) use the very same
    instance, so there is one model in memory."""

    name = "faster-whisper"

    def __init__(self, models_dir: Path, size: str = "small", device: str = "auto", compute_type: str = "auto", *,
                 model_factory: Callable[..., Any] | None = None, lease_factory: Callable[..., Any] | None = None):
        self.engine = stt.Transcriber(models_dir, size, device, compute_type, owner="funes", model_factory=model_factory,
                                      lease_factory=lease_factory)

    # ---------- state the rest of the app reads ----------
    models_dir = property(lambda self: self.engine.models_dir)
    size = property(lambda self: self.engine.size)
    state = property(lambda self: self.engine.state)
    error = property(lambda self: self.engine.error)
    last_ms = property(lambda self: self.engine.last_ms)
    cpu_fallback_reason = property(lambda self: self.engine.cpu_fallback_reason)
    loaded_key = property(lambda self: self.engine.loaded_key)
    model = property(lambda self: self.engine.model)

    def reconfigure(self, size: str | None = None, device: str | None = None, compute_type: str | None = None, **_: object) -> None:
        self.engine.reconfigure(size=size, device=device, compute=compute_type)

    def ensure_loaded(self) -> None:
        self.engine.ensure_loaded()

    # ---------- work ----------
    def transcribe(self, samples: np.ndarray, language: str | None = None) -> list[Segment]:
        """Raw segments with the decoder's quality signals; the hallucination filter runs afterwards (``filter.py``) so its counters
        are kept on the session."""
        result = self.engine.transcribe(samples, language=language, word_timestamps=True, vad=True, beam_size=2, clean=False)
        return [_segment(s) for s in result.segments]

    def info(self) -> dict:
        info = self.engine.info()
        info["compute_type"] = info.pop("compute")
        info["models_dir"] = str(self.engine.models_dir)
        return info
