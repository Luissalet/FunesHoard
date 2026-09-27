"""Server-sent events for a session: `segment` per new segment, `status`, then `done` (or `deleted`)."""

from __future__ import annotations

import asyncio
import json
import queue
from typing import AsyncIterator, Awaitable, Callable

from .services import Services

POLL_S = 0.2
KEEPALIVE_S = 15.0


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def live_events(svc: Services, session_id: str, is_disconnected: Callable[[], Awaitable[bool]] | None = None, keepalive_s: float = KEEPALIVE_S, poll_s: float = POLL_S) -> AsyncIterator[str]:
    session = svc.sessions.get(session_id)
    if session is None:
        yield sse("done", {"status": "deleted"})
        return
    sub = svc.bus.subscribe(session_id)
    idle = 0.0
    try:
        yield sse("status", {"status": session["status"]})
        if session["status"] in ("done", "failed"):
            yield sse("done", {"status": session["status"]})
            return
        while True:
            if is_disconnected is not None and await is_disconnected():
                return
            try:
                event, data = sub.queue.get_nowait()
            except queue.Empty:
                current = svc.sessions.get(session_id)
                if current is None or current["status"] in ("done", "failed"):
                    # drain anything published between the check and now, then close
                    while not sub.queue.empty():
                        event, data = sub.queue.get_nowait()
                        if event != "done":
                            yield sse(event, data)
                    yield sse("done", {"status": current["status"] if current else "deleted"})
                    return
                await asyncio.sleep(poll_s)
                idle += poll_s
                if idle >= keepalive_s:
                    idle = 0.0
                    yield ": keepalive\n\n"
                continue
            idle = 0.0
            yield sse(event, data)
            if event == "done":
                return
    finally:
        svc.bus.unsubscribe(sub)
