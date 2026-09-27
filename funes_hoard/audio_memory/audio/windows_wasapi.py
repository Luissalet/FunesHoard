"""Windows backend: WASAPI loopback of the default speaker + default microphone via `soundcard`.

Two independent tracks ("mic" = you, "system" = the others) so the transcript can tell who spoke
without any voice recognition. Cannot run on Linux; written for Windows 10/11 and reviewed, not executed here.

Notes on WASAPI loopback: a loopback capture delivers no packets while nothing is being rendered,
which would desynchronise the two tracks. soundcard >= 0.4.6 measures the idle time and returns
zeros for it; on top of that the recorder pads each track with silence based on wall-clock time
(see recorder.TrackWriter). As a last resort `keepalive=True` renders silence to the default speaker
so the loopback endpoint keeps ticking (off by default: it opens an extra render stream).
"""

from __future__ import annotations

import ctypes
import threading
import time

import numpy as np
import soundcard as sc  # Windows-only dependency (requirements-windows.txt)

from .base import TRACK_MIC, TRACK_SYSTEM, AudioBackend, Block, CaptureStream, Device, DeviceList
from .wav import SAMPLE_RATE, to_mono_int16

BLOCKSIZE_HINT = 4800  # ~300 ms of buffer at 16 kHz: generous, avoids glitches on busy machines
KEEPALIVE_BLOCK_S = 0.2


def _com_init() -> None:
    """Every thread that touches WASAPI needs COM initialised (COINIT_MULTITHREADED = 0)."""
    try:
        ctypes.windll.ole32.CoInitializeEx(None, 0)  # type: ignore[attr-defined]
    except Exception:
        pass


class WasapiBackend(AudioBackend):
    name = "wasapi"

    def __init__(self, keepalive: bool = False):
        self.keepalive = keepalive

    def devices(self) -> DeviceList:
        listing = DeviceList(notes=["wasapi backend: microphone + loopback of the default speaker"])
        try:
            default_mic = sc.default_microphone()
            default_name = default_mic.name if default_mic else ""
        except Exception:
            default_name = ""
        try:
            for mic in sc.all_microphones(include_loopback=False):
                listing.mic.append(Device(str(mic.id), mic.name, "mic", mic.name == default_name))
        except Exception as error:
            listing.notes.append(f"no microphones: {error}")
        try:
            default_speaker = sc.default_speaker()
            speaker_name = default_speaker.name if default_speaker else ""
            for spk in sc.all_speakers():
                listing.system.append(Device(str(spk.id), f"{spk.name} (loopback)", "system", spk.name == speaker_name))
        except Exception as error:
            listing.notes.append(f"no speakers: {error}")
        return listing

    def open(self, mic: bool, system: bool, block_ms: int = 30) -> CaptureStream:
        tracks = [name for name, enabled in ((TRACK_MIC, mic), (TRACK_SYSTEM, system)) if enabled]
        if not tracks:
            raise ValueError("At least one source (mic or system) must be enabled.")
        stream = CaptureStream(tracks)
        frames = int(SAMPLE_RATE * block_ms / 1000)
        if mic:
            source = sc.default_microphone()
            if source is None:
                raise RuntimeError("No default microphone.")
            threading.Thread(target=self._record, args=(stream, TRACK_MIC, source, frames), name="wasapi-mic", daemon=True).start()
        if system:
            speaker = sc.default_speaker()
            if speaker is None:
                raise RuntimeError("No default speaker for loopback.")
            loopback = sc.get_microphone(id=str(speaker.name), include_loopback=True)
            threading.Thread(target=self._record, args=(stream, TRACK_SYSTEM, loopback, frames), name="wasapi-loopback", daemon=True).start()
            if self.keepalive:
                threading.Thread(target=self._keepalive, args=(stream, speaker), name="wasapi-keepalive", daemon=True).start()
        return stream

    @staticmethod
    def _record(stream: CaptureStream, track: str, source, frames: int) -> None:
        _com_init()
        try:
            with source.recorder(samplerate=SAMPLE_RATE, channels=1, blocksize=BLOCKSIZE_HINT) as recorder:
                while not stream.closed:
                    data = recorder.record(numframes=frames)  # float32 (frames, 1)
                    if data is None or len(data) == 0:
                        continue
                    stream.push(Block(track, to_mono_int16(np.asarray(data, dtype=np.float32))))
        except Exception as error:  # device unplugged, exclusive-mode app, etc.
            stream.error = f"{track}: {error}"
            stream.push(Block(track, np.zeros(0, dtype=np.int16), ended=True))

    @staticmethod
    def _keepalive(stream: CaptureStream, speaker) -> None:
        """Render silence so the loopback endpoint keeps delivering packets during quiet spells."""
        _com_init()
        silence = np.zeros(int(SAMPLE_RATE * KEEPALIVE_BLOCK_S), dtype=np.float32)
        try:
            with speaker.player(samplerate=SAMPLE_RATE, channels=1, blocksize=BLOCKSIZE_HINT) as player:
                while not stream.closed:
                    player.play(silence)
                    time.sleep(KEEPALIVE_BLOCK_S / 2)
        except Exception:
            return
