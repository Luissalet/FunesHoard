"""Import an existing audio/video file as a session: convert to 16 kHz mono WAV, then transcribe in the background."""

from __future__ import annotations

import shutil
from pathlib import Path

from funes_hoard.hoard_link.media import bins
from funes_hoard.hoard_link.media.ffmpeg import FFmpeg, FFmpegError
from funes_hoard.hoard_link.errors import HoardLinkError

from .audio.wav import SAMPLE_RATE, read_wav, write_wav
from .pipeline import Pipeline
from .store import SessionStore

ALLOWED_EXT = {".wav", ".mp3", ".m4a", ".ogg", ".oga", ".opus", ".flac", ".mp4", ".mkv", ".webm", ".aac", ".wma", ".mov"}
MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024


def _ffmpeg() -> FFmpeg:
    return FFmpeg()


def _has_ffmpeg(ffmpeg: FFmpeg) -> bool:
    """ffmpeg itself is enough to convert (ffprobe is only needed to measure a duration)."""
    try:
        ffmpeg.ffmpeg  # noqa: B018 - raises when the program cannot be found
    except HoardLinkError:
        return False
    return True


def ffmpeg_path() -> str | None:
    """Where ffmpeg is: `FUNES_FFMPEG` / `HOARD_FFMPEG`, `$HOARD_HOME/bin`, PATH and the usual install folders (the family's lookup)."""
    tool = bins.find("ffmpeg", legacy_env=("FUNES_FFMPEG", "SCRIBE_FFMPEG"))
    return tool.path if tool else None


def pyav_available() -> bool:
    try:
        import av  # noqa: F401  (ships with faster-whisper)

        return True
    except Exception:
        return False


def convert_to_wav(source: Path, target: Path, ffmpeg: FFmpeg | None = None) -> str:
    """Return the converter used: ffmpeg | pyav | wav."""
    ffmpeg = ffmpeg or _ffmpeg()
    if _has_ffmpeg(ffmpeg):
        try:
            ffmpeg.to_wav16k_mono(source, target)
        except FFmpegError as error:
            raise ValueError(f"ffmpeg could not decode the file: {(error.stderr or str(error)).strip()[-300:]}") from error
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
    raise ValueError("Only WAV files can be imported without ffmpeg installed.")


class Importer:
    def __init__(self, store: SessionStore, pipeline: Pipeline):
        self.store, self.pipeline = store, pipeline

    def import_file(self, source: Path, filename: str, title: str = "", kind: str = "other", language: str = "auto", keep_source: bool = False) -> dict:
        """`keep_source` copies the file instead of moving it: used when the caller owns it (import by path)."""
        ext = Path(filename).suffix.lower()
        if ext not in ALLOWED_EXT:
            raise ValueError(f"Unsupported file type: {ext or '(none)'}")
        session = self.store.create(title or Path(filename).stem, kind, mic=True, system=False, language=language, origin="import", status="processing")
        folder = self.store.session_dir(session["id"])
        original = folder / f"original{ext}"
        if keep_source:
            shutil.copy2(source, original)
        else:
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
