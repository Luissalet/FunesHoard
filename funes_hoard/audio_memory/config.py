"""Process-level configuration read from the environment (never from the DB)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .guard import parse_allowed_hosts

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_PORT = 5185


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass
class Config:
    """Everything the process needs before the database exists."""

    data_dir: Path = field(default_factory=lambda: REPO_ROOT / "data" / "audio")
    port: int = DEFAULT_PORT
    port_strict: bool = False
    audio_backend: str = "auto"  # auto | wasapi | sounddevice | fake | none
    transcriber: str = "auto"  # auto | whisper | fake
    fake_fixture: str = ""  # WAV (or "mic.wav,system.wav") streamed by the fake backend
    fake_speed: float = 1.0  # 0 = as fast as possible
    allowed_hosts: tuple[str, ...] = ()  # extra Host values (exact or *.suffix) besides localhost
    data_dir_configured: bool = False

    @property
    def db_path(self) -> Path:
        return self.data_dir / "scribe-hoard.db"

    @property
    def sessions_dir(self) -> Path:
        return self.data_dir / "sessions"

    models_dir_override: Path | None = None  # SCRIBE_MODELS_DIR: share downloaded models between data dirs

    @property
    def models_dir(self) -> Path:
        return self.models_dir_override or self.data_dir / "models"

    @property
    def token_path(self) -> Path:
        return self.data_dir / "mcp-token"

    @classmethod
    def from_env(cls) -> "Config":
        raw_dir = _env("SCRIBE_DATA_DIR")
        port_raw = _env("SCRIBE_PORT") or _env("PORT") or str(DEFAULT_PORT)
        try:
            port = int(port_raw)
        except ValueError:
            port = DEFAULT_PORT
        if not 1 <= port <= 65535:
            port = DEFAULT_PORT
        try:
            speed = float(_env("SCRIBE_FAKE_SPEED", "1") or "1")
        except ValueError:
            speed = 1.0
        return cls(
            data_dir=Path(raw_dir).expanduser() if raw_dir else REPO_ROOT / "data" / "audio",
            port=port,
            port_strict=_env("PORT_STRICT") == "1",
            audio_backend=_env("SCRIBE_AUDIO", "auto") or "auto",
            transcriber=_env("SCRIBE_TRANSCRIBER", "auto") or "auto",
            fake_fixture=_env("SCRIBE_FAKE_FIXTURE"),
            fake_speed=speed,
            models_dir_override=Path(_env("SCRIBE_MODELS_DIR")).expanduser() if _env("SCRIBE_MODELS_DIR") else None,
            allowed_hosts=parse_allowed_hosts(_env("SCRIBE_ALLOWED_HOSTS")),
            data_dir_configured=bool(raw_dir),
        )
