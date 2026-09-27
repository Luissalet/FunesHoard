"""Transcriber selection. Never crashes on import: faster-whisper is imported lazily."""

from __future__ import annotations

from pathlib import Path

from .base import Segment, Transcriber, Word
from .fake import FakeTranscriber

__all__ = ["FakeTranscriber", "Segment", "Transcriber", "Word", "select_transcriber", "whisper_available"]


def whisper_available() -> bool:
    try:
        import faster_whisper  # noqa: F401

        return True
    except Exception:
        return False


def select_transcriber(kind: str, models_dir: Path, size: str, device: str, compute_type: str) -> tuple[Transcriber, list[str]]:
    notes: list[str] = []
    if kind == "fake":
        return FakeTranscriber(), notes
    if whisper_available():
        from .whisper import WhisperTranscriber

        return WhisperTranscriber(models_dir, size, device, compute_type), notes
    notes.append("faster-whisper is not installed; using the fake transcriber (pip install faster-whisper)")
    return FakeTranscriber(), notes
