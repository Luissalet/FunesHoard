"""TXT / SRT / Markdown renderings of a session transcript."""

from __future__ import annotations

from datetime import datetime

FORMATS = ("txt", "srt", "md")
MIME = {"txt": "text/plain; charset=utf-8", "srt": "application/x-subrip; charset=utf-8", "md": "text/markdown; charset=utf-8"}


def hms(seconds: float, millis: bool = False) -> str:
    seconds = max(0.0, float(seconds))
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    if millis:
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{int(round((seconds - int(seconds)) * 1000)):03d}"
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
    blocks = []
    for index, seg in enumerate(segments, start=1):
        end = max(seg["end_s"], seg["start_s"] + 0.5)
        blocks.append(f"{index}\n{hms(seg['start_s'], True)} --> {hms(end, True)}\n{seg['speaker']}: {seg['text']}\n")
    return "\n".join(blocks) + ("" if blocks else "\n")


def render_md(session: dict, segments: list[dict]) -> str:
    title = session.get("title") or "Sesión sin título"
    meta = [f"- Fecha: {_stamp(session.get('started_at'))}", f"- Tipo: {session.get('kind', 'other')}", f"- Duración: {hms(session.get('duration_s', 0))}"]
    if session.get("tags"):
        meta.append("- Etiquetas: " + ", ".join(session["tags"]))
    lines = [f"# {title}", "", *meta, "", "## Transcripción", ""]
    current = None
    for seg in segments:
        if seg["speaker"] != current:
            current = seg["speaker"]
            lines += ["", f"**{current}**", ""]
        lines.append(f"- `{hms(seg['start_s'])}` {seg['text']}")
    if session.get("notes"):
        lines += ["", "## Notas", "", session["notes"]]
    return "\n".join(lines).rstrip() + "\n"


def render(fmt: str, session: dict, segments: list[dict]) -> str:
    if fmt == "txt":
        return render_txt(session, segments)
    if fmt == "srt":
        return render_srt(session, segments)
    if fmt == "md":
        return render_md(session, segments)
    raise ValueError(f"Unknown export format: {fmt}")
