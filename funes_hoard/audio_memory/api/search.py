"""Full-text search across transcripts and tag listing."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from ..store import KINDS
from .deps import bounds, services

router = APIRouter(prefix="/api")


@router.get("/search")
def search(request: Request, q: str = Query(..., min_length=1, max_length=200), kind: str | None = None, from_: str | None = Query(None, alias="from"), to: str | None = None, limit: int = Query(60, ge=1, le=300)):
    if kind and kind not in KINDS:
        raise HTTPException(400, f"kind must be one of {', '.join(KINDS)}")
    start, end = bounds(from_, to)
    return services(request).sessions.search(q, kind, start, end, limit)


@router.get("/tags")
def tags(request: Request):
    return {"tags": services(request).sessions.tags()}
