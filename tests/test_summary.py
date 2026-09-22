from funes_hoard.core.summary import SpanRow, count_context_switches, day_totals, focus_blocks, where_was_i

T0 = 1_700_000_000.0


def span(id_, start_off, end_off, kind="active", app="Code.exe", project="Atlas", category="Coding", title="x"):
    return SpanRow(
        id=id_, start_ts=T0 + start_off, end_ts=T0 + end_off, kind=kind,
        app=app, exe="", title=title, pid=1, category=category, project=project,
    )


def test_day_totals_splits_active_and_away():
    spans = [
        span(1, 0, 1800, kind="active"),
        span(2, 1800, 2400, kind="away", app="unknown", project=None, category="Other"),
    ]
    totals = day_totals(spans)
    assert totals["active_s"] == 1800
    assert totals["away_s"] == 600
    assert totals["by_category"]["Coding"] == 1800


def test_focus_block_requires_25_minutes():
    spans = [span(1, 0, 20 * 60)]  # only 20 min
    assert focus_blocks(spans) == []

    spans2 = [span(1, 0, 26 * 60)]
    blocks = focus_blocks(spans2)
    assert len(blocks) == 1
    assert blocks[0].duration_s == 26 * 60


def test_focus_block_survives_short_interruption():
    spans = [
        span(1, 0, 15 * 60, project="Atlas"),
        span(2, 15 * 60, 16 * 60, kind="away", app="unknown", project=None, category="Other"),  # 1 min interruption
        span(3, 16 * 60, 31 * 60, project="Atlas"),
    ]
    blocks = focus_blocks(spans)
    assert len(blocks) == 1
    assert blocks[0].key == "Atlas"
    assert blocks[0].duration_s == 30 * 60


def test_focus_block_broken_by_long_interruption():
    spans = [
        span(1, 0, 15 * 60, project="Atlas"),
        span(2, 15 * 60, 20 * 60, kind="away", app="unknown", project=None, category="Other"),  # 5 min > budget
        span(3, 20 * 60, 35 * 60, project="Atlas"),
    ]
    blocks = focus_blocks(spans)
    # neither half reaches 25 min alone
    assert blocks == []


def test_focus_block_broken_by_different_project():
    spans = [
        span(1, 0, 30 * 60, project="Atlas"),
        span(2, 30 * 60, 60 * 60, project="Lumen"),
    ]
    blocks = focus_blocks(spans)
    assert len(blocks) == 2
    assert {b.key for b in blocks} == {"Atlas", "Lumen"}


def test_context_switches_ignore_short_dwell():
    spans = [
        span(1, 0, 300, app="Code.exe"),
        span(2, 300, 305, app="chrome.exe"),  # 5s flicker, ignored
        span(3, 305, 600, app="Code.exe"),
    ]
    assert count_context_switches(spans) == 0


def test_context_switches_counts_real_changes():
    spans = [
        span(1, 0, 300, app="Code.exe"),
        span(2, 300, 600, app="chrome.exe"),
        span(3, 600, 900, app="Teams.exe"),
    ]
    assert count_context_switches(spans) == 2


def test_where_was_i_merges_adjacent_same_project():
    spans = [
        span(1, 0, 600, project="Atlas", app="Code.exe"),
        span(2, 600, 900, project="Atlas", app="Code.exe", title="y"),
        span(3, 900, 1200, project="Lumen", app="Code.exe"),
    ]
    ctxs = where_was_i(spans, before=T0 + 1500, contexts=5)
    assert len(ctxs) == 2
    assert ctxs[0].project == "Lumen"
    assert ctxs[1].project == "Atlas"
    assert ctxs[1].duration_s == 900


def test_where_was_i_skips_away_and_respects_before():
    spans = [
        span(1, 0, 600, project="Atlas"),
        span(2, 600, 1200, kind="away", app="unknown", project=None, category="Other"),
        span(3, 1200, 1800, project="Lumen"),
    ]
    ctxs = where_was_i(spans, before=T0 + 900, contexts=5)
    assert len(ctxs) == 1
    assert ctxs[0].project == "Atlas"
