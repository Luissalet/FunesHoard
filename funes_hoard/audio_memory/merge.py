"""Speaker labelling by source channel and time-ordered merge of per-track segments.

With two tracks ("mic" = the user, "system" = everyone else) the label is a fact of the channel,
not a guess: "yo" / "otros". A single track gets "S1" — the recorder does not do voice recognition.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .audio.base import TRACK_MIC, TRACK_SYSTEM
from .transcribe.base import Segment

LABELS = {TRACK_MIC: "yo", TRACK_SYSTEM: "otros"}
SINGLE_LABEL = "S1"


@dataclass
class Labelled:
    start_s: float
    end_s: float
    speaker: str
    text: str
    confidence: float = 0.0
    words: list[dict] = field(default_factory=list)


def label_for(track: str, track_count: int) -> str:
    return LABELS.get(track, SINGLE_LABEL) if track_count > 1 else SINGLE_LABEL


def merge_tracks(per_track: dict[str, list[Segment]], offsets: dict[str, float] | None = None) -> list[Labelled]:
    """Interleave segments from every track by start time; labels come from the track name."""
    offsets = offsets or {}
    count = len(per_track)
    merged: list[Labelled] = []
    for track, segments in per_track.items():
        shift = offsets.get(track, 0.0)
        label = label_for(track, count)
        for seg in segments:
            if not seg.text.strip():
                continue
            merged.append(
                Labelled(
                    round(seg.start + shift, 2),
                    round(seg.end + shift, 2),
                    label,
                    seg.text.strip(),
                    seg.confidence,
                    [{"start": round(w.start + shift, 2), "end": round(w.end + shift, 2), "word": w.word, "p": w.probability} for w in seg.words],
                )
            )
    merged.sort(key=lambda s: (s.start_s, s.speaker, s.end_s))
    return merged


def coalesce(segments: list[Labelled], gap_s: float = 0.6, max_chars: int = 400) -> list[Labelled]:
    """Join consecutive segments of the same speaker separated by a short pause (nicer transcripts)."""
    out: list[Labelled] = []
    for seg in segments:
        last = out[-1] if out else None
        if last and last.speaker == seg.speaker and seg.start_s - last.end_s <= gap_s and len(last.text) + len(seg.text) <= max_chars:
            last.end_s = max(last.end_s, seg.end_s)
            last.text = f"{last.text} {seg.text}".strip()
            last.confidence = round((last.confidence + seg.confidence) / 2, 3)
            last.words.extend(seg.words)
        else:
            out.append(Labelled(seg.start_s, seg.end_s, seg.speaker, seg.text, seg.confidence, list(seg.words)))
    return out
