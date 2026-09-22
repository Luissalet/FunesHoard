from datetime import datetime

from funes_hoard.timeparse import human_range, parse_day_or_range, parse_moment

NOW = datetime(2026, 3, 15, 14, 30, 0).timestamp()  # a Sunday


def test_parse_moment_now():
    assert parse_moment(None, NOW) == NOW
    assert parse_moment("now", NOW) == NOW


def test_parse_moment_today_word_en_and_es():
    assert parse_moment("today", NOW) == NOW
    assert parse_moment("hoy", NOW) == NOW


def test_parse_moment_yesterday_en_and_es():
    assert parse_moment("yesterday", NOW) == NOW - 86400
    assert parse_moment("ayer", NOW) == NOW - 86400


def test_parse_moment_relative_hours_and_days():
    assert parse_moment("-2h", NOW) == NOW - 2 * 3600
    assert parse_moment("-3d", NOW) == NOW - 3 * 86400
    assert parse_moment("-30m", NOW) == NOW - 30 * 60


def test_parse_moment_iso_datetime():
    ts = parse_moment("2026-01-01T10:00:00", NOW)
    assert datetime.fromtimestamp(ts) == datetime(2026, 1, 1, 10, 0, 0)


def test_parse_day_today_bounds():
    start, end = parse_day_or_range("today", None, None, NOW)
    assert end - start == 86400
    assert datetime.fromtimestamp(start).hour == 0


def test_parse_day_this_week_es():
    start, end = parse_day_or_range("esta semana", None, None, NOW)
    assert end - start == 7 * 86400
    assert datetime.fromtimestamp(start).weekday() == 0  # Monday


def test_parse_start_end_range():
    start, end = parse_day_or_range(None, "-2d", "now", NOW)
    assert abs(end - NOW) < 1
    assert abs((NOW - start) - 2 * 86400) < 1


def test_human_range_format():
    s = NOW - 3600
    text = human_range(s, NOW)
    assert "60 min" in text.split(", ")[-1] or "min" in text
