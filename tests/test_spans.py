from funes_hoard.core.spans import Sample, SpanBuilder


def s(ts, app="Code.exe", title="a.py - Foo - Visual Studio Code", idle_s=0.0, locked=False):
    return Sample(ts=ts, app=app, exe="", title=title, pid=1, idle_s=idle_s, locked=locked)


def test_consecutive_same_app_title_merge_into_one_span():
    b = SpanBuilder(away_after_s=120, sleep_gap_s=90)
    closed = []
    closed += b.add_sample(s(0))
    closed += b.add_sample(s(1))
    closed += b.add_sample(s(2))
    assert closed == []  # nothing closed yet, still the same open span
    open_span = b.peek_open()
    assert open_span.start_ts == 0
    assert open_span.end_ts == 2
    assert open_span.kind == "active"


def test_app_change_closes_previous_span():
    b = SpanBuilder(away_after_s=120, sleep_gap_s=90)
    b.add_sample(s(0, app="Code.exe"))
    b.add_sample(s(1, app="Code.exe"))
    closed = b.add_sample(s(2, app="chrome.exe", title="something - Chrome"))
    assert len(closed) == 1
    assert closed[0].app == "Code.exe"
    assert closed[0].start_ts == 0
    assert closed[0].end_ts == 2
    assert b.peek_open().app == "chrome.exe"


def test_title_change_same_app_closes_span():
    b = SpanBuilder(away_after_s=120, sleep_gap_s=90)
    b.add_sample(s(0, title="a.py - Foo - Visual Studio Code"))
    closed = b.add_sample(s(1, title="b.py - Foo - Visual Studio Code"))
    assert len(closed) == 1
    assert closed[0].title == "a.py - Foo - Visual Studio Code"


def test_idle_crossing_threshold_opens_backdated_away_span():
    b = SpanBuilder(away_after_s=120, sleep_gap_s=9999)
    b.add_sample(s(0, idle_s=0))
    b.add_sample(s(100, idle_s=100))  # still under threshold, extends active span
    closed = b.add_sample(s(300, idle_s=300))  # idle_s >= 120 -> away, back-dated
    assert len(closed) == 1
    active_span = closed[0]
    assert active_span.kind == "active"
    assert active_span.start_ts == 0
    # away span should have started when input actually stopped: ts - idle_s = 300-300=0,
    # but must not start before the active span's own start (0) -- clamp check
    assert active_span.end_ts == 0
    open_span = b.peek_open()
    assert open_span.kind == "away"
    assert open_span.start_ts == 0
    assert open_span.end_ts == 300


def test_away_backdate_does_not_cross_earlier_active_start():
    b = SpanBuilder(away_after_s=60, sleep_gap_s=9999)
    b.add_sample(s(1000, idle_s=0))
    b.add_sample(s(1010, idle_s=10))
    # idle jumps a lot but active span only started at 1000; back-dated start
    # (1080 - 200 = 880) must not be before 1000.
    closed = b.add_sample(s(1080, idle_s=200))
    assert closed[0].start_ts == 1000
    assert closed[0].end_ts == 1000
    open_span = b.peek_open()
    assert open_span.start_ts == 1000


def test_locked_span_starts_immediately_no_backdate():
    b = SpanBuilder(away_after_s=120, sleep_gap_s=9999)
    b.add_sample(s(0))
    closed = b.add_sample(s(5, locked=True))
    assert len(closed) == 1
    assert closed[0].kind == "active"
    assert closed[0].end_ts == 5
    assert b.peek_open().kind == "locked"
    assert b.peek_open().start_ts == 5


def test_consecutive_away_samples_merge():
    b = SpanBuilder(away_after_s=60, sleep_gap_s=9999)
    b.add_sample(s(0, idle_s=0))
    b.add_sample(s(100, idle_s=100))
    b.add_sample(s(200, idle_s=200))
    open_span = b.peek_open()
    assert open_span.kind == "away"
    assert open_span.end_ts == 200


def test_sleep_gap_closes_span_at_last_sample_without_bridging():
    b = SpanBuilder(away_after_s=120, sleep_gap_s=90)
    b.add_sample(s(0))
    b.add_sample(s(10))
    # huge gap: laptop slept for an hour
    closed = b.add_sample(s(3610))
    assert len(closed) == 1
    assert closed[0].start_ts == 0
    assert closed[0].end_ts == 10  # closed at the LAST sample, not bridged to 3610
    open_span = b.peek_open()
    assert open_span.start_ts == 3610


def test_resume_from_away_closes_away_span_at_resume_time():
    b = SpanBuilder(away_after_s=60, sleep_gap_s=9999)
    b.add_sample(s(0, idle_s=0))
    b.add_sample(s(100, idle_s=100))  # away starts backdated
    closed = b.add_sample(s(200, idle_s=0))  # activity resumes
    assert closed[0].kind == "away"
    assert closed[0].end_ts == 200
    assert b.peek_open().kind == "active"


def test_close_open_on_shutdown():
    b = SpanBuilder()
    b.add_sample(s(0))
    b.add_sample(s(5))
    span = b.close_open(at_ts=10)
    assert span.end_ts == 10
    assert b.peek_open() is None


# --- B2 regression: away spans must never be back-dated across the moment
# recording (re)started -----------------------------------------------------
def test_first_sample_ever_deeply_idle_opens_away_at_its_own_ts_not_backdated():
    b = SpanBuilder(away_after_s=120, sleep_gap_s=9999)
    closed = b.add_sample(s(1_800_000_000.0, idle_s=3121))
    assert closed == []  # nothing to close, no active span was ever open
    open_span = b.peek_open()
    assert open_span.kind == "away"
    assert open_span.start_ts == 1_800_000_000.0  # not backdated 3121s into the past


def test_floor_prevents_backdating_below_the_last_persisted_span():
    b = SpanBuilder(away_after_s=120, sleep_gap_s=9999)
    b.raise_floor(1000.0)  # e.g. Collector's MAX(end_ts) from a previous run
    closed = b.add_sample(s(1300.0, idle_s=3000))  # would backdate to -1700 unfloored
    assert closed == []
    assert b.peek_open().start_ts == 1000.0


def test_restart_during_the_same_idle_does_not_duplicate_the_away_span():
    # First run: idle crosses the threshold, an away span opens (back-dated
    # to the active span's own start) and is still open at t=300.
    b1 = SpanBuilder(away_after_s=120, sleep_gap_s=9999)
    b1.add_sample(s(0, idle_s=0))
    b1.add_sample(s(100, idle_s=100))
    b1.add_sample(s(300, idle_s=300))
    persisted_end = b1.peek_open().end_ts  # what a periodic flush would have written (300)

    # App restarts mid-idle: a fresh builder, floored at what was persisted.
    b2 = SpanBuilder(away_after_s=120, sleep_gap_s=9999, floor_ts=persisted_end)
    closed = b2.add_sample(s(600.0, idle_s=600.0))  # still idle since real input stopped at t=0
    assert closed == []
    # Must continue from where the first run left off, never re-open at the
    # original (already-recorded) start.
    assert b2.peek_open().start_ts == persisted_end


def test_away_after_a_pause_starts_no_earlier_than_the_pause() -> None:
    b = SpanBuilder(away_after_s=60, sleep_gap_s=9999)
    b.add_sample(s(1000, idle_s=0))
    b.add_sample(s(1600, idle_s=0))
    closed_by_pause = b.interrupt(1600.0)  # e.g. the user paused recording here
    assert closed_by_pause.end_ts == 1600.0
    # Resuming later with a stale idle reading must not backdate into (or
    # before) the span the pause just closed.
    closed = b.add_sample(s(2000.0, idle_s=500.0))  # 2000-500=1500 < 1600 unfloored
    assert closed == []
    assert b.peek_open().kind == "away"
    assert b.peek_open().start_ts == 1600.0
