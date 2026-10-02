"""Converting a file to 16 kHz mono WAV: the family's ffmpeg lookup and runner, with PyAV and plain WAV as the fallbacks."""

from __future__ import annotations

import sys
import types

import numpy as np
import pytest

from funes_hoard.audio_memory import importer
from funes_hoard.audio_memory.audio.wav import SAMPLE_RATE, read_wav, write_wav
from funes_hoard.hoard_link.errors import HoardLinkError
from funes_hoard.hoard_link.media.ffmpeg import FFmpegError


class FakeFFmpeg:
    def __init__(self, present=True, fail=""):
        self.present, self.fail, self.calls = present, fail, []

    @property
    def ffmpeg(self):
        if not self.present:
            raise HoardLinkError("ffmpeg not found")
        return ["ffmpeg"]

    def to_wav16k_mono(self, src, dst):
        self.calls.append((str(src), str(dst)))
        if self.fail:
            raise FFmpegError("ffmpeg failed", code="failed", returncode=1, stderr=self.fail)
        write_wav(dst, np.ones(SAMPLE_RATE, dtype=np.int16))
        return dst


def test_ffmpeg_does_the_conversion_when_it_is_there(tmp_path):
    ffmpeg = FakeFFmpeg()
    assert importer.convert_to_wav(tmp_path / "a.mp3", tmp_path / "audio.wav", ffmpeg) == "ffmpeg"
    assert ffmpeg.calls == [(str(tmp_path / "a.mp3"), str(tmp_path / "audio.wav"))]
    assert len(read_wav(tmp_path / "audio.wav")[0]) == SAMPLE_RATE


def test_an_ffmpeg_failure_becomes_a_value_error_with_its_last_lines(tmp_path):
    with pytest.raises(ValueError, match="(?s)ffmpeg could not decode the file:.*Invalid data found"):
        importer.convert_to_wav(tmp_path / "a.mp3", tmp_path / "audio.wav", FakeFFmpeg(fail="[mp3] ...\nInvalid data found when processing input"))


def test_without_ffmpeg_pyav_decodes_then_plain_wav_then_a_clear_error(tmp_path, monkeypatch):
    absent = FakeFFmpeg(present=False)
    decoder = types.SimpleNamespace(decode_audio=lambda path, sampling_rate: np.zeros(SAMPLE_RATE, dtype=np.int16))
    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(audio=decoder))
    monkeypatch.setitem(sys.modules, "faster_whisper.audio", decoder)
    monkeypatch.setattr(importer, "pyav_available", lambda: True)
    assert importer.convert_to_wav(tmp_path / "a.m4a", tmp_path / "pyav.wav", absent) == "pyav"
    monkeypatch.setattr(importer, "pyav_available", lambda: False)
    write_wav(tmp_path / "plain.wav", np.zeros(SAMPLE_RATE, dtype=np.int16))
    assert importer.convert_to_wav(tmp_path / "plain.wav", tmp_path / "out.wav", absent) == "wav"
    with pytest.raises(ValueError, match="Only WAV files"):
        importer.convert_to_wav(tmp_path / "a.mp3", tmp_path / "out2.wav", absent)


def test_ffmpeg_path_follows_the_family_lookup(tmp_path, monkeypatch):
    tool = tmp_path / "ffmpeg-custom"
    tool.write_text("#!/bin/sh\necho ffmpeg version 7.0\n", encoding="utf-8")
    tool.chmod(0o755)
    monkeypatch.setenv("FUNES_FFMPEG", str(tool))
    from funes_hoard.hoard_link.media import bins

    bins.find("ffmpeg", refresh=True, legacy_env=("FUNES_FFMPEG", "SCRIBE_FFMPEG"),
              runner=lambda argv, timeout: (0, "ffmpeg version 7.0", ""))
    assert importer.ffmpeg_path() == str(tool)
