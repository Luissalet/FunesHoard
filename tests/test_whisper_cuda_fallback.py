"""A missing cuBLAS/cuDNN only shows while faster-whisper's lazy segment generator
is consumed: the transcriber must fall back to the CPU instead of failing the session."""

import sys
import types
from types import SimpleNamespace

import numpy as np

from funes_hoard.audio_memory.transcribe import whisper as w


class FakeModel:
    loads: list = []

    def __init__(self, size, device, compute_type, download_root):
        self.device = device
        FakeModel.loads.append(device)

    def transcribe(self, audio, **_):
        def gen():
            if self.device == "cuda":
                raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
            yield SimpleNamespace(start=0.0, end=1.5, text=" Yo me encargo del presupuesto.", avg_logprob=-0.1,
                                  words=[], no_speech_prob=0.01, compression_ratio=1.1)
        return gen(), None


def test_cuda_error_while_iterating_falls_back_to_cpu(monkeypatch, tmp_path):
    FakeModel.loads = []
    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeModel))
    monkeypatch.setattr(w, "cuda_available", lambda: True)
    monkeypatch.setattr(w, "gpu_leasing_enabled", lambda: False)
    t = w.WhisperTranscriber(tmp_path, size="small", device="auto")
    segments = t.transcribe(np.zeros(16000, dtype=np.int16), language="es")
    assert [s.text for s in segments] == ["Yo me encargo del presupuesto."]
    assert FakeModel.loads == ["cuda", "cpu"]
    assert "cublas" in t.cpu_fallback_reason
    assert t.loaded_key[1] == "cpu"
