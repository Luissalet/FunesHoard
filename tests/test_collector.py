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


def test_pause_indefinitely_never_auto_expires(tmp_path):
    db = Database(tmp_path / "data")
    probe = make_probe([("Code.exe", "x - Foo - Visual Studio Code", 0, False)])
    c = Collector(db, probe, interval_s=1)
    c.pause_indefinitely()
    assert c.is_paused(T0)
    assert c.is_paused(T0 + 10 * 86400)  # ten days later, still paused
    db.set_meta("paused_until", "")  # only an explicit resume clears it
    assert not c.is_paused(T0 + 10 * 86400)


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


# --- review regressions ----------------------------------------------------
def test_excluded_interlude_is_not_billed_to_the_surrounding_window(tmp_path):
    db = Database(tmp_path / "data")
    code = ("Code.exe", "a.py - Foo - Visual Studio Code", 0, False)
    vault = ("KeePassXC", "KeePassXC - vault.kdbx", 0, False)
    probe = make_probe([code, code, vault, vault, vault, code, code, ("chrome.exe", "x", 0, False)])
    c = Collector(db, probe, interval_s=1)
    for i in range(8):
        c.tick(now=T0 + i * 20)
    rows = db.query("SELECT start_ts, end_ts, app FROM spans WHERE open = 0 ORDER BY start_ts")
    code_rows = [(r["start_ts"] - T0, r["end_ts"] - T0) for r in rows if r["app"] == "Code.exe"]
    # 0-40 before the vault, 100-140 after: the 60 s in between is not Code.
    assert code_rows == [(0, 40), (100, 140)]


def test_pausing_closes_the_open_span_at_the_pause(tmp_path):
    db = Database(tmp_path / "data")
    probe = make_probe([("Code.exe", "x - Foo - Visual Studio Code", 0, False)] * 10)
    c = Collector(db, probe, interval_s=1)
    c.tick(now=T0)
    c.tick(now=T0 + 10)
    c.pause(minutes=5, now=T0 + 15)
    c.tick(now=T0 + 20)
    rows = db.query("SELECT * FROM spans")
    assert len(rows) == 1 and rows[0]["open"] == 0
    assert rows[0]["end_ts"] == T0 + 20  # recorded up to the first paused sample, then nothing


def test_stale_open_rows_from_a_crash_are_closed_on_startup(tmp_path):
    db = Database(tmp_path / "data")
    db.execute(
        "INSERT INTO spans(start_ts, end_ts, kind, app, exe, title, open) VALUES (1, 2, 'active', 'Code.exe', '', 'x', 1)"
    )
    Collector(db, make_probe([("a", "b", 0, False)]), interval_s=1)
    assert db.query_one("SELECT COUNT(*) c FROM spans WHERE open = 1")["c"] == 0


def test_stop_closes_at_the_last_sample_not_the_wall_clock(tmp_path):
    db = Database(tmp_path / "data")
    probe = make_probe([("Code.exe", "x - Foo - Visual Studio Code", 0, False)] * 3)
    c = Collector(db, probe, interval_s=1)
    c.tick(now=T0)
    c.tick(now=T0 + 1)
    c.stop()
    assert db.query_one("SELECT end_ts FROM spans")["end_ts"] == T0 + 1


def test_restart_after_a_crash_does_not_backdate_away_before_persisted_history(tmp_path):
    # B2: a fresh Collector must never open an away span earlier than what
    # is already recorded -- otherwise a restart during a long idle period
    # produces a second span covering time that was already counted.
    db = Database(tmp_path / "data")
    db.execute(
        "INSERT INTO spans(start_ts, end_ts, kind, app, exe, title, open) VALUES (?, ?, 'active', 'Code.exe', '', 'x', 0)",
        (T0, T0 + 100),
    )
    probe = make_probe([("Code.exe", "x", 3000, False)])  # still idle since before restart
    c = Collector(db, probe, interval_s=1)
    c.tick(now=T0 + 130)  # idle_s=3000 would backdate to T0-2870 unfloored
    rows = db.query("SELECT * FROM spans WHERE kind = 'away'")
    assert len(rows) == 1
    assert rows[0]["start_ts"] == T0 + 100  # floored at the last persisted span's end


def test_zero_length_span_stub_is_never_persisted(tmp_path):
    # C1: idle jumps enough that the back-dated away span swallows the
    # active span entirely (start == end == T0), leaving a zero-length stub.
    # The gap between ticks (80s) stays under the 90s sleep-gap threshold,
    # so this exercises the backdate-collapse path, not the sleep-gap one.
    db = Database(tmp_path / "data")
    probe = make_probe([
        ("Code.exe", "x - Foo - Visual Studio Code", 0, False),
        ("Code.exe", "x - Foo - Visual Studio Code", 200, False),  # idle >= 120s: away, backdated to T0
    ])
    c = Collector(db, probe, interval_s=1)
    c.tick(now=T0)
    c.tick(now=T0 + 80)
    active_rows = db.query("SELECT * FROM spans WHERE kind = 'active'")
    assert active_rows == []  # the zero-length active stub must never be written
    away_rows = db.query("SELECT * FROM spans WHERE kind = 'away'")
    assert len(away_rows) == 1 and away_rows[0]["start_ts"] == T0


def test_meetings_and_media_get_a_much_longer_away_threshold(tmp_path):
    # A3: a 20-minute idle gap during a Teams call must stay active (Teams.exe
    # is a Meetings default), while the same gap in a plain editor goes away.
    db = Database(tmp_path / "data")
    probe = make_probe([
        ("Teams.exe", "Weekly sync", 0, False),
        ("Teams.exe", "Weekly sync", 20 * 60, False),  # 20 min idle: below the 60 min meetings threshold
    ])
    c = Collector(db, probe, interval_s=1)
    c.tick(now=T0)
    c.tick(now=T0 + 60)
    closed_rows = db.query("SELECT * FROM spans WHERE open = 0")
    assert closed_rows == []  # still one continuous active span, not yet closed
    open_row = db.query_one("SELECT * FROM spans WHERE open = 1")
    assert open_row["kind"] == "active"


def test_meetings_threshold_does_not_apply_to_ordinary_apps(tmp_path):
    db = Database(tmp_path / "data")
    probe = make_probe([
        ("Code.exe", "x - Foo - Visual Studio Code", 0, False),
        ("Code.exe", "x - Foo - Visual Studio Code", 20 * 60, False),  # 20 min idle: well past the 2 min default
    ])
    c = Collector(db, probe, interval_s=1)
    c.tick(now=T0)
    c.tick(now=T0 + 60)
    open_row = db.query_one("SELECT * FROM spans WHERE open = 1")
    assert open_row["kind"] == "away"


def test_pause_at_least_never_shortens_or_ends_an_existing_pause(tmp_path):
    db = Database(tmp_path / "data")
    c = Collector(db, make_probe([("a", "b", 0, False)]), interval_s=1)
    until, extended = c.pause_at_least(15, now=T0)
    assert extended and until == T0 + 900
    until, extended = c.pause_at_least(5, now=T0)
    assert not extended and until == T0 + 900
    until, extended = c.pause_at_least(30, now=T0)
    assert extended and until == T0 + 1800
    c.pause_indefinitely()
    until, extended = c.pause_at_least(5, now=T0)
    assert until is None and not extended
    assert c.is_paused(T0 + 10 * 86400)


def _feed(c, app, title, start, minutes, step=60, idle_from=None):
    """Tick `minutes` of one window with no input from `idle_from` (default: the start)."""
    base = start if idle_from is None else idle_from
    for k in range(int(minutes * 60 / step) + 1):
        ts = start + k * step
        c.probe = FakeProbe([Sample(ts=ts, app=app, exe="", title=title, pid=1, idle_s=max(0.0, ts - base), locked=False)])
        c.tick(now=ts)


def test_a_45_minute_call_with_no_input_is_all_meeting(tmp_path):
    # Re-walk of UC3: the Thursday interview (Zoom.exe, 45 min, hands off the
    # keyboard) was recorded as 45 min away under the 30-min threshold.
    db = Database(tmp_path / "data")
    c = Collector(db, make_probe([("x", "y", 0, False)]), interval_s=1)
    _feed(c, "Zoom.exe", "Entrevista técnica - Zoom Meeting", T0, 45)
    _feed(c, "Code.exe", "a.py - Foo - Visual Studio Code", T0 + 45 * 60 + 5, 1)
    rows = db.query("SELECT kind, category, start_ts, end_ts FROM spans WHERE app = 'Zoom.exe'")
    assert [(r["kind"], r["category"]) for r in rows] == [("active", "Meetings")]
    assert rows[0]["end_ts"] - rows[0]["start_ts"] >= 45 * 60


def test_a_film_left_running_past_the_threshold_keeps_the_first_hour(tmp_path):
    db = Database(tmp_path / "data")
    c = Collector(db, make_probe([("x", "y", 0, False)]), interval_s=1)
    _feed(c, "chrome.exe", "Fan film: The Last Signal (Full Movie) - YouTube - Google Chrome", T0, 80)
    c.stop()
    rows = db.query("SELECT kind, category, start_ts, end_ts FROM spans ORDER BY start_ts")
    assert [(r["kind"], r["category"]) for r in rows] == [("active", "Media"), ("away", "Other")]
    assert rows[0]["end_ts"] - rows[0]["start_ts"] == 3600
    assert rows[1]["start_ts"] == T0 + 3600
