"""TXT / SRT / Markdown renderings of a session transcript."""

from __future__ import annotations

from datetime import datetime

from funes_hoard.hoard_link.media.subs import to_srt

FORMATS = ("txt", "srt", "md")
MIME = {"txt": "text/plain; charset=utf-8", "srt": "application/x-subrip; charset=utf-8", "md": "text/markdown; charset=utf-8"}


def hms(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def _stamp(started_at: float | None) -> str:
    return datetime.fromtimestamp(started_at).strftime("%Y-%m-%d %H:%M") if started_at else ""


def render_txt(session: dict, segments: list[dict]) -> str:
    lines = [session.get("title") or "Sesión sin título", _stamp(session.get("started_at")), ""]
    for seg in segments:
        lines.append(f"[{hms(seg['start_s'])}] {seg['speaker']}: {seg['text']}")
    if session.get("notes"):
        lines += ["", "Notas:", session["notes"]]
    return "\n".join(line for line in lines if line is not None).rstrip() + "\n"


def render_srt(session: dict, segments: list[dict]) -> str:
    """SubRip through the family's writer: milliseconds rounded before the fields are split, a cue lasts at least half a second."""
    cues = [{"start_s": seg["start_s"], "end_s": seg["end_s"], "text": seg["text"], "speaker": seg["speaker"]} for seg in segments]
    return to_srt(cues, speakers=True, min_duration_s=0.5) or "\n"


def render_md(session: dict, segments: list[dict], minutes: dict | None = None) -> str:
    title = session.get("title") or "Sesión sin título"
    meta = [f"- Fecha: {_stamp(session.get('started_at'))}", f"- Tipo: {session.get('kind', 'other')}", f"- Duración: {hms(session.get('duration_s', 0))}"]
    if session.get("tags"):
        meta.append("- Etiquetas: " + ", ".join(session["tags"]))
    lines = [f"# {title}", "", *meta, ""]
    if minutes:
        from .minutes import render_minutes_md

        lines += [render_minutes_md(session, minutes, heading=2).rstrip(), ""]
    lines += ["## Transcripción", ""]
    current = None
    for seg in segments:
        if seg["speaker"] != current:
            current = seg["speaker"]
            lines += ["", f"**{current}**", ""]
        lines.append(f"- `{hms(seg['start_s'])}` {seg['text']}")
    if session.get("notes"):
        lines += ["", "## Notas", "", session["notes"]]
    return "\n".join(lines).rstrip() + "\n"


def render(fmt: str, session: dict, segments: list[dict], minutes: dict | None = None) -> str:
    if fmt == "txt":
        return render_txt(session, segments)
    if fmt == "srt":
        return render_srt(session, segments)
    if fmt == "md":
        return render_md(session, segments, minutes)
    raise ValueError(f"Unknown export format: {fmt}")
