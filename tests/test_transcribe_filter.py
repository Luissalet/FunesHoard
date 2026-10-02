"""The hallucination filter the sessions use is the family's, with Funes's counters kept on the session."""

from funes_hoard.audio_memory.transcribe.base import Segment, Word
from funes_hoard.audio_memory.transcribe.filter import FilterStats, filter_segments


def seg(text, start=0.0, end=2.0, **kw):
    return Segment(start, end, text, 0.9, kw.pop("words", []), **{"no_speech_prob": 0.05, "avg_logprob": -0.3, "compression_ratio": 1.2, **kw})


def test_decoder_doubts_and_known_artifacts_are_dropped_and_counted():
    stats = FilterStats()
    kept = filter_segments([
        seg("Yo me encargo del presupuesto."),
        seg("Algo dudoso", no_speech_prob=0.9),
        seg("Otra cosa dudosa", avg_logprob=-1.5),
        seg("Repetición", compression_ratio=3.0),
        seg("Subtítulos realizados por la comunidad de Amara.org"),
        seg("[Música]"),
    ], stats)
    assert [s.text for s in kept] == ["Yo me encargo del presupuesto."]
    assert (stats.kept, stats.dropped_no_speech, stats.dropped_logprob, stats.dropped_compression, stats.dropped_denylist) == (1, 1, 1, 1, 2)
    assert stats.dropped == 5 and stats.as_dict()["dropped"] == 5


def test_a_word_loop_is_collapsed_and_its_words_are_cleared():
    words = [Word(0.0, 0.2, "no", 0.9)] * 6
    kept = filter_segments([seg("no no no no no no", words=words)])
    assert kept[0].text == "no" and kept[0].words == []
    kept = filter_segments([seg("no no no", words=words[:3])])  # three repeats are natural speech
    assert kept[0].text == "no no no" and len(kept[0].words) == 3


def test_near_identical_neighbours_merge_into_one_that_ends_where_the_run_ends():
    stats = FilterStats()
    kept = filter_segments([seg("Hasta mañana a todos.", 0, 2), seg("Hasta mañana a todos", 2, 4)], stats)
    assert len(kept) == 1 and kept[0].start == 0 and kept[0].end == 4 and stats.removed_duplicate == 1


def test_stats_add_up_across_chunks():
    total = FilterStats()
    total.add(FilterStats(kept=2, dropped_denylist=1, loops_collapsed=1)).add(FilterStats(kept=1, removed_duplicate=2))
    assert (total.kept, total.dropped_denylist, total.loops_collapsed, total.removed_duplicate) == (3, 1, 1, 2)
