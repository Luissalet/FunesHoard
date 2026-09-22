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
