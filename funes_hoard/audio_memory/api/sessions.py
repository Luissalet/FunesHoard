"""Sessions: start/stop, list, detail, edit, delete, live SSE, audio, peaks, export, import."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from ..audio.wav import peaks as compute_peaks, read_wav
from ..export import FORMATS, MIME, render
from ..importer import MAX_UPLOAD_BYTES
from ..live import live_events
from ..settings import Kind, Language
from ..store import KINDS
from .deps import bounds, require_session, services

router = APIRouter(prefix="/api/sessions")


class StartBody(BaseModel):
    title: str = Field("", max_length=200)
    kind: Kind = "meeting"
    sources: dict[str, bool] = Field(default_factory=lambda: {"mic": True, "system": True})
    language: Language = "auto"


class PatchBody(BaseModel):
    title: str | None = Field(None, max_length=200)
    notes: str | None = Field(None, max_length=20000)
    tags: list[str] | None = Field(None, max_length=30)
    kind: Kind | None = None


def _lookup(error: Exception) -> HTTPException:
    if isinstance(error, LookupError):
        return HTTPException(404, str(error))
    if isinstance(error, ValueError):
        return HTTPException(409, str(error))
    return HTTPException(503, str(error))


@router.post("/start", status_code=201)
def start(request: Request, body: StartBody):
    svc = services(request)
    try:
        return svc.recorder.start(body.title, body.kind, bool(body.sources.get("mic")), bool(body.sources.get("system")), body.language)
    except (LookupError, ValueError, RuntimeError) as error:
        raise _lookup(error) from error


@router.post("/{session_id}/stop")
def stop(request: Request, session_id: str):
    try:
        return services(request).recorder.stop(session_id)
    except (LookupError, ValueError) as error:
        raise _lookup(error) from error


@router.get("")
def list_sessions(request: Request, q: str = "", kind: str | None = None, tag: str | None = None, from_: str | None = Query(None, alias="from"), to: str | None = None, status: str | None = None, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
    if kind and kind not in KINDS:
        raise HTTPException(400, f"kind must be one of {', '.join(KINDS)}")
    start_at, end_at = bounds(from_, to)
    svc = services(request)
    rows = svc.sessions.list(q, kind, tag, start_at, end_at, status, limit, offset)
    for row in rows:
        row["first_line"] = svc.sessions.first_line(row["id"])
    return {"sessions": rows}


@router.get("/{session_id}")
def detail(request: Request, session_id: str):
    svc = services(request)
    session = require_session(svc, session_id)
    return {**session, "segments": svc.sessions.segments(session_id)}


@router.patch("/{session_id}")
def patch(request: Request, session_id: str, body: PatchBody):
    svc = services(request)
    require_session(svc, session_id)
    return svc.sessions.update(session_id, **body.model_dump(exclude_none=True))


@router.delete("/{session_id}")
def delete(request: Request, session_id: str):
    svc = services(request)
    require_session(svc, session_id)
    if svc.recorder.current and svc.recorder.current.session_id == session_id:
        raise HTTPException(409, "Stop the recording before deleting it.")
    return {"ok": svc.sessions.delete(session_id)}


@router.post("/{session_id}/retranscribe")
def retranscribe(request: Request, session_id: str):
    svc = services(request)
    session = require_session(svc, session_id)
    if session["status"] == "recording":
        raise HTTPException(409, "The session is still recording.")
    svc.pipeline.enqueue_final(session_id)
    return {"ok": True, "queue_depth": svc.worker.depth}


@router.get("/{session_id}/live")
async def live(request: Request, session_id: str):
    """Server-sent events: `segment` for each new live/final segment, `status`, `done`."""
    svc = services(request)
    require_session(svc, session_id)
    stream = live_events(svc, session_id, request.is_disconnected)
    return StreamingResponse(stream, media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _audio_file(svc, session: dict) -> Path:
    path = Path(session["audio_path"]) if session["audio_path"] else None
    if path is None or not path.is_file():
        folder = svc.sessions.session_dir(session["id"])
        for name in ("audio.wav", "mic.wav", "system.wav"):
            if (folder / name).is_file():
                return folder / name
        raise HTTPException(404, "No audio for this session yet.")
    return path


@router.get("/{session_id}/audio")
def audio(request: Request, session_id: str):
    svc = services(request)
    path = _audio_file(svc, require_session(svc, session_id))
    return FileResponse(path, media_type="audio/wav", filename=f"{session_id}.wav")


@router.get("/{session_id}/peaks")
def peaks(request: Request, session_id: str, n: int = Query(400, ge=20, le=2000)):
    svc = services(request)
    session = require_session(svc, session_id)
    cache = svc.sessions.session_dir(session_id) / f"peaks-{n}.json"
    if cache.is_file() and session["status"] == "done":
        return json.loads(cache.read_text(encoding="utf-8"))
    samples, rate = read_wav(_audio_file(svc, session))
    result = {"peaks": compute_peaks(samples, n), "duration_s": round(len(samples) / rate, 2)}
    if session["status"] == "done":
        cache.write_text(json.dumps(result), encoding="utf-8")
    return result


@router.get("/{session_id}/export")
def export(request: Request, session_id: str, format: str = Query("txt", alias="format")):
    if format not in FORMATS:
        raise HTTPException(400, f"format must be one of {', '.join(FORMATS)}")
    svc = services(request)
    session = require_session(svc, session_id)
    text = render(format, session, svc.sessions.segments(session_id))
    return PlainTextResponse(text, media_type=MIME[format], headers={"Content-Disposition": f'attachment; filename="{session_id}.{format}"'})


import_router = APIRouter(prefix="/api")


@import_router.post("/import", status_code=201)
async def import_file(request: Request, file: UploadFile = File(...), title: str = Form(""), kind: str = Form("other"), language: str = Form("auto")):
    if kind not in KINDS:
        raise HTTPException(400, f"kind must be one of {', '.join(KINDS)}")
    if language not in ("auto", "es", "en"):
        raise HTTPException(400, "language must be auto, es or en")
    svc = services(request)
    svc.config.data_dir.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(delete=False, dir=svc.config.data_dir, suffix=Path(file.filename or "upload").suffix.lower())
    size = 0
    with handle:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                Path(handle.name).unlink(missing_ok=True)
                raise HTTPException(413, "File too large.")
            handle.write(chunk)
    try:
        return await asyncio.to_thread(svc.importer.import_file, Path(handle.name), file.filename or "upload.wav", title[:200], kind, language)
    except ValueError as error:
        Path(handle.name).unlink(missing_ok=True)
        raise HTTPException(400, str(error)) from error
