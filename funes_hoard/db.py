"""SQLite storage (stdlib sqlite3, WAL mode).

One shared connection (`check_same_thread=False`) serialised by a
re-entrant lock: the app is a single-user local tool whose heaviest writer
is the 1 Hz collector, so one connection is simpler and cheaper than a
pool. WAL mode keeps an external reader (a backup, a sqlite shell) from
blocking the collector.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS spans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_ts REAL NOT NULL,
    end_ts REAL NOT NULL,
    kind TEXT NOT NULL,
    app TEXT NOT NULL,
    exe TEXT NOT NULL,
    title TEXT NOT NULL,
    pid INTEGER,
    category TEXT NOT NULL DEFAULT 'Other',
    project TEXT,
    rules_version INTEGER NOT NULL DEFAULT 0,
    open INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_spans_start ON spans(start_ts);
CREATE INDEX IF NOT EXISTS idx_spans_open ON spans(open);

CREATE TABLE IF NOT EXISTS classify_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_idx INTEGER NOT NULL,
    match_type TEXT NOT NULL,
    pattern TEXT NOT NULL,
    category TEXT NOT NULL,
    project TEXT,
    enabled INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS privacy_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    match_type TEXT NOT NULL,
    pattern TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS file_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    path TEXT NOT NULL,
    app_hint TEXT
);
CREATE INDEX IF NOT EXISTS idx_file_events_ts ON file_events(ts);

CREATE TABLE IF NOT EXISTS commit_repos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_scan_ts REAL,
    last_repo_count INTEGER
);

-- A1: per-repo scan bookkeeping, one row per `.git` folder discovered under
-- any configured root. `refs_mtime` lets a poll skip a repo whose refs have
-- not changed at all (no git process needed); `last_scan_ts` is this repo's
-- own commit-log checkpoint, only advanced after a scan that actually
-- completed, so a repo whose `git log` times out is retried, not skipped.
CREATE TABLE IF NOT EXISTS git_repo_scan (
    path TEXT PRIMARY KEY,
    refs_mtime REAL NOT NULL,
    last_scan_ts REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS commits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    repo TEXT NOT NULL,
    sha TEXT NOT NULL,
    subject TEXT NOT NULL,
    author TEXT NOT NULL,
    UNIQUE(repo, sha)
);
CREATE INDEX IF NOT EXISTS idx_commits_ts ON commits(ts);

CREATE TABLE IF NOT EXISTS agent_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    tool TEXT NOT NULL,
    args_summary TEXT NOT NULL,
    duration_ms REAL NOT NULL,
    ok INTEGER NOT NULL,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_agent_calls_ts ON agent_calls(ts);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS day_narratives (
    day TEXT PRIMARY KEY,
    day_start_ts REAL NOT NULL,
    text TEXT NOT NULL,
    model TEXT,
    generated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_day_narratives_start ON day_narratives(day_start_ts);
"""

DEFAULT_META = {
    "rules_version": "1",
    "away_after_s": "120",
    "retention_days": "180",
    "paused_until": "",
    "sample_interval_s": "1",
    "write_my_day_enabled": "1",
}


class Database:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "funes.sqlite3"
        self._lock = threading.RLock()
        self._conn = self._new_connection()
        self._init()

    def _new_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def close(self) -> None:
        """Close the shared connection (app shutdown); later calls reopen it."""
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def connect(self) -> sqlite3.Connection:
        """The app's single shared connection.

        Used as `with db.connect() as conn:` -- sqlite3's own context
        manager only commits/rolls back the transaction on exit, it does
        NOT close the connection, so reusing one shared handle here (rather
        than opening a fresh connection for every call, at up to 1 sample/s)
        is both correct and far cheaper.
        """
        if self._conn is None:
            self._conn = self._new_connection()
        return self._conn

    def _init_fts(self, conn: sqlite3.Connection) -> bool:
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5("
                "text, source UNINDEXED, ref_id UNINDEXED, ts UNINDEXED,"
                " tokenize=\"unicode61 remove_diacritics 2\")"
            )
            return True
        except sqlite3.OperationalError:
            return False

    def index_text(self, source: str, ref_id: int, ts: float, text: str) -> None:
        """Best-effort: add a row to the FTS index. No-op if FTS5 is
        unavailable on this platform's sqlite3 build."""
        if not self.fts_available or not text:
            return
        with self._lock, self.connect() as conn:
            conn.execute(
                "INSERT INTO search_fts(text, source, ref_id, ts) VALUES (?, ?, ?, ?)",
                (text, source, ref_id, ts),
            )
            conn.commit()

    def _init(self) -> None:
        with self._lock, self.connect() as conn:
            conn.executescript(SCHEMA)
            self._migrate_columns(conn)
            self.fts_available = self._init_fts(conn)
            for k, v in DEFAULT_META.items():
                conn.execute(
                    "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)", (k, v)
                )
            has_rules = conn.execute("SELECT COUNT(*) c FROM classify_rules").fetchone()["c"]
            if not has_rules:
                from funes_hoard.core.classify import default_rules

                for r in default_rules():
                    conn.execute(
                        "INSERT INTO classify_rules(order_idx, match_type, pattern, category, project, enabled)"
                        " VALUES (?, ?, ?, ?, ?, 1)",
                        (r.order_idx, r.match_type, r.pattern, r.category, r.project),
                    )
            has_priv = conn.execute("SELECT COUNT(*) c FROM privacy_rules").fetchone()["c"]
            if not has_priv:
                from funes_hoard.core.privacy import DEFAULT_EXCLUDE_RULES, DEFAULT_REDACT_RULES

                for r in [*DEFAULT_EXCLUDE_RULES, *DEFAULT_REDACT_RULES]:
                    conn.execute(
                        "INSERT INTO privacy_rules(kind, match_type, pattern, enabled) VALUES (?, ?, ?, 1)",
                        (r.kind, r.match_type, r.pattern),
                    )
            self._migrate_privacy_defaults(conn)
            conn.commit()

    def _migrate_columns(self, conn: sqlite3.Connection) -> None:
        """Add a column a later version needs to a table that already
        existed (`CREATE TABLE IF NOT EXISTS` only helps a brand new one)."""
        try:
            conn.execute("ALTER TABLE commit_repos ADD COLUMN last_repo_count INTEGER")
        except sqlite3.OperationalError:
            pass  # already has it

    def _migrate_privacy_defaults(self, conn: sqlite3.Connection) -> None:
        """Bring a pre-existing database's default privacy rules up to date
        (e.g. the case/accent-insensitive incognito fix) without touching
        anything the user changed. A fresh database is already current, so
        this is a fast no-op there; see `core.privacy.migrate_default_rules`."""
        from funes_hoard.core.privacy import PRIVACY_DEFAULTS_VERSION, migrate_default_rules

        key = "privacy_defaults_version"
        current = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        current_version = int(current["value"]) if current and current["value"] else 1
        if current_version >= PRIVACY_DEFAULTS_VERSION:
            return
        existing = [dict(r) for r in conn.execute("SELECT id, kind, match_type, pattern FROM privacy_rules").fetchall()]
        plan = migrate_default_rules(existing)
        for rule_id, new_pattern in plan["renames"]:
            conn.execute("UPDATE privacy_rules SET pattern = ? WHERE id = ?", (new_pattern, rule_id))
        for rule in plan["adds"]:
            conn.execute(
                "INSERT INTO privacy_rules(kind, match_type, pattern, enabled) VALUES (?, ?, ?, 1)",
                (rule.kind, rule.match_type, rule.pattern),
            )
        conn.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(PRIVACY_DEFAULTS_VERSION)),
        )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """The shared connection, for a caller batching several writes into
        one commit instead of one per row (A1: a git scan inserting a
        repo's commits, say). Holds the same (re-entrant) lock `execute`/
        `query` use, so it is safe alongside the collector's 1 Hz writes."""
        with self._lock:
            conn = self.connect()
            yield conn
            conn.commit()

    def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        with self._lock, self.connect() as conn:
            cur = conn.execute(sql, params)
            conn.commit()
            return cur.lastrowid

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        # No `with conn:` here: used as a context manager, sqlite3 commits
        # on every successful exit even for a plain SELECT -- harmless but
        # not free, and at up to 1 query/s from the collector (plus every
        # git-history import row, before A1) it measurably added up.
        # Nothing is written by a read, so there is nothing to commit.
        with self._lock:
            conn = self.connect()
            return list(conn.execute(sql, params).fetchall())

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> Optional[sqlite3.Row]:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def get_meta(self, key: str, default: str = "") -> str:
        row = self.query_one("SELECT value FROM meta WHERE key = ?", (key,))
        return row["value"] if row is not None else default

    def set_meta(self, key: str, value: str) -> None:
        with self._lock, self.connect() as conn:
            conn.execute(
                "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            conn.commit()
