"""Search as a model uses it: free text, several words, no FTS syntax."""
import pytest

from funes_hoard import queries
from funes_hoard.db import Database

NOW = 1_800_000_000.0


@pytest.fixture(params=["fts", "like"])
def db(tmp_path, request):
    d = Database(tmp_path)
    if request.param == "like":
        d.fts_available = False
    for i, title in enumerate(["DuckDB documentation - Google Chrome", "100% done - progress_bar.py - Atlas - Visual Studio Code"]):
        sid = d.execute(
            "INSERT INTO spans(start_ts, end_ts, kind, app, exe, title, open) VALUES (?, ?, 'active', 'x', '', ?, 0)",
            (NOW - 3600 + i * 60, NOW - 3500 + i * 60, title),
        )
        d.index_text("span", sid, NOW - 3600 + i * 60, title)
    return d


def test_all_words_must_match_first(db):
    res = queries.activity_search(db, "duckdb documentation", None, None, 10, now=NOW)
    assert res["matched"] == "all words" and len(res["items"]) == 1


def test_falls_back_to_any_word_and_says_so(db):
    res = queries.activity_search(db, "duckdb docs page", None, None, 10, now=NOW)
    assert res["matched"] == "any word"
    assert any("DuckDB" in i["text"] for i in res["items"])


def test_like_wildcards_in_the_query_are_literal(db):
    res = queries.activity_search(db, "progress_bar", None, None, 10, now=NOW)
    assert len(res["items"]) == 1
    none = queries.activity_search(db, "zzz_", None, None, 10, now=NOW)
    assert none["items"] == []


def test_since_until_bound_the_results(db):
    assert queries.activity_search(db, "duckdb", "-10m", None, 10, now=NOW)["items"] == []
    assert len(queries.activity_search(db, "duckdb", "-2h", None, 10, now=NOW)["items"]) == 1


def test_a_window_title_hit_says_how_long_the_window_was_open(db):
    # A8/UC3: "how much of the week went to job boards" needs a duration per
    # hit, not just a count of appearances.
    item = queries.activity_search(db, "duckdb", None, None, 10, now=NOW)["items"][0]
    assert item["duration_s"] == 100
    assert item["human"].endswith(", 2 min")  # 100 s, rounded like every other human string


def test_file_and_commit_hits_have_no_invented_duration(db):
    fid = db.execute("INSERT INTO file_events(ts, path, app_hint) VALUES (?, ?, 'md')", (NOW - 60, "C:/x/duckdb-notes.md"))
    db.index_text("file", fid, NOW - 60, "C:/x/duckdb-notes.md")
    items = queries.activity_search(db, "duckdb", None, None, 10, now=NOW)["items"]
    file_hit = next(i for i in items if i["source"] == "file")
    assert "duration_s" not in file_hit


def test_search_totals_how_long_the_matching_windows_were_open(db):
    res = queries.activity_search(db, "duckdb atlas", None, None, 10, now=NOW)  # any-word: both spans
    assert res["windows_open_s"] == 200
    assert res["windows_open_human"] == "3 min"
