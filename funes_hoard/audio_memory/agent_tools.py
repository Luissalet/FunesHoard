"""Tools exposed to the assistant. One list drives /api/agent/* and mcp_server.py."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field

from .export import FORMATS, hms, render
from .importer import ALLOWED_EXT, MAX_UPLOAD_BYTES
from .minutes import family_view
from .services import Services
from .timeparse import TIME_HELP, iso_local, parse_bound

AGENT_INSTRUCTIONS = """Funes Audio records the user's meetings, interviews and voice notes on their own PC and transcribes them locally.
Quote transcripts as transcripts: automatic speech recognition may contain errors, so never present a line as a verbatim fact and prefer "según la transcripción".
Always give timestamps (mm:ss) and the session title when citing what someone said; the user can jump to that moment.
Never summarise a session you have not read with scribe_transcript; scribe_sessions only gives titles and first lines. Page through long transcripts with from_s/to_s until you have read what the user asked about.
Speaker labels "yo" and "otros" come from the audio channel (microphone vs. system loopback), not from voice recognition: "otros" may be several people. "S1" means a single-track recording with no speaker information.
Never start a recording unless the user explicitly asks for it in the current message. When you start one, say clearly that recording has started and how to stop it (scribe_stop or the app). scribe_delete is permanent: confirm first.
Prefer scribe_search to find a topic across sessions, then scribe_transcript around the hit for context.
scribe_minutes gives the minutes (acta) of a transcribed session: summary, decisions, action items and open questions. They were written by a local model and checked against the transcript; every action item carries a literal quote with its time, so cite that quote and time, and say "según el acta" rather than presenting the summary as certain. status "no_model" means no local language model is available: say so, never write minutes yourself as if they were stored ones.
minutes_get gives the same minutes in a compact shape for other apps (title, date, attendees, action items with owner and due, summary).
scribe_import_file turns an audio or video file already on this computer into a session (the original is never moved or deleted); with wait_s it waits for the transcript."""

KindArg = Literal["meeting", "interview", "note", "other"]
LangArg = Literal["auto", "es", "en"]


class Empty(BaseModel):
    pass


class SessionsArgs(BaseModel):
    q: str | None = Field(None, max_length=200, description="Words in the title or transcript.")
    kind: KindArg | None = Field(None, description="meeting | interview | note | other")
    tag: str | None = Field(None, max_length=40)
    from_: str | None = Field(None, alias="from", description=f"Sessions started after this. {TIME_HELP}")
    to: str | None = Field(None, description=f"Sessions started before this. {TIME_HELP}")
    limit: int = Field(20, ge=1, le=100)
    model_config = {"populate_by_name": True}


class TranscriptArgs(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=64)
    from_s: float | None = Field(None, ge=0, description="Start offset in seconds (page through long sessions).")
    to_s: float | None = Field(None, ge=0, description="End offset in seconds.")
    max_chars: int = Field(12000, ge=500, le=60000, description="Cut the page here; the reply tells you where to continue.")


class SearchArgs(BaseModel):
    q: str = Field(..., min_length=1, max_length=200, description="Words to find (all must appear; last word matches as prefix).")
    kind: KindArg | None = None
    from_: str | None = Field(None, alias="from", description=f"Sessions started after this. {TIME_HELP}")
    to: str | None = Field(None, description=f"Sessions started before this. {TIME_HELP}")
    limit: int = Field(40, ge=1, le=200)
    model_config = {"populate_by_name": True}


class StartArgs(BaseModel):
    title: str = Field("", max_length=200)
    kind: KindArg = "meeting"
    mic: bool = Field(True, description="Capture the microphone (the user, labelled 'yo').")
    system: bool = Field(True, description="Capture system audio / loopback (the others, labelled 'otros'). Windows only.")
    language: LangArg = "auto"


class SessionIdArgs(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=64)


class NoteArgs(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=64)
    notes: str = Field(..., min_length=1, max_length=20000, description="Text appended to the session notes.")


class TagArgs(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=64)
    add: list[str] = Field(default_factory=list, description="Tags to add (lower-cased, deduplicated).")
    remove: list[str] = Field(default_factory=list)


class ExportArgs(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=64)
    format: Literal["txt", "srt", "md"] = "md"


class MinutesArgs(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=64)
    regenerate: bool = Field(False, description="Write the minutes again with the model even if some are stored (the old ones are replaced).")


class MinutesGetArgs(BaseModel):
    minutes_id: str = Field(..., min_length=1, max_length=64, description="Id of the meeting session whose minutes you want (the minutes_id of funes.minutes.ready).")
    generate: bool = Field(True, description="Write the minutes with the local model when none are stored. false only reads what is stored.")


class ImportFileArgs(BaseModel):
    path: str = Field(..., min_length=1, max_length=1024, description="Absolute path of an audio/video file on this computer (wav, mp3, m4a, mp4, mkv, webm...).")
    title: str = Field("", max_length=200)
    kind: KindArg = "other"
    language: LangArg = "auto"
    wait_s: float = Field(0, ge=0, le=3600, description="Seconds to wait for the transcription to finish. 0 returns the session at once; poll scribe_sessions or scribe_transcript later.")


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    annotations: dict[str, bool]
    run: Callable[[Services, Any], Any]


def _ann(read_only: bool, destructive: bool = False, idempotent: bool | None = None) -> dict[str, bool]:
    return {"readOnlyHint": read_only, "destructiveHint": destructive, "idempotentHint": read_only if idempotent is None else idempotent, "openWorldHint": False}


def _session_brief(svc: Services, s: dict) -> dict:
    return {
        "id": s["id"],
        "title": s["title"],
        "kind": s["kind"],
        "status": s["status"],
        "started_at": iso_local(s["started_at"]),
        "duration": hms(s["duration_s"]),
        "duration_s": s["duration_s"],
        "sources": s["sources"],
        "tags": s["tags"],
        "first_line": svc.sessions.first_line(s["id"]),
        "no_speech": bool(s.get("stats", {}).get("no_speech")),
    }


def _require(svc: Services, session_id: str) -> dict:
    session = svc.sessions.get(session_id)
    if session is None:
        raise LookupError(f"Unknown session: {session_id}")
    return session


def run_status(svc: Services, _: Empty):
    st = svc.status()
    return {
        "recording": st["recording"],
        "backend": st["backend"],
        "devices": {"mic": [d["name"] for d in st["devices"]["mic"]], "system": [d["name"] for d in st["devices"]["system"]]},
        "transcriber": st["transcriber"],
        "queue_depth": st["queue_depth"],
        "sessions_total": st["sessions_total"],
        "last_error": st["last_error"],
    }


def run_sessions(svc: Services, a: SessionsArgs):
    rows = svc.sessions.list(a.q or "", a.kind, a.tag, parse_bound(a.from_), parse_bound(a.to, end=True), limit=a.limit)
    return {"total": len(rows), "sessions": [_session_brief(svc, s) for s in rows]}


def run_transcript(svc: Services, a: TranscriptArgs):
    session = _require(svc, a.session_id)
    segments = svc.sessions.segments(a.session_id, a.from_s, a.to_s)
    lines, used, next_from = [], 0, None
    for seg in segments:
        line = {"t": hms(seg["start_s"]), "start_s": seg["start_s"], "end_s": seg["end_s"], "speaker": seg["speaker"], "text": seg["text"], "live": seg["live"]}
        cost = len(seg["text"]) + 24
        if used + cost > a.max_chars and lines:
            next_from = seg["start_s"]
            break
        lines.append(line)
        used += cost
    return {
        "session": _session_brief(svc, session),
        "status": session["status"],
        "notes": session["notes"],
        "segments": lines,
        "next_from_s": next_from,
        "note": ("No speech was detected in this session (silence or noise only); there is nothing to quote. " if session.get("stats", {}).get("no_speech") else "")
        + "Speaker labels come from the audio channel (yo = microphone, otros = system audio). Live segments are provisional; segments with hallucination signals were dropped.",
    }


def run_search(svc: Services, a: SearchArgs):
    result = svc.sessions.search(a.q, a.kind, parse_bound(a.from_), parse_bound(a.to, end=True), limit=a.limit)
    for entry in result["sessions"]:
        entry["session"]["started_at"] = iso_local(entry["session"]["started_at"])
        for hit in entry["hits"]:
            hit["t"] = hms(hit["start_s"])
    return result


def run_start(svc: Services, a: StartArgs):
    session = svc.recorder.start(a.title, a.kind, a.mic, a.system, a.language)
    return {"started": True, "session": _session_brief(svc, session), "message": f"Recording '{session['title']}' has started (tracks: {', '.join(svc.recorder.current.tracks) if svc.recorder.current else ''}). Stop it with scribe_stop session_id={session['id']} or from the app."}


def run_stop(svc: Services, a: SessionIdArgs):
    session = svc.recorder.stop(a.session_id)
    return {"stopped": True, "session": _session_brief(svc, session), "message": "Recording stopped; the final transcription is running in the background. Read it with scribe_transcript when status is done."}


def run_note(svc: Services, a: NoteArgs):
    _require(svc, a.session_id)
    session, existing = svc.sessions.append_notes_once(a.session_id, a.notes)
    return {"ok": True, "notes": session["notes"], "existing": existing}


def run_tag(svc: Services, a: TagArgs):
    session = _require(svc, a.session_id)
    tags = [t for t in session["tags"] if t not in {r.strip().lower() for r in a.remove}] + [t for t in a.add]
    updated = svc.sessions.update(a.session_id, tags=tags)
    return {"ok": True, "tags": updated["tags"]}


def run_export(svc: Services, a: ExportArgs):
    session = _require(svc, a.session_id)
    return {"format": a.format, "text": render(a.format, session, svc.sessions.segments(a.session_id), svc.minutes.get(a.session_id) if a.format == "md" else None)}


def _agent_minutes(minutes: dict) -> dict:
    """The minutes as the assistant should read them: local ISO time, hh:mm:ss marks next to the seconds."""
    out = dict(minutes)
    out["started_at"] = iso_local(minutes.get("started_at"))
    out["created_at"] = iso_local(minutes.get("created_at"))
    out["action_items"] = [{**item, "evidence": {**item["evidence"], "t": hms(item["evidence"]["start_s"])}} for item in minutes.get("action_items", [])]
    return out


def run_minutes(svc: Services, a: MinutesArgs):
    _require(svc, a.session_id)
    result = svc.minutes.generate(a.session_id, regenerate=a.regenerate)
    if result.get("minutes"):
        result["minutes"] = _agent_minutes(result["minutes"])
    return result


def run_minutes_get(svc: Services, a: MinutesGetArgs):
    """The minutes in the family's shape: title, date, attendees, action items with owner and due, summary."""
    _require(svc, a.minutes_id)
    if a.generate:
        result = svc.minutes.generate(a.minutes_id)
    else:
        stored = svc.minutes.get(a.minutes_id)
        result = {"status": "ready", "cached": True, "minutes": stored} if stored else {"status": "not_generated", "detail": "No minutes are stored for this session; call again with generate=true."}
    if result.get("status") != "ready" or not result.get("minutes"):
        return {"ok": False, "status": result.get("status", "error"), "minutes_id": a.minutes_id, "detail": result.get("detail", "")}
    return {"ok": True, "status": "ready", "cached": bool(result.get("cached")), **family_view(a.minutes_id, result["minutes"])}


TRANSCRIPT_TEXT_MAX_CHARS = 60000


def run_import_file(svc: Services, a: ImportFileArgs):
    source = Path(a.path).expanduser()
    if not source.is_absolute():
        raise ValueError("path must be absolute (for example C:\\Users\\me\\Videos\\clase.mp4).")
    if not source.exists():
        raise ValueError(f"No such file: {source}")
    if not source.is_file():
        raise ValueError(f"Not a file: {source}")
    if source.suffix.lower() not in ALLOWED_EXT:
        raise ValueError(f"Unsupported file type {source.suffix.lower() or '(none)'}; allowed: {', '.join(sorted(ALLOWED_EXT))}.")
    size = source.stat().st_size
    if size == 0:
        raise ValueError("The file is empty.")
    if size > MAX_UPLOAD_BYTES:
        raise ValueError(f"The file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB, the import limit.")
    session = svc.importer.import_file(source, source.name, a.title, a.kind, a.language, keep_source=True)
    sid = session["id"]
    deadline = time.monotonic() + a.wait_s
    while a.wait_s > 0 and time.monotonic() < deadline:
        current = svc.sessions.get(sid)
        if current is None or current["status"] in ("done", "failed"):
            break
        time.sleep(0.25)
    session = svc.sessions.get(sid)
    status = session["status"]
    out: dict[str, Any] = {"session": _session_brief(svc, session), "status": status}
    if status == "failed":
        out["error"] = session["error"]
    if a.wait_s <= 0:
        out["message"] = f"Imported; the transcription runs in the background. Read it with scribe_transcript session_id={sid} when status is done."
        return out
    if status == "processing":
        out["message"] = f"Still transcribing after {a.wait_s:g} s. Read it later with scribe_transcript session_id={sid}."
        return out
    if status == "done":
        text, used, next_from = [], 0, None
        for seg in svc.sessions.segments(sid):
            speaker = "" if seg["speaker"] == "S1" else f"{seg['speaker']}: "
            line = f"[{hms(seg['start_s'])}] {speaker}{seg['text']}"
            if used + len(line) + 1 > TRANSCRIPT_TEXT_MAX_CHARS and text:
                next_from = seg["start_s"]
                break
            text.append(line)
            used += len(line) + 1
        out["transcript_text"] = "\n".join(text)
        out["segments"] = len(svc.sessions.segments(sid))
        out["next_from_s"] = next_from
        if next_from is not None:
            out["message"] = f"The transcript was cut at {hms(next_from)}; continue with scribe_transcript session_id={sid} from_s={next_from}."
        elif not text:
            out["message"] = "No speech was detected in the file."
    return out


def run_delete(svc: Services, a: SessionIdArgs):
    _require(svc, a.session_id)
    if svc.recorder.current and svc.recorder.current.session_id == a.session_id:
        raise ValueError("Stop the recording before deleting it.")
    return {"deleted": svc.sessions.delete(a.session_id)}


TOOLS: list[Tool] = [
    Tool("scribe_status", "Is a recording in progress? Backend, devices, model and queue. Keywords: estado, grabando, micrófono.\nWhether a recording is in progress (title, elapsed time, tracks), which audio backend and devices exist, the transcription model and its download state, and queue depth.\nSinónimos: estado, grabando, está grabando, micrófono, modelo, transcripción, dispositivos.", Empty, _ann(True), run_status),
    Tool("scribe_sessions", "List recorded sessions newest first, with filters. Keywords: reuniones, grabaciones, sesiones, notas de voz.\nList recorded sessions (meetings, interviews, voice notes, imports) newest first with title, when, duration, kind, tags and the first transcribed line. Filter by words, kind, tag and dates.\nSinónimos: reunión, entrevista, llamada, nota de voz, sesiones, grabaciones, qué reuniones, ayer, esta semana, lista.", SessionsArgs, _ann(True), run_sessions),
    Tool("scribe_transcript", "Transcript of one session with speakers and timestamps, paginated. Keywords: transcripción, qué se dijo.\nTranscript of one session with speaker labels (yo/otros from the audio channel) and timestamps, paginated by time (from_s/to_s, next_from_s). Read it before summarising or quoting a session.\nSinónimos: transcripción, qué dijeron, qué se dijo, leer la reunión, hablaron de, entrevista, llamada. (Para el acta o los acuerdos usa scribe_minutes.)", TranscriptArgs, _ann(True), run_transcript),
    Tool("scribe_search", "Search every transcript by words, grouped by session. Keywords: cuándo hablamos de, buscar en reuniones.\nFull-text search across every transcript: hits grouped by session with time offset, speaker and a snippet. Best first step for 'when did we talk about X'.\nSinónimos: buscar, hablaron de, cuándo dijimos, salario, presupuesto, mencionaron, en qué reunión, reunión, entrevista, transcripción.", SearchArgs, _ann(True), run_search),
    Tool("scribe_start", "Start recording a new session (only when the user asks now). Keywords: graba, empieza a grabar, reunión.\nStart recording a new session (microphone = 'yo', system audio = 'otros'). Only when the user explicitly asks in the current message; not destructive. Tell the user recording has started and that scribe_stop stops it.\nSinónimos: grabar, empieza a grabar, graba la reunión, graba la entrevista, nota de voz, grabación, llamada.", StartArgs, _ann(False, False, False), run_start),
    Tool("scribe_stop", "Stop the recording in progress; the final transcription runs in the background and replaces the live one.\nSinónimos: parar, detener, deja de grabar, termina la grabación, fin de la reunión.", SessionIdArgs, _ann(False, False, True), run_stop),
    Tool("scribe_note", "Append text to a session's notes (decisions, action items, a summary the user approved).\nAn identical trailing note is returned with existing=true instead of appended again.\nSinónimos: nota, apunta, añade a las notas, apuntes de la sesión.", NoteArgs, _ann(False, False, True), run_note),
    Tool("scribe_tag", "Add or remove tags on a session (idempotent: existing tags are kept once).\nSinónimos: etiqueta, etiquetar, categoría, marcar, cliente, proyecto.", TagArgs, _ann(False, False, True), run_tag),
    Tool("scribe_export", "Render a session as text, SRT or Markdown (md includes notes and minutes). Exportar.\nReturns the transcript as plain text, SRT subtitles or Markdown; the Markdown also carries the notes and, when they exist, the minutes.\nSinónimos: exportar, subtítulos, markdown, texto, descargar la transcripción, exportar el acta.", ExportArgs, _ann(True), run_export),
    Tool("scribe_minutes", "Meeting minutes of a session: summary, decisions, action items with evidence. Acta de reunión.\nWrites (or returns the stored) minutes of a transcribed session with the local model: summary, decisions, action items {owner yo|name, action, counterpart, due_date/due_text, evidence {start_s, end_s, speaker, literal quote}}, open questions and participants. Every action item's quote is verified against the transcript and items without one are dropped. status: ready | no_model (no local language model; nothing was invented) | not_ready (still transcribing) | no_speech | error. regenerate=true replaces stored minutes. Long meetings can take minutes.\nSinónimos: acta, minutas, resumen de la reunión, acuerdos, tareas pendientes, qué se acordó, quién se comprometió, compromisos, action items, decisiones.", MinutesArgs, _ann(False, False, True), run_minutes),
    Tool("minutes_get", "Minutes of a meeting for other apps: title, date, attendees, action items with owner and due, summary.\nReturns the minutes (acta) of a transcribed meeting session in a compact shape: {title, date, attendees: [names], action_items: [{text, owner (yo | a name | empty), counterpart, due (YYYY-MM-DD or null), due_text, quote}], summary, decisions, open_questions}. Writes them with the local model when none are stored (generate=true, the default); status is ready, or no_model, not_ready, no_speech, not_generated. minutes_id is the session id announced by funes.minutes.ready.\nSinónimos: acta, minutas, asistentes, quién estuvo en la reunión, tareas de la reunión, acuerdos, resumen de la reunión.", MinutesGetArgs, _ann(False, False, True), run_minutes_get),
    Tool("scribe_import_file", "Import a local audio/video file as a session and optionally wait for its transcript. Importar audio.\nCreates a session from a file already on this computer, by absolute path (the original is copied, never moved or deleted), and transcribes it in the background. With wait_s > 0 it waits up to that many seconds and returns {session, status, transcript_text} (cut at 60000 characters with next_from_s to continue). Use it to get the text of a recorded class, call or video.\nSinónimos: importar audio, importar vídeo, transcribir un archivo, transcribe este vídeo, pasar a texto, grabación existente, mp3, mp4, subir audio.", ImportFileArgs, _ann(False, False, False), run_import_file),
    Tool("scribe_delete", "Permanently delete a session (only when asked; no undo). Keywords: borrar grabación, eliminar sesión.\nPermanently delete a session: audio, transcript and notes. Only when the user explicitly asks; there is no undo.\nSinónimos: borrar, eliminar, borra la grabación, elimina la reunión, olvidar.", SessionIdArgs, _ann(False, True, True), run_delete),
]
TOOLS_BY_NAME = {t.name: t for t in TOOLS}
assert all(f in FORMATS for f in ("txt", "srt", "md"))


def tool_catalog() -> list[dict]:
    return [
        {"name": t.name, "description": t.description, "annotations": t.annotations, "inputSchema": t.input_model.model_json_schema(by_alias=True)}
        for t in TOOLS
    ]


def call_tool(services: Services, name: str, arguments: dict | None) -> Any:
    tool = TOOLS_BY_NAME.get(name)
    if tool is None:
        raise KeyError(f"Unknown tool: {name}")
    args = tool.input_model.model_validate(arguments or {})
    return tool.run(services, args)
