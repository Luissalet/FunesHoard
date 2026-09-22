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


# --- review regressions ----------------------------------------------------
def test_focus_block_survives_a_short_glance_at_another_app():
    # 20 s on chat in the middle of an hour of Atlas is an interruption,
    # not the end of the focus block (spec: interruptions <= 2 min each).
    spans = [
        span(1, 0, 20 * 60, project="Atlas"),
        span(2, 20 * 60, 20 * 60 + 20, app="Discord.exe", project=None, category="Communication"),
        span(3, 20 * 60 + 20, 40 * 60, project="Atlas"),
    ]
    blocks = focus_blocks(spans)
    assert len(blocks) == 1
    assert blocks[0].key == "Atlas"
    assert blocks[0].start_ts == T0 and blocks[0].end_ts == T0 + 40 * 60


def test_focus_block_interruption_budget_is_the_whole_gap_not_each_span():
    # Three 50 s side trips back to back = 150 s away from Atlas: too long.
    spans = [
        span(1, 0, 20 * 60, project="Atlas"),
        span(2, 1200, 1250, app="a.exe", project=None, category="Browsing"),
        span(3, 1250, 1300, app="b.exe", project=None, category="Communication"),
        span(4, 1300, 1350, app="c.exe", project=None, category="Media"),
        span(5, 1350, 1350 + 20 * 60, project="Atlas"),
    ]
    assert focus_blocks(spans) == []


def test_focus_block_does_not_end_on_a_trailing_interruption():
    spans = [
        span(1, 0, 30 * 60, project="Atlas"),
        span(2, 30 * 60, 30 * 60 + 30, app="Discord.exe", project=None, category="Communication"),
    ]
    blocks = focus_blocks(spans)
    assert blocks[0].end_ts == T0 + 30 * 60


def test_where_was_i_returns_distinct_contexts_with_last_title():
    spans = [
        span(1, 0, 600, project="Atlas", title="api.py - Atlas - Visual Studio Code"),
        span(2, 600, 900, app="chrome.exe", project=None, category="Browsing", title="Docs"),
        span(3, 900, 1500, project="Atlas", title="db.py - Atlas - Visual Studio Code"),
    ]
    ctxs = where_was_i(spans, before=T0 + 2000, contexts=5)
    assert [c.key for c in ctxs] == ["Atlas", "chrome.exe"]
    assert ctxs[0].title == "db.py - Atlas - Visual Studio Code"


def test_where_was_i_ignores_alt_tab_blips():
    spans = [
        span(1, 0, 600, project="Atlas"),
        span(2, 600, 603, app="explorer.exe", project=None, category="System"),
        span(3, 603, 900, project="Lumen"),
    ]
    ctxs = where_was_i(spans, before=T0 + 1000, contexts=5)
    assert [c.key for c in ctxs] == ["Lumen", "Atlas"]


def test_where_was_i_clips_a_span_that_runs_past_before():
    spans = [span(1, 0, 3600, project="Atlas")]
    ctxs = where_was_i(spans, before=T0 + 600, contexts=5)
    assert ctxs[0].end_ts == T0 + 600
    assert ctxs[0].duration_s == 600


def test_clip_spans_to_window():
    from funes_hoard.core.summary import clip_spans

    spans = [span(1, -3600, 1800), span(2, 1800, 7200), span(3, 9000, 9600)]
    clipped = clip_spans(spans, T0, T0 + 3600)
    assert [(s.start_ts - T0, s.end_ts - T0) for s in clipped] == [(0, 1800), (1800, 3600)]
    assert spans[0].start_ts == T0 - 3600  # inputs untouched


def test_first_and_last_activity_ignore_away_and_locked_time():
    spans = [
        span(1, 0, 600, kind="locked", app="LockApp.exe", project=None, category="System"),
        span(2, 600, 1200),
        span(3, 1200, 5000, kind="away", app="unknown", project=None, category="Other"),
    ]
    totals = day_totals(spans)
    assert totals["first_activity"] == T0 + 600 and totals["last_activity"] == T0 + 1200


# --- A4: "where was I" answers with real work, not the music player --------
def test_where_was_i_skips_media_and_communication_by_default():
    spans = [
        span(1, 0, 600, project="daguerres-hoard", app="Code.exe", category="Coding"),
        span(2, 600, 900, project=None, app="Spotify.exe", category="Media", title="Focus playlist"),
        span(3, 900, 1200, project=None, app="WhatsApp.exe", category="Communication", title="Chat"),
    ]
    ctxs = where_was_i(spans, before=T0 + 1500, contexts=3)
    keys = [c.key for c in ctxs]
    assert "Spotify.exe" not in keys and "WhatsApp.exe" not in keys
    assert "daguerres-hoard" in keys


def test_where_was_i_all_categories_restores_old_behaviour():
    spans = [
        span(1, 0, 600, project="daguerres-hoard", app="Code.exe", category="Coding"),
        span(2, 600, 900, project=None, app="Spotify.exe", category="Media", title="Focus playlist"),
    ]
    ctxs = where_was_i(spans, before=T0 + 1500, contexts=5, skip_categories=frozenset())
    keys = [c.key for c in ctxs]
    assert "Spotify.exe" in keys


def test_where_was_i_ranks_a_project_ahead_of_a_bare_app_name():
    # Obsidian (no project) is the most recent, but a real project should
    # still come first -- this was the exact "Spotify before daguerres-hoard"
    # complaint once Media/Communication no longer explain it away.
    spans = [
        span(1, 0, 600, project="daguerres-hoard", app="Code.exe", category="Coding", title="a"),
        span(2, 600, 1200, project=None, app="Obsidian.exe", category="Writing", title="notes.md"),
    ]
    ctxs = where_was_i(spans, before=T0 + 1500, contexts=5)
    assert [c.key for c in ctxs] == ["daguerres-hoard", "Obsidian.exe"]


def test_where_was_i_keeps_the_editor_title_behind_a_final_terminal_blip():
    # Re-walk of UC1/UC2: the context ended in "pwsh - daguerres-hoard", so the
    # only title given was the terminal's, and the file was nowhere.
    spans = [
        span(1, 0, 600, app="Code.exe", title="Gallery.tsx - daguerres-hoard - Visual Studio Code", project="daguerres-hoard"),
        span(2, 600, 720, app="WindowsTerminal.exe", title="pwsh - daguerres-hoard", project="daguerres-hoard"),
    ]
    (ctx,) = where_was_i(spans, before=T0 + 1000, contexts=1)
    assert ctx.title == "pwsh - daguerres-hoard" and ctx.app == "WindowsTerminal.exe"
    assert ctx.recent_titles == ["pwsh - daguerres-hoard", "Gallery.tsx - daguerres-hoard - Visual Studio Code"]
