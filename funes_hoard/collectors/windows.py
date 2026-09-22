"""Windows probe via ctypes (user32/kernel32) -- no heavy dependencies.

Import-safe on non-Windows platforms: every Windows-only symbol is defined
inside `if sys.platform == "win32":` so this module can be imported (but
not instantiated) on Linux for tests/CI.
"""
from __future__ import annotations

import sys
import time
from typing import Optional

from funes_hoard.core.spans import Sample

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    def _get_idle_seconds() -> float:
        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        now_ticks = kernel32.GetTickCount64()
        return max(0.0, (now_ticks - info.dwTime) / 1000.0)

    def _get_foreground_window_text() -> tuple[str, int]:
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return "", 0
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return buf.value, pid.value

    def _is_locked() -> bool:
        # A locked workstation typically has LockApp.exe foreground, or no
        # foreground window while the input desktop cannot be opened.
        try:
            desktop = user32.OpenInputDesktop(0, False, 0x0100)
            if not desktop:
                return True
            user32.CloseDesktop(desktop)
            return False
        except Exception:
            return False


class WindowsProbe:
    def __init__(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("WindowsProbe only runs on Windows")
        try:
            import psutil  # noqa: F401
        except ImportError as exc:
            raise RuntimeError("psutil is required for WindowsProbe") from exc

    def sample(self) -> Sample:
        ts = time.time()
        try:
            title, pid = _get_foreground_window_text()
        except Exception:
            title, pid = "", 0
        exe = ""
        app = "unknown"
        if pid:
            try:
                import psutil

                proc = psutil.Process(pid)
                exe = proc.exe() or ""
                app = proc.name() or "unknown"
            except Exception:
                app = "unknown"
        if app.lower() == "lockapp.exe":
            locked = True
        else:
            try:
                locked = _is_locked()
            except Exception:
                locked = False
        try:
            idle_s = _get_idle_seconds()
        except Exception:
            idle_s = 0.0
        return Sample(ts=ts, app=app, exe=exe, title=title, pid=pid or None, idle_s=idle_s, locked=locked)

    def status(self) -> str:
        return "active (Windows probe via user32/psutil)"
