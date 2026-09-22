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


# --- edges: day words are ranges, so since/until need the right end -------
def _midnight(y, m, d):
    return datetime(y, m, d).timestamp()


def test_since_today_means_start_of_today_not_now():
    assert parse_moment("today", NOW, edge="start") == _midnight(2026, 3, 15)
    assert parse_moment("hoy", NOW, edge="start") == _midnight(2026, 3, 15)


def test_since_yesterday_means_start_of_yesterday():
    assert parse_moment("ayer", NOW, edge="start") == _midnight(2026, 3, 14)


def test_until_or_before_yesterday_means_end_of_yesterday():
    assert parse_moment("yesterday", NOW, edge="end") == _midnight(2026, 3, 15)


def test_since_this_week_accepted_in_both_languages():
    assert parse_moment("this week", NOW, edge="start") == _midnight(2026, 3, 9)
    assert parse_moment("esta semana", NOW, edge="start") == _midnight(2026, 3, 9)


def test_until_plain_date_includes_that_whole_day():
    assert parse_moment("2026-03-10", NOW, edge="end") == _midnight(2026, 3, 11)


def test_relative_forms_without_sign_and_in_words():
    assert parse_moment("2h", NOW) == NOW - 7200
    assert parse_moment("2h ago", NOW) == NOW - 7200
    assert parse_moment("hace 3d", NOW) == NOW - 3 * 86400


def test_iso_with_z_suffix_and_offset():
    a = parse_moment("2026-03-15T12:00:00Z", NOW)
    b = parse_moment("2026-03-15T13:00:00+01:00", NOW)
    assert a == b


def test_bad_time_message_lists_what_is_accepted():
    import pytest

    from funes_hoard.timeparse import TimeParseError

    with pytest.raises(TimeParseError) as exc:
        parse_moment("next tuesdayish", NOW)
    assert exc.value.code == "bad_time"
    assert "yesterday/ayer" in exc.value.message


def test_start_alone_naming_a_day_selects_that_whole_day():
    start, end = parse_day_or_range(None, "yesterday", None, NOW)
    assert (start, end) == (_midnight(2026, 3, 14), _midnight(2026, 3, 15))
    start, end = parse_day_or_range(None, "2026-03-10", None, NOW)
    assert (start, end) == (_midnight(2026, 3, 10), _midnight(2026, 3, 11))


def test_start_alone_relative_runs_until_now():
    start, end = parse_day_or_range(None, "-2h", None, NOW)
    assert (start, end) == (NOW - 7200, NOW)


def test_day_accepts_relative_offset_as_its_calendar_day():
    start, _ = parse_day_or_range("-1d", None, None, NOW)
    assert start == _midnight(2026, 3, 14)


def test_human_range_whole_day_and_cross_day():
    assert human_range(_midnight(2026, 3, 15), _midnight(2026, 3, 16), now=NOW) == "today, whole day"
    text = human_range(_midnight(2026, 3, 14) + 23 * 3600, _midnight(2026, 3, 15) + 3600, now=NOW)
    assert text == "yesterday 23:00 to today 01:00, 2 h 00 min"
    assert human_range(NOW - 42 * 60, NOW, now=NOW) == "today 13:48-14:30, 42 min"


def test_human_range_for_a_week_uses_absolute_dates():
    start, end = parse_day_or_range("this week", None, None, NOW)
    assert human_range(start, end, now=NOW) == "Mon 2026-03-09 to Sun 2026-03-15 (7 days)"
