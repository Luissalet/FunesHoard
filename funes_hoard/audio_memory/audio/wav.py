"""WAV helpers: 16 kHz mono int16 is the house format for every track."""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000


def to_mono_int16(samples: np.ndarray) -> np.ndarray:
    """Any float/int array, 1-D or (frames, channels), to mono int16."""
    array = np.asarray(samples)
    if array.ndim == 2:
        array = array.mean(axis=1) if array.dtype.kind == "f" else array.astype(np.float32).mean(axis=1)
    if array.dtype == np.int16:
        return array
    if array.dtype.kind == "f":
        return (np.clip(array, -1.0, 1.0) * 32767).astype(np.int16)
    if array.dtype == np.int32:
        return (array >> 16).astype(np.int16)
    return array.astype(np.int16)


def to_float32(samples: np.ndarray) -> np.ndarray:
    array = np.asarray(samples)
    if array.dtype.kind == "f":
        return array.astype(np.float32)
    return array.astype(np.float32) / 32768.0


def resample(samples: np.ndarray, src_rate: int, dst_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Linear resampling — good enough for speech going into a recognizer."""
    if src_rate == dst_rate or len(samples) == 0:
        return samples
    floats = to_float32(samples)
    count = int(round(len(floats) * dst_rate / src_rate))
    positions = np.linspace(0, len(floats) - 1, num=count, dtype=np.float64)
    out = np.interp(positions, np.arange(len(floats)), floats).astype(np.float32)
    return out if samples.dtype.kind == "f" else to_mono_int16(out)


def rms_level(samples: np.ndarray) -> float:
    """0..1 loudness of a block (RMS of the float signal, clipped)."""
    if len(samples) == 0:
        return 0.0
    floats = to_float32(samples)
    return float(min(1.0, np.sqrt(np.mean(floats * floats)) * 4))


class WavWriter:
    """Append-only mono int16 writer; `frames` is the running length."""

    def __init__(self, path: Path, sample_rate: int = SAMPLE_RATE):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.sample_rate = sample_rate
        self.frames = 0
        self._wav = wave.open(str(path), "wb")
        self._wav.setnchannels(1)
        self._wav.setsampwidth(2)
        self._wav.setframerate(sample_rate)

    def write(self, samples: np.ndarray) -> None:
        data = to_mono_int16(samples)
        self._wav.writeframes(data.tobytes())
        self.frames += len(data)

    @property
    def seconds(self) -> float:
        return self.frames / self.sample_rate

    def close(self) -> None:
        self._wav.close()


def read_wav(path: Path, target_rate: int = SAMPLE_RATE) -> tuple[np.ndarray, int]:
    """Read a PCM WAV (8/16/32-bit, any channels) as mono int16 at `target_rate`."""
    with wave.open(str(path), "rb") as wav:
        channels, width, rate = wav.getnchannels(), wav.getsampwidth(), wav.getframerate()
        raw = wav.readframes(wav.getnframes())
    if width == 2:
        data = np.frombuffer(raw, dtype=np.int16)
    elif width == 4:
        data = (np.frombuffer(raw, dtype=np.int32) >> 16).astype(np.int16)
    elif width == 1:
        data = ((np.frombuffer(raw, dtype=np.uint8).astype(np.int16) - 128) << 8).astype(np.int16)
    else:
        raise ValueError(f"Unsupported WAV sample width: {width * 8} bits")
    if channels > 1:
        data = data.reshape(-1, channels)
    mono = to_mono_int16(data)
    return resample(mono, rate, target_rate), target_rate


def write_wav(path: Path, samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> None:
    writer = WavWriter(path, sample_rate)
    writer.write(samples)
    writer.close()


def mix_tracks(tracks: list[np.ndarray]) -> np.ndarray:
    """Sum mono int16 tracks (zero-padded to the longest) with soft clipping."""
    if not tracks:
        return np.zeros(0, dtype=np.int16)
    length = max(len(t) for t in tracks)
    total = np.zeros(length, dtype=np.float32)
    for track in tracks:
        total[: len(track)] += to_float32(track)
    return to_mono_int16(np.tanh(total))


def peaks(samples: np.ndarray, buckets: int = 400) -> list[float]:
    """Waveform-lite: peak absolute value per bucket, 0..1."""
    floats = np.abs(to_float32(samples))
    if len(floats) == 0 or buckets <= 0:
        return []
    size = max(1, len(floats) // buckets)
    trimmed = floats[: size * buckets] if len(floats) >= buckets else floats
    if len(trimmed) < buckets:
        return [round(float(v), 3) for v in trimmed]
    return [round(float(v), 3) for v in trimmed.reshape(buckets, size).max(axis=1)]
