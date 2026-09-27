"""Import an existing audio/video file as a session: convert to 16 kHz mono WAV, then transcribe in the background."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .audio.wav import SAMPLE_RATE, read_wav, write_wav
from .pipeline import Pipeline
from .store import SessionStore

ALLOWED_EXT = {".wav", ".mp3", ".m4a", ".ogg", ".oga", ".opus", ".flac", ".mp4", ".mkv", ".webm", ".aac", ".wma", ".mov"}
MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024


def ffmpeg_path() -> str | None:
    return shutil.which("ffmpeg")


def pyav_available() -> bool:
    try:
        import av  # noqa: F401  (ships with faster-whisper)

        return True
    except Exception:
        return False


def convert_to_wav(source: Path, target: Path) -> str:
    """Return the converter used: ffmpeg | pyav | wav."""
    ffmpeg = ffmpeg_path()
    if ffmpeg:
        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(source), "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "wav", str(target)]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if result.returncode != 0:
            raise ValueError(f"ffmpeg could not decode the file: {result.stderr.strip()[:300]}")
        return "ffmpeg"
    if pyav_available():
        from faster_whisper.audio import decode_audio

        samples = decode_audio(str(source), sampling_rate=SAMPLE_RATE)
        write_wav(target, samples)
        return "pyav"
    if source.suffix.lower() == ".wav":
        samples, _ = read_wav(source)
        write_wav(target, samples)
        return "wav"
    raise ValueError("Only WAV files can be imported without ffmpeg on PATH.")


class Importer:
    def __init__(self, store: SessionStore, pipeline: Pipeline):
        self.store, self.pipeline = store, pipeline

    def import_file(self, source: Path, filename: str, title: str = "", kind: str = "other", language: str = "auto") -> dict:
        ext = Path(filename).suffix.lower()
        if ext not in ALLOWED_EXT:
            raise ValueError(f"Unsupported file type: {ext or '(none)'}")
        session = self.store.create(title or Path(filename).stem, kind, mic=True, system=False, language=language, origin="import", status="processing")
        folder = self.store.session_dir(session["id"])
        original = folder / f"original{ext}"
        shutil.move(str(source), original)
        try:
            convert_to_wav(original, folder / "audio.wav")
        except Exception as error:
            self.store.update(session["id"], status="failed", error=str(error)[:500])
            raise ValueError(str(error)) from error
        samples, _ = read_wav(folder / "audio.wav")
        session = self.store.update(session["id"], audio_path=str(folder / "audio.wav"), duration_s=round(len(samples) / SAMPLE_RATE, 2))
        self.pipeline.enqueue_final(session["id"])
        return self.store.get(session["id"])
