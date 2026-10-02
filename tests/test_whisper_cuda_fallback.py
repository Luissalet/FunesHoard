"""A missing cuBLAS/cuDNN only shows while faster-whisper's lazy segment generator
is consumed: the transcriber must fall back to the CPU instead of failing the session."""

import sys
import types
from types import SimpleNamespace

import numpy as np

from funes_hoard.audio_memory.transcribe import whisper as w
from funes_hoard.hoard_link.media import stt


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
    monkeypatch.setattr(stt, "cuda_available", lambda: True)
    monkeypatch.setattr(stt, "gpu_leasing_enabled", lambda: False)
    t = w.WhisperTranscriber(tmp_path, size="small", device="auto")
    segments = t.transcribe(np.zeros(16000, dtype=np.int16), language="es")
    assert [s.text for s in segments] == ["Yo me encargo del presupuesto."]
    assert FakeModel.loads == ["cuda", "cpu"]
    assert "cublas" in t.cpu_fallback_reason
    assert t.loaded_key[1] == "cpu"


def test_info_keeps_the_keys_the_ui_reads(tmp_path):
    info = w.WhisperTranscriber(tmp_path, size="tiny", device="cpu").info()
    for key in ("name", "model", "device", "compute_type", "cuda", "loaded", "download", "state", "error", "cpu_fallback_reason", "last_ms",
                "models_dir", "gpu_lease"):
        assert key in info, key
    assert info["model"] == "tiny" and info["compute_type"] == "int8" and info["models_dir"] == str(tmp_path)


def test_models_folder_stays_in_the_data_dir_only_when_a_model_is_there(tmp_path, monkeypatch):
    monkeypatch.setenv("HOARD_HOME", str(tmp_path / "home"))
    data = tmp_path / "data"
    assert w.default_models_dir(data) == tmp_path / "home" / "models" / "whisper"  # nothing downloaded: the family's folder
    model = data / "models" / "models--Systran--faster-whisper-small" / "snapshots" / "abc"
    model.mkdir(parents=True)
    (model / "model.bin").write_bytes(b"x")
    assert w.default_models_dir(data) == data / "models"  # an existing download is still found


def test_decoder_signals_and_words_reach_the_segments(monkeypatch, tmp_path):
    FakeModel.loads = []
    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeModel))
    t = w.WhisperTranscriber(tmp_path, size="small", device="cpu")
    seg = t.transcribe(np.zeros(16000, dtype=np.int16), language="es")[0]
    assert (seg.start, seg.end) == (0.0, 1.5) and seg.no_speech_prob == 0.01 and seg.compression_ratio == 1.1
    assert seg.avg_logprob == -0.1 and seg.confidence == 0.905
