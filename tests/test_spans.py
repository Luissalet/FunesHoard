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
