from funes_hoard.collector import Collector
from funes_hoard.collectors.fake import FakeProbe
from funes_hoard.core.spans import Sample
from funes_hoard.db import Database

T0 = 1_800_000_000.0


def make_probe(samples):
    return FakeProbe([
        Sample(ts=0.0, app=a, exe="", title=t, pid=1, idle_s=idle, locked=locked)
        for (a, t, idle, locked) in samples
    ])


def test_tick_persists_closed_span_on_app_change(tmp_path):
    db = Database(tmp_path / "data")
    probe = make_probe([
        ("Code.exe", "a.py - Foo - Visual Studio Code", 0, False),
        ("Code.exe", "a.py - Foo - Visual Studio Code", 0, False),
        ("chrome.exe", "New Tab - Chrome", 0, False),
    ])
    c = Collector(db, probe, interval_s=1)
    c.tick(now=T0)
    c.tick(now=T0 + 1)
    c.tick(now=T0 + 2)
    rows = db.query("SELECT * FROM spans WHERE open = 0")
    assert len(rows) == 1
    assert rows[0]["app"] == "Code.exe"
    assert rows[0]["category"] == "Coding"
    assert rows[0]["project"] == "Foo"


def test_flush_writes_open_span_for_crash_safety(tmp_path):
    db = Database(tmp_path / "data")
    probe = make_probe([("Code.exe", "a.py - Foo - Visual Studio Code", 0, False)] * 5)
    c = Collector(db, probe, interval_s=1)
    c.tick(now=T0)
    rows = db.query("SELECT * FROM spans")
    assert len(rows) == 1 and rows[0]["open"] == 1 and rows[0]["end_ts"] == T0
    # more than FLUSH_INTERVAL_S (30s) later, still the same open span: the
    # periodic flush must have advanced end_ts without closing it, so a
    # crash here would only lose the last <30s, not the whole session.
    c.tick(now=T0 + 31)
    rows = db.query("SELECT * FROM spans")
    assert len(rows) == 1
    assert rows[0]["open"] == 1
    assert rows[0]["end_ts"] == T0 + 31


def test_excluded_sample_never_creates_a_row(tmp_path):
    db = Database(tmp_path / "data")
    probe = make_probe([
        ("KeePassXC", "KeePassXC - vault.kdbx", 0, False),
        ("KeePassXC", "KeePassXC - vault.kdbx", 0, False),
        ("Code.exe", "a.py - Foo - Visual Studio Code", 0, False),
    ])
    c = Collector(db, probe, interval_s=1)
    c.tick(now=T0)
    c.tick(now=T0 + 1)
    c.tick(now=T0 + 2)
    rows = db.query("SELECT * FROM spans")
    # only the Code.exe sample should ever have produced a row
    assert all(r["app"] != "KeePassXC" for r in rows)
    titles = [r["title"] for r in rows]
    assert "KeePassXC - vault.kdbx" not in titles


def test_redacted_title_is_stored_as_redacted_marker(tmp_path):
    db = Database(tmp_path / "data")
    probe = make_probe([
        ("chrome.exe", "My Bank - login", 0, False),
        ("chrome.exe", "My Bank - login", 0, False),
        ("Code.exe", "a.py - Foo - Visual Studio Code", 0, False),
    ])
    c = Collector(db, probe, interval_s=1)
    c.tick(now=T0)
    c.tick(now=T0 + 1)
    c.tick(now=T0 + 2)
    rows = db.query("SELECT * FROM spans WHERE app = 'chrome.exe'")
    assert len(rows) == 1
    assert rows[0]["title"] == "[redacted]"


def test_pause_stops_recording_and_expires_automatically(tmp_path):
    db = Database(tmp_path / "data")
    probe = make_probe([("Code.exe", "x - Foo - Visual Studio Code", 0, False)] * 10)
    c = Collector(db, probe, interval_s=1)
    c.pause(minutes=1, now=T0)
    assert c.is_paused(T0)
    c.tick(now=T0 + 10)  # still paused, nothing recorded
    assert db.query("SELECT * FROM spans") == []
    assert not c.is_paused(T0 + 61)  # auto-expires
    c.tick(now=T0 + 61)
    rows = db.query("SELECT * FROM spans")
    assert len(rows) == 1


def test_stop_flushes_the_open_span_closed(tmp_path):
    db = Database(tmp_path / "data")
    probe = make_probe([("Code.exe", "x - Foo - Visual Studio Code", 0, False)] * 3)
    c = Collector(db, probe, interval_s=1)
    c.tick(now=T0)
    c.tick(now=T0 + 1)
    c.stop()
    rows = db.query("SELECT * FROM spans")
    assert len(rows) == 1
    assert rows[0]["open"] == 0
