import time

from funes_hoard.db import Database
from funes_hoard.retention import delete_range, run_retention

NOW = 1_800_000_000.0


def _seed(db):
    db.execute(
        "INSERT INTO spans(start_ts, end_ts, kind, app, exe, title, category, project, open) VALUES (?, ?, 'active', 'Code.exe', '', 't', 'Coding', 'Atlas', 0)",
        (NOW - 200 * 86400, NOW - 200 * 86400 + 60),
    )
    db.execute(
        "INSERT INTO spans(start_ts, end_ts, kind, app, exe, title, category, project, open) VALUES (?, ?, 'active', 'Code.exe', '', 't', 'Coding', 'Atlas', 0)",
        (NOW - 5 * 86400, NOW - 5 * 86400 + 60),
    )
    db.execute("INSERT INTO file_events(ts, path) VALUES (?, ?)", (NOW - 200 * 86400, "old.txt"))
    db.execute("INSERT INTO file_events(ts, path) VALUES (?, ?)", (NOW - 5 * 86400, "recent.txt"))


def test_run_retention_purges_only_older_than_cutoff(tmp_path):
    db = Database(tmp_path / "data")
    db.set_meta("retention_days", "180")
    _seed(db)
    counts = run_retention(db, now=NOW)
    assert counts["spans"] == 1
    remaining = db.query("SELECT * FROM spans")
    assert len(remaining) == 1
    assert remaining[0]["start_ts"] == NOW - 5 * 86400


def test_run_retention_respects_configured_days(tmp_path):
    db = Database(tmp_path / "data")
    db.set_meta("retention_days", "3")
    _seed(db)
    counts = run_retention(db, now=NOW)
    assert counts["spans"] == 2  # both are older than 3 days


def test_delete_range_removes_only_requested_window(tmp_path):
    db = Database(tmp_path / "data")
    _seed(db)
    counts = delete_range(db, NOW - 6 * 86400, NOW - 4 * 86400)
    assert counts["spans"] == 1
    remaining = db.query("SELECT * FROM spans")
    assert len(remaining) == 1
    assert remaining[0]["start_ts"] == NOW - 200 * 86400


def test_delete_range_also_removes_overlapping_spans_and_search_rows(tmp_path):
    db = Database(tmp_path / "data")
    sid = db.execute(
        "INSERT INTO spans(start_ts, end_ts, kind, app, exe, title, open) VALUES (?, ?, 'active', 'chrome.exe', '', 'secret page', 0)",
        (NOW - 600, NOW + 600),
    )
    db.index_text("span", sid, NOW - 600, "secret page")
    counts = delete_range(db, NOW, NOW + 60)
    assert counts["spans"] == 1
    if db.fts_available:
        assert db.query("SELECT * FROM search_fts WHERE search_fts MATCH 'secret'") == []


def test_retention_purges_the_search_index_too(tmp_path):
    db = Database(tmp_path / "data")
    fid = db.execute("INSERT INTO file_events(ts, path) VALUES (?, ?)", (NOW - 400 * 86400, "C:/old/diary.txt"))
    db.index_text("file", fid, NOW - 400 * 86400, "C:/old/diary.txt")
    run_retention(db, now=NOW)
    if db.fts_available:
        assert db.query("SELECT * FROM search_fts WHERE search_fts MATCH 'diary'") == []
