"""The background sampler thread: probe -> privacy -> spans -> sqlite.

Kept separate from the pure `core.spans` logic so that thread lifecycle,
flushing and persistence can be tested with a `FakeProbe` and a real
temp-dir database, without touching FastAPI.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import List, Optional

from funes_hoard.collectors.base import Probe
from funes_hoard.core.classify import ClassifyRule, classify
from funes_hoard.core.privacy import PauseState, PrivacyRule, apply_privacy
from funes_hoard.core.spans import Sample, Span, SpanBuilder
from funes_hoard.db import Database

logger = logging.getLogger("funes_hoard.collector")

FLUSH_INTERVAL_S = 30.0


def _load_classify_rules(db: Database) -> List[ClassifyRule]:
    rows = db.query("SELECT * FROM classify_rules ORDER BY order_idx ASC")
    return [
        ClassifyRule(
            id=r["id"], order_idx=r["order_idx"], match_type=r["match_type"],
            pattern=r["pattern"], category=r["category"], project=r["project"],
            enabled=bool(r["enabled"]),
        )
        for r in rows
    ]


def _load_privacy_rules(db: Database, kind: str) -> List[PrivacyRule]:
    rows = db.query("SELECT * FROM privacy_rules WHERE kind = ?", (kind,))
    return [
        PrivacyRule(id=r["id"], kind=r["kind"], match_type=r["match_type"], pattern=r["pattern"], enabled=bool(r["enabled"]))
        for r in rows
    ]


def _known_repo_names(db: Database) -> List[str]:
    rows = db.query("SELECT path FROM commit_repos WHERE enabled = 1")
    names = []
    for r in rows:
        p = r["path"].replace("\\", "/").rstrip("/")
        if p:
            names.append(p.rsplit("/", 1)[-1])
    return names


class Collector:
    """Owns one `SpanBuilder`, samples a `Probe` every `interval_s`, applies
    privacy rules before anything is persisted, classifies, and writes to
    the database with crash-safe periodic flushing of the open span."""

    def __init__(self, db: Database, probe: Probe, interval_s: float = 1.0) -> None:
        self.db = db
        self.probe = probe
        self.interval_s = interval_s
        self.builder = SpanBuilder(away_after_s=float(db.get_meta("away_after_s", "120")))
        self._open_row_id: Optional[int] = None
        self._last_flush = 0.0
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.last_idle_s: float = 0.0
        self.last_locked: bool = False

    # -- lifecycle -----------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="funes-collector", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        with self._lock:
            span = self.builder.close_open(at_ts=time.time())
            if span is not None:
                self._persist_close(span)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # pragma: no cover - defensive
                logger.exception("collector tick failed")
            self._stop.wait(self.interval_s)

    # -- one sampling step (also called directly by tests/demo) --------
    def is_paused(self, now: Optional[float] = None) -> bool:
        now = now if now is not None else time.time()
        raw = self.db.get_meta("paused_until", "")
        if not raw:
            return False
        if raw == "inf":
            return True  # paused until resumed: never auto-expires
        try:
            until = float(raw)
        except ValueError:
            return False
        if now >= until:
            self.db.set_meta("paused_until", "")
            return False
        return True

    def pause(self, minutes: float, now: Optional[float] = None) -> float:
        now = now if now is not None else time.time()
        until = now + minutes * 60.0
        self.db.set_meta("paused_until", str(until))
        return until

    def pause_indefinitely(self) -> None:
        """Pause until a human explicitly resumes it (no auto-expiry)."""
        self.db.set_meta("paused_until", "inf")

    def tick(self, now: Optional[float] = None) -> None:
        with self._lock:
            sample = self.probe.sample()
            if now is not None:
                sample.ts = now
            self.last_idle_s = sample.idle_s
            self.last_locked = sample.locked
            if self.is_paused(sample.ts):
                return
            exclude_rules = _load_privacy_rules(self.db, "exclude")
            redact_rules = _load_privacy_rules(self.db, "redact")
            cleaned = apply_privacy(sample, exclude_rules, redact_rules)
            if cleaned is None:
                return
            closed = self.builder.add_sample(cleaned)
            for span in closed:
                self._persist_close(span)
            self._maybe_flush_open(sample.ts)

    def _classify(self, span: Span) -> tuple[str, Optional[str]]:
        if span.kind != "active":
            return ("System" if span.kind == "locked" else "Other"), None
        rules = _load_classify_rules(self.db)
        repos = _known_repo_names(self.db)
        return classify(span.app, span.exe, span.title, rules, repos)

    def _rules_version(self) -> int:
        try:
            return int(self.db.get_meta("rules_version", "1"))
        except ValueError:
            return 1

    def _persist_close(self, span: Span) -> None:
        category, project = self._classify(span)
        version = self._rules_version()
        if self._open_row_id is not None:
            self.db.execute(
                "UPDATE spans SET end_ts=?, category=?, project=?, rules_version=?, open=0 WHERE id=?",
                (span.end_ts, category, project, version, self._open_row_id),
            )
            row_id = self._open_row_id
            self._open_row_id = None
        else:
            row_id = self.db.execute(
                "INSERT INTO spans(start_ts, end_ts, kind, app, exe, title, pid, category, project, rules_version, open)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
                (span.start_ts, span.end_ts, span.kind, span.app, span.exe, span.title, span.pid, category, project, version),
            )
        if span.kind == "active" and span.title and span.title != "[redacted]":
            self.db.index_text("span", row_id, span.start_ts, span.title)

    def _maybe_flush_open(self, now: float) -> None:
        open_span = self.builder.peek_open()
        if open_span is None:
            self._open_row_id = None
            return
        if self._open_row_id is None:
            category, project = self._classify(open_span)
            self._open_row_id = self.db.execute(
                "INSERT INTO spans(start_ts, end_ts, kind, app, exe, title, pid, category, project, rules_version, open)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
                (
                    open_span.start_ts, open_span.end_ts, open_span.kind, open_span.app,
                    open_span.exe, open_span.title, open_span.pid, category, project, self._rules_version(),
                ),
            )
            self._last_flush = now
            return
        if (now - self._last_flush) >= FLUSH_INTERVAL_S:
            self.db.execute("UPDATE spans SET end_ts=? WHERE id=?", (open_span.end_ts, self._open_row_id))
            self._last_flush = now
