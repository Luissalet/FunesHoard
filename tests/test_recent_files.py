import os
import time
from pathlib import Path

from tests.test_lnk import build_lnk

from funes_hoard.db import Database
from funes_hoard.recent_files import RecentFilesPoller, scan_once


def _write_lnk(dir_: Path, name: str, target: str) -> Path:
    data = build_lnk(target, name)
    p = dir_ / f"{name}.lnk"
    p.write_bytes(data)
    return p


def test_scan_once_resolves_lnk_targets(tmp_path):
    recent = tmp_path / "Recent"
    recent.mkdir()
    _write_lnk(recent, "readme", r"C:\Users\demo\Desktop\Proj\README.md")
    events = scan_once(recent, since_ts=0.0)
    assert len(events) == 1
    assert events[0].path == r"C:\Users\demo\Desktop\Proj\README.md"
    assert events[0].app_hint == "md"


def test_scan_once_skips_files_older_than_since(tmp_path):
    recent = tmp_path / "Recent"
    recent.mkdir()
    lnk = _write_lnk(recent, "old", r"C:\old.txt")
    future_since = lnk.stat().st_mtime + 100
    events = scan_once(recent, since_ts=future_since)
    assert events == []


def test_scan_once_missing_dir_returns_empty(tmp_path):
    events = scan_once(tmp_path / "does-not-exist", since_ts=0.0)
    assert events == []


def test_poller_records_events_and_advances_watermark(tmp_path):
    data_dir = tmp_path / "data"
    recent = tmp_path / "Recent"
    recent.mkdir()
    db = Database(data_dir)
    _write_lnk(recent, "a", r"C:\a.txt")
    poller = RecentFilesPoller(db, recent)
    n = poller.poll_once()
    assert n == 1
    rows = db.query("SELECT * FROM file_events")
    assert len(rows) == 1
    assert rows[0]["path"] == r"C:\a.txt"
    # a second poll with no new files finds nothing
    assert poller.poll_once() == 0


def test_poller_with_no_recent_dir_is_a_noop(tmp_path):
    db = Database(tmp_path / "data")
    poller = RecentFilesPoller(db, None)
    assert poller.poll_once() == 0
