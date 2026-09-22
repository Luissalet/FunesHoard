"""Windows probe via ctypes (user32/kernel32) -- no heavy dependencies.

Import-safe on non-Windows platforms: every Windows-only symbol is defined
inside `if sys.platform == "win32":` so this module can be imported (but
not instantiated) on Linux for tests/CI. The arithmetic that does not need
Win32 (`idle_seconds_from_ticks`) lives outside that block so it is tested
everywhere.

Every foreign function gets explicit `argtypes`/`restype`: ctypes defaults
to C `int` for both, which silently truncates 64-bit handles and return
values (HWND, HDESK, ULONGLONG) on 64-bit Python.
"""
from __future__ import annotations

import sys
import time

from funes_hoard.core.spans import Sample

_TICK_WRAP = 2 ** 32


def idle_seconds_from_ticks(now_tick32: int, last_input_tick32: int) -> float:
    """Seconds since the last input, from two 32-bit millisecond tick counts.

    LASTINPUTINFO.dwTime is the low 32 bits of the tick count and wraps
    every 49.7 days, so the difference must be taken modulo 2**32 against
    the 32-bit GetTickCount(), never against the 64-bit GetTickCount64().
    """
    return ((now_tick32 - last_input_tick32) % _TICK_WRAP) / 1000.0


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    HDESK = wintypes.HANDLE
    DESKTOP_SWITCHDESKTOP = 0x0100

    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]
    user32.GetLastInputInfo.restype = wintypes.BOOL
    user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    user32.OpenInputDesktop.restype = HDESK
    user32.CloseDesktop.argtypes = [HDESK]
    user32.CloseDesktop.restype = wintypes.BOOL
    kernel32.GetTickCount.argtypes = []
    kernel32.GetTickCount.restype = wintypes.DWORD

    def _get_idle_seconds() -> float:
        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        return idle_seconds_from_ticks(kernel32.GetTickCount(), info.dwTime)

    def _get_foreground_window() -> tuple[str, int, bool]:
        """(title, pid, has_window)."""
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return "", 0, False
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(max(0, length) + 1)
        user32.GetWindowTextW(hwnd, buf, len(buf))
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return buf.value, pid.value, True

    def _input_desktop_available() -> bool:
        # While the workstation is locked the input desktop is Winlogon's
        # and cannot be opened by a normal process.
        desktop = user32.OpenInputDesktop(0, False, DESKTOP_SWITCHDESKTOP)
        if not desktop:
            return False
        user32.CloseDesktop(desktop)
        return True


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
            title, pid, _has_window = _get_foreground_window()
        except Exception:
            title, pid = "", 0
        exe = ""
        app = "unknown"
        if pid:
            import psutil

            try:
                proc = psutil.Process(pid)
                app = proc.name() or "unknown"
            except Exception:
                proc = None
            if proc is not None:
                try:
                    exe = proc.exe() or ""  # AccessDenied for elevated processes
                except Exception:
                    exe = ""
        try:
            locked = app.lower() == "lockapp.exe" or not _input_desktop_available()
        except Exception:
            locked = False
        try:
            idle_s = _get_idle_seconds()
        except Exception:
            idle_s = 0.0
        return Sample(ts=ts, app=app, exe=exe, title=title, pid=pid or None, idle_s=idle_s, locked=locked)

    def status(self) -> str:
        return "active (Windows probe via user32/psutil)"
