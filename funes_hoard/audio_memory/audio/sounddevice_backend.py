"""Cross-platform microphone capture through PortAudio (`sounddevice`). Mic only: no loopback."""

from __future__ import annotations

import threading
import time

import numpy as np
import sounddevice as sd  # raises OSError when PortAudio is missing; the selector catches it

from .base import TRACK_MIC, AudioBackend, Block, CaptureStream, Device, DeviceList
from .wav import SAMPLE_RATE, resample, to_mono_int16


class SounddeviceBackend(AudioBackend):
    name = "sounddevice"

    def devices(self) -> DeviceList:
        listing = DeviceList(notes=["sounddevice backend: microphone only, no system loopback on this platform"])
        try:
            default_in = sd.default.device[0]
            for index, info in enumerate(sd.query_devices()):
                if info.get("max_input_channels", 0) > 0:
                    listing.mic.append(Device(str(index), info["name"], "mic", index == default_in))
        except Exception as error:  # no devices at all
            listing.notes.append(f"no input devices: {error}")
        return listing

    def open(self, mic: bool, system: bool, block_ms: int = 30) -> CaptureStream:
        if not mic:
            raise ValueError("The sounddevice backend can only capture the microphone.")
        stream = CaptureStream([TRACK_MIC])
        device = sd.default.device[0]
        info = sd.query_devices(device, "input")
        rate = SAMPLE_RATE
        try:
            sd.check_input_settings(device=device, channels=1, samplerate=rate, dtype="int16")
        except Exception:
            rate = int(info["default_samplerate"])
        frames = int(rate * block_ms / 1000)

        def callback(indata, _frames, _time, status):
            if stream.closed:
                raise sd.CallbackStop
            data = to_mono_int16(np.array(indata, copy=True))
            if rate != SAMPLE_RATE:
                data = resample(data, rate, SAMPLE_RATE)
            stream.push(Block(TRACK_MIC, data))

        sd_stream = sd.InputStream(samplerate=rate, channels=1, dtype="int16", blocksize=frames, device=device, callback=callback)
        sd_stream.start()

        def closer():
            while not stream.closed:
                time.sleep(0.2)
            try:
                sd_stream.stop()
                sd_stream.close()
            except Exception:
                pass

        threading.Thread(target=closer, name="sounddevice-closer", daemon=True).start()
        return stream
