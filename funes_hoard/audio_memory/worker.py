"""One background thread runs every transcription job (live chunks first, then final passes)."""

from __future__ import annotations

import itertools
import logging
import queue
import threading
from typing import Callable

log = logging.getLogger("scribe.worker")

PRIORITY_LIVE = 0
PRIORITY_FINAL = 1


class TranscriptionWorker:
    def __init__(self):
        self._queue: queue.PriorityQueue = queue.PriorityQueue()
        self._seq = itertools.count()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._busy = ""
        self.last_error = ""
        self.done = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="scribe-transcribe", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._queue.put((-1, next(self._seq), "", None))
        if self._thread:
            self._thread.join(timeout=5)

    def submit(self, label: str, fn: Callable[[], None], priority: int = PRIORITY_FINAL) -> None:
        self._queue.put((priority, next(self._seq), label, fn))

    @property
    def depth(self) -> int:
        return self._queue.qsize() + (1 if self._busy else 0)

    @property
    def busy(self) -> str:
        return self._busy

    def wait_idle(self, timeout: float = 60.0) -> bool:
        """Tests: block until the queue drains."""
        import time

        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.depth == 0:
                return True
            time.sleep(0.05)
        return False

    def _run(self) -> None:
        while not self._stop.is_set():
            _priority, _seq, label, fn = self._queue.get()
            if fn is None:
                break
            self._busy = label
            try:
                fn()
                self.done += 1
            except Exception as error:  # a failing job must never kill the worker
                self.last_error = f"{label}: {error}"
                log.exception("job %s failed", label)
            finally:
                self._busy = ""
