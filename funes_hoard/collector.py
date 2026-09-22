"""The background sampler thread: probe -> privacy -> spans -> sqlite.

Kept separate from the pure `core.spans` logic so that thread lifecycle,
flushing and persistence can be tested with a `FakeProbe` and a real
temp-dir database, without touching FastAPI.
"""
from __future__ import annotations

import json
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
    """Names of the git repos the commits source knows about.

    These come from repos actually discovered under the configured roots
    (stored by the git poller) and from recorded commits -- never from the
    root folder itself, which is usually a parent like "Projects" that
    would otherwise be matched as a project in every title.
    """
    names = {r["repo"] for r in db.query("SELECT DISTINCT repo FROM commits")}
    try:
        stored = json.loads(db.get_meta("known_repos", "[]") or "[]")
        names.update(n for n in stored if isinstance(n, str))
    except ValueError:
        pass
    return sorted(n for n in names if n)


class Collector:
    """Owns one `SpanBuilder`, samples a `Probe` every `interval_s`, applies
    privacy rules before anything is persisted, classifies, and writes to
    the database with crash-safe periodic flushing of the open span."""

    def __init__(self, db: Database, probe: Probe, interval_s: float = 1.0) -> None:
        self.db = db
        self.probe = probe
        self.interval_s = interval_s
        self.builder = SpanBuilder(away_after_s=float(db.get_meta("away_after_s", "120")))
        # B2: an away span may never be back-dated before recording existed.
        # `MAX(end_ts)` covers a restart (nothing before what was already
        # persisted); when there is no history at all yet, the builder itself
        # floors a first deeply-idle sample at its own timestamp.
        last_end = db.query_one("SELECT MAX(end_ts) m FROM spans")["m"]
        if last_end is not None:
            self.builder.raise_floor(float(last_end))
        self._open_row_id: Optional[int] = None
        self._last_flush = 0.0
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._pause_lock = threading.Lock()
        self.last_idle_s: float = 0.0
        self.last_locked: bool = False
        # A previous process that crashed (or was killed) left its open span
        # flagged open; it can never be continued, so mark it closed at the
        # last flushed end_ts rather than reporting it as "now" forever.
        db.execute("UPDATE spans SET open = 0 WHERE open = 1")

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
            # Close at the last real sample: the wall clock may be far ahead
            # (e.g. stopping right after resume from sleep).
            span = self.builder.close_open(at_ts=self.builder.last_sample_ts)
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

    def paused_until(self, now: Optional[float] = None) -> Optional[float]:
        """Epoch seconds the pause ends, or None (not paused / until resumed)."""
        if not self.is_paused(now):
            return None
        raw = self.db.get_meta("paused_until", "")
        return None if raw == "inf" else float(raw)

    def pause_at_least(self, minutes: float, now: Optional[float] = None) -> tuple[Optional[float], bool]:
        """Pause for `minutes` unless recording is already paused for longer.

        This is the agent's pause: it may only ever *extend* a pause, never
        shorten one the human set or turn "until resumed" into a timed pause
        (which would amount to resuming early). Returns (until, extended);
        until is None for a pause that lasts until the human resumes it.
        """
        now = now if now is not None else time.time()
        with self._pause_lock:
            if self.is_paused(now):
                raw = self.db.get_meta("paused_until", "")
                if raw == "inf":
                    return None, False
                current = float(raw)
                if current >= now + minutes * 60.0:
                    return current, False
            return self.pause(minutes, now), True

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
                self._interrupt(sample.ts)
                return
            exclude_rules = _load_privacy_rules(self.db, "exclude")
            redact_rules = _load_privacy_rules(self.db, "redact")
            cleaned = apply_privacy(sample, exclude_rules, redact_rules)
            if cleaned is None:
                self._interrupt(sample.ts)
                return
            closed = self.builder.add_sample(cleaned)
            for span in closed:
                self._persist_close(span)
            self._maybe_flush_open(sample.ts)

    def _interrupt(self, ts: float) -> None:
        span = self.builder.interrupt(ts)
        if span is not None:
            self._persist_close(span)

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
