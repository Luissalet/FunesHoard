"""Recent-files source: polls `%APPDATA%\\Microsoft\\Windows\\Recent\\*.lnk`.

`scan_once()` is the testable unit: given a directory of .lnk files (or any
directory on any OS -- the Recent folder path is only special on Windows),
resolve each into a `file_events` row and return the new ones since a given
timestamp. The poller thread just calls this every 60s.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import List, NamedTuple, Optional

from funes_hoard.core.lnk import LnkParseError, parse_lnk_file, resolve_target
from funes_hoard.db import Database


class FileEvent(NamedTuple):
    ts: float
    path: str
    app_hint: Optional[str]


def windows_recent_dir() -> Optional[Path]:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return Path(appdata) / "Microsoft" / "Windows" / "Recent"


def scan_once(recent_dir: Path, since_ts: float = 0.0) -> List[FileEvent]:
    """Read every .lnk in `recent_dir` modified after `since_ts`."""
    events: List[FileEvent] = []
    if not recent_dir.is_dir():
        return events
    for entry in sorted(recent_dir.glob("*.lnk")):
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        if mtime <= since_ts:
            continue
        try:
            link = parse_lnk_file(entry)
        except (LnkParseError, OSError):
            continue
        target = resolve_target(link, lnk_dir=recent_dir)
        if not target:
            continue
        app_hint = Path(target).suffix.lstrip(".") or None
        events.append(FileEvent(ts=mtime, path=target, app_hint=app_hint))
    return events


class RecentFilesPoller:
    def __init__(self, db: Database, recent_dir: Optional[Path], interval_s: float = 60.0) -> None:
        self.db = db
        self.recent_dir = recent_dir
        self.interval_s = interval_s

    def poll_once(self, now: Optional[float] = None) -> int:
        if self.recent_dir is None:
            return 0
        last = self.db.get_meta("recent_files_last_ts", "0")
        since = float(last or 0.0)
        events = scan_once(self.recent_dir, since_ts=since)
        for ev in events:
            row_id = self.db.execute(
                "INSERT INTO file_events(ts, path, app_hint) VALUES (?, ?, ?)",
                (ev.ts, ev.path, ev.app_hint),
            )
            self.db.index_text("file", row_id, ev.ts, ev.path)
        if events:
            self.db.set_meta("recent_files_last_ts", str(max(e.ts for e in events)))
        return len(events)
