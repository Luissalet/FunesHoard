"""Shared helpers for the API routers."""

from __future__ import annotations

from fastapi import HTTPException, Request

from ..services import Services
from ..timeparse import parse_bound


def services(request: Request) -> Services:
    return request.app.state.services


def bounds(start: str | None, end: str | None) -> tuple[float | None, float | None]:
    try:
        return parse_bound(start), parse_bound(end, end=True)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


def require_session(svc: Services, session_id: str) -> dict:
    session = svc.sessions.get(session_id)
    if session is None:
        raise HTTPException(404, f"Unknown session: {session_id}")
    return session
