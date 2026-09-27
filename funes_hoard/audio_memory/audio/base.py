"""Audio capture interface: a backend lists devices and opens a stream of timed blocks."""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field

import numpy as np

from .wav import SAMPLE_RATE

TRACK_MIC = "mic"
TRACK_SYSTEM = "system"


@dataclass
class Device:
    id: str
    name: str
    kind: str  # "mic" | "system"
    default: bool = False


@dataclass
class DeviceList:
    mic: list[Device] = field(default_factory=list)
    system: list[Device] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "mic": [d.__dict__ for d in self.mic],
            "system": [d.__dict__ for d in self.system],
            "notes": list(self.notes),
        }


@dataclass
class Block:
    track: str  # "mic" | "system"
    samples: np.ndarray  # mono int16 at SAMPLE_RATE
    ended: bool = False  # last block of the stream (fake backend reached the end)


class CaptureStream:
    """Blocks from every track land in one queue; producers run in their own threads."""

    def __init__(self, tracks: list[str], sample_rate: int = SAMPLE_RATE):
        self.tracks = list(tracks)
        self.sample_rate = sample_rate
        self.queue: queue.Queue[Block] = queue.Queue(maxsize=4096)
        self.error = ""  # set by a producer thread when its device fails
        self._closed = threading.Event()

    def push(self, block: Block) -> None:
        try:
            self.queue.put(block, timeout=1)
        except queue.Full:  # consumer stalled: drop rather than block capture
            pass

    def read(self, timeout: float = 0.5) -> Block | None:
        try:
            return self.queue.get(timeout=timeout)
        except queue.Empty:
            return None

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    def close(self) -> None:
        self._closed.set()


class AudioBackend:
    name = "base"

    def devices(self) -> DeviceList:  # pragma: no cover - interface
        raise NotImplementedError

    def open(self, mic: bool, system: bool, block_ms: int = 30) -> CaptureStream:  # pragma: no cover - interface
        raise NotImplementedError


class UnavailableBackend(AudioBackend):
    """Explains why capture is impossible instead of crashing."""

    name = "none"

    def __init__(self, reason: str):
        self.reason = reason

    def devices(self) -> DeviceList:
        return DeviceList(notes=[self.reason])

    def open(self, mic: bool, system: bool, block_ms: int = 30) -> CaptureStream:
        raise RuntimeError(self.reason)
