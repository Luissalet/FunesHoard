"""Persisted settings (key/value JSON in the `settings` table) with a pydantic model."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field

from .db import Database

ModelSize = Literal["tiny", "base", "small", "medium", "large-v3", "large-v3-turbo", "distil-large-v3"]
DeviceName = Literal["auto", "cuda", "cpu"]
ComputeType = Literal["auto", "int8", "int8_float16", "float16", "float32"]
Language = Literal["auto", "es", "en"]
Kind = Literal["meeting", "interview", "note", "other"]


class Settings(BaseModel):
    model_size: ModelSize = "small"
    device: DeviceName = "auto"
    compute_type: ComputeType = "auto"
    default_language: Language = "auto"
    default_kind: Kind = "meeting"
    default_source_mic: bool = True
    default_source_system: bool = True
    vad_sensitivity: int = Field(2, ge=0, le=3)  # 0 = permissive, 3 = aggressive (webrtcvad scale)
    live_transcription: bool = True
    live_chunk_max_s: int = Field(15, ge=5, le=60)


class SettingsPatch(BaseModel):
    """Every field optional: the UI writes single fields immediately."""

    model_size: ModelSize | None = None
    device: DeviceName | None = None
    compute_type: ComputeType | None = None
    default_language: Language | None = None
    default_kind: Kind | None = None
    default_source_mic: bool | None = None
    default_source_system: bool | None = None
    vad_sensitivity: int | None = Field(None, ge=0, le=3)
    live_transcription: bool | None = None
    live_chunk_max_s: int | None = Field(None, ge=5, le=60)


class SettingsStore:
    def __init__(self, db: Database):
        self.db = db
        self._cache: Settings | None = None

    def get(self) -> Settings:
        if self._cache is not None:
            return self._cache
        with self.db.lock:
            rows = self.db.conn.execute("SELECT key, value FROM settings").fetchall()
        raw = {}
        for row in rows:
            try:
                raw[row["key"]] = json.loads(row["value"])
            except json.JSONDecodeError:
                continue
        known = {k: v for k, v in raw.items() if k in Settings.model_fields}
        try:
            self._cache = Settings(**known)
        except ValueError:
            self._cache = Settings()
        return self._cache

    def update(self, patch: SettingsPatch | dict) -> Settings:
        data = patch.model_dump(exclude_none=True) if isinstance(patch, SettingsPatch) else dict(patch)
        merged = self.get().model_copy(update=data)
        merged = Settings.model_validate(merged.model_dump())
        with self.db.lock:
            self.db.conn.executemany(
                "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                [(key, json.dumps(value)) for key, value in merged.model_dump().items()],
            )
        self._cache = merged
        return merged
