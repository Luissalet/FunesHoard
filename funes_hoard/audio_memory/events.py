"""Tiny in-process pub/sub used by the SSE endpoint: one queue per subscriber, keyed by session."""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field


@dataclass
class Subscription:
    session_id: str
    queue: "queue.Queue[tuple[str, dict]]" = field(default_factory=lambda: queue.Queue(maxsize=1000))


class EventBus:
    def __init__(self):
        self._subs: dict[str, list[Subscription]] = {}
        self._lock = threading.Lock()

    def subscribe(self, session_id: str) -> Subscription:
        sub = Subscription(session_id)
        with self._lock:
            self._subs.setdefault(session_id, []).append(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        with self._lock:
            subs = self._subs.get(sub.session_id, [])
            if sub in subs:
                subs.remove(sub)
            if not subs:
                self._subs.pop(sub.session_id, None)

    def publish(self, session_id: str, event: str, data: dict) -> None:
        with self._lock:
            subs = list(self._subs.get(session_id, []))
        for sub in subs:
            try:
                sub.queue.put_nowait((event, data))
            except queue.Full:
                pass
