"""The Win32 calls cannot run here; the logic around them can."""
import os
import sys

import psutil  # noqa: F401  (import before sys.platform is faked below)
import pytest

from funes_hoard.collectors import windows
from funes_hoard.collectors.windows import idle_seconds_from_ticks


def test_idle_is_plain_difference_normally():
    assert idle_seconds_from_ticks(10_000, 4_000) == 6.0


def test_idle_survives_the_49_day_tick_wraparound():
    # GetTickCount wrapped to 1 000 ms; last input was 2 000 ms before the wrap.
    assert idle_seconds_from_ticks(1_000, 2**32 - 2_000) == 3.0


def test_idle_after_24_days_uptime_is_not_zero():
    # Past 2**31 ms a signed 32-bit return value turns negative; the old
    # code then clamped idle to 0 and away time was never detected.
    base = 2**31 + 5_000
    assert idle_seconds_from_ticks(base + 300_000, base) == 300.0


def test_module_imports_on_every_platform_without_win32_symbols():
    if sys.platform != "win32":
        assert not hasattr(windows, "user32")
        with pytest.raises(RuntimeError):
            windows.WindowsProbe()


@pytest.fixture()
def fake_win32(monkeypatch):
    monkeypatch.setattr(windows.sys, "platform", "win32")
    state = {"fg": ("main.py - Atlas - Visual Studio Code", os.getpid(), True), "desktop": True, "idle": 3.0}
    monkeypatch.setattr(windows, "_get_foreground_window", lambda: state["fg"], raising=False)
    monkeypatch.setattr(windows, "_input_desktop_available", lambda: state["desktop"], raising=False)
    monkeypatch.setattr(windows, "_get_idle_seconds", lambda: state["idle"], raising=False)
    return state


def test_sample_reads_app_title_and_idle(fake_win32):
    s = windows.WindowsProbe().sample()
    assert s.title == "main.py - Atlas - Visual Studio Code"
    assert s.app != "unknown" and s.pid == os.getpid()
    assert s.idle_s == 3.0 and s.locked is False


def test_sample_reports_locked_when_input_desktop_is_unavailable(fake_win32):
    fake_win32["desktop"] = False
    assert windows.WindowsProbe().sample().locked is True


def test_sample_keeps_the_app_name_when_exe_is_access_denied(fake_win32, monkeypatch):
    import psutil

    class Proc:
        def __init__(self, pid):
            pass

        def name(self):
            return "Taskmgr.exe"

        def exe(self):
            raise psutil.AccessDenied()

    monkeypatch.setattr(psutil, "Process", Proc)
    s = windows.WindowsProbe().sample()
    assert s.app == "Taskmgr.exe" and s.exe == ""


def test_sample_never_raises(fake_win32, monkeypatch):
    def boom():
        raise OSError("win32 error")

    monkeypatch.setattr(windows, "_get_foreground_window", boom, raising=False)
    monkeypatch.setattr(windows, "_input_desktop_available", boom, raising=False)
    monkeypatch.setattr(windows, "_get_idle_seconds", boom, raising=False)
    s = windows.WindowsProbe().sample()
    assert s.app == "unknown" and s.idle_s == 0.0 and s.locked is False
