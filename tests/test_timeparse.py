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


# --- A5: everyday time words ------------------------------------------------
def test_weekday_names_mean_the_most_recent_past_occurrence():
    # NOW is Sunday 2026-03-15. "martes"/"tuesday" -> 2026-03-10.
    for word in ("martes", "el martes", "tuesday", "last tuesday", "Tuesday"):
        start, end = parse_day_or_range(word, None, None, NOW)
        assert (start, end) == (_midnight(2026, 3, 10), _midnight(2026, 3, 11)), word


def test_weekday_name_never_resolves_to_today_even_if_it_matches():
    # NOW is a Sunday: "domingo"/"sunday" must mean last Sunday, a week ago.
    for word in ("domingo", "sunday"):
        start, _ = parse_day_or_range(word, None, None, NOW)
        assert start == _midnight(2026, 3, 8), word


def test_this_morning_is_the_first_half_of_today():
    for word in ("this morning", "esta mañana"):
        start, end = parse_day_or_range(word, None, None, NOW)
        assert start == _midnight(2026, 3, 15)
        assert end == _midnight(2026, 3, 15) + 12 * 3600


def test_bare_time_of_day_means_today_at_that_time():
    ts = parse_moment("14:30", NOW)
    assert datetime.fromtimestamp(ts) == datetime(2026, 3, 15, 14, 30, 0)
    ts2 = parse_moment("08:05", NOW)
    assert datetime.fromtimestamp(ts2) == datetime(2026, 3, 15, 8, 5, 0)


def test_before_lunch_is_no_longer_advertised_in_the_mcp_tool_keywords():
    # mcp_server.py is a standalone script (see its own module docstring)
    # deliberately not imported by anything else -- read its source instead.
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "funes_hoard" / "mcp_server.py").read_text(encoding="utf-8")
    assert "before lunch" not in source
    assert "antes de comer" not in source
