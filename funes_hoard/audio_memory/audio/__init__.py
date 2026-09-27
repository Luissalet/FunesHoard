"""Backend selection by platform and configuration. Never crashes on import."""

from __future__ import annotations

import sys

from .base import TRACK_MIC, TRACK_SYSTEM, AudioBackend, Block, CaptureStream, Device, DeviceList, UnavailableBackend
from .fake import FakeBackend

__all__ = ["AudioBackend", "Block", "CaptureStream", "Device", "DeviceList", "FakeBackend", "TRACK_MIC", "TRACK_SYSTEM", "select_backend"]


def select_backend(kind: str = "auto", fixture: str = "", speed: float = 1.0) -> tuple[AudioBackend, list[str]]:
    notes: list[str] = []
    if kind == "fake":
        return FakeBackend(fixture, speed), notes
    if kind == "none":
        return UnavailableBackend("Audio capture disabled by SCRIBE_AUDIO=none."), notes
    if kind in ("auto", "wasapi"):
        if sys.platform == "win32":
            try:
                from .windows_wasapi import WasapiBackend

                return WasapiBackend(), notes
            except Exception as error:
                notes.append(f"wasapi backend unavailable (pip install -r requirements-windows.txt): {error}")
        elif kind == "wasapi":
            notes.append("wasapi backend requested on a non-Windows platform")
        if kind == "wasapi":
            return UnavailableBackend("WASAPI backend unavailable: " + "; ".join(notes)), notes
    try:
        from .sounddevice_backend import SounddeviceBackend

        backend = SounddeviceBackend()
        if sys.platform == "win32":
            notes.append("using sounddevice: microphone only (no system loopback)")
        return backend, notes
    except Exception as error:  # PortAudio missing, headless box
        notes.append(f"sounddevice unavailable: {error}")
    return UnavailableBackend("No audio capture backend available: " + "; ".join(notes)), notes
