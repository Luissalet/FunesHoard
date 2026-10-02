"""Transcript renderings: the SRT writer is the family's (milliseconds are rounded before the fields are split)."""

from funes_hoard.audio_memory.export import hms, render, render_srt

SESSION = {"title": "Reunión", "started_at": None, "kind": "meeting", "duration_s": 90}


def seg(start, end, text, speaker="yo"):
    return {"start_s": start, "end_s": end, "text": text, "speaker": speaker}


def test_srt_never_prints_a_thousand_milliseconds():
    # 59.9996 s used to come out as 00:00:59,1000 (the fraction was rounded after the seconds were split)
    out = render_srt(SESSION, [seg(59.9996, 61.2, "Hola")])
    assert "00:01:00,000 --> 00:01:01,200" in out and ",1000" not in out


def test_srt_blocks_speakers_and_minimum_length():
    out = render_srt(SESSION, [seg(0, 0.1, "Buenas"), seg(2.5, 4, "Sí", "otros")])
    assert out == "1\n00:00:00,000 --> 00:00:00,500\nyo: Buenas\n\n2\n00:00:02,500 --> 00:00:04,000\notros: Sí\n"


def test_srt_of_nothing_is_a_blank_line_and_render_dispatches():
    assert render_srt(SESSION, []) == "\n"
    assert render("srt", SESSION, [seg(1, 2, "x")]).startswith("1\n00:00:01,000")


def test_hms_keeps_the_display_format():
    assert hms(75) == "01:15" and hms(3725) == "01:02:05" and hms(-3) == "00:00"
