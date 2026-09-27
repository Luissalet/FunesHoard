"""SQLite connection (WAL, FTS5) and ordered schema migrations."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

MIN_SQLITE = (3, 35, 0)

MIGRATIONS: list[str] = [
    # 1: sessions, segments, FTS over segments (+ title), settings
    """
    CREATE TABLE sessions (
      id TEXT PRIMARY KEY,
      title TEXT NOT NULL DEFAULT '',
      kind TEXT NOT NULL DEFAULT 'other' CHECK (kind IN ('meeting', 'interview', 'note', 'other')),
      status TEXT NOT NULL DEFAULT 'recording' CHECK (status IN ('recording', 'processing', 'done', 'failed')),
      started_at REAL NOT NULL,
      ended_at REAL,
      source_system INTEGER NOT NULL DEFAULT 0,
      source_mic INTEGER NOT NULL DEFAULT 1,
      language TEXT NOT NULL DEFAULT 'auto',
      audio_path TEXT NOT NULL DEFAULT '',
      duration_s REAL NOT NULL DEFAULT 0,
      notes TEXT NOT NULL DEFAULT '',
      tags TEXT NOT NULL DEFAULT '[]',
      error TEXT NOT NULL DEFAULT '',
      origin TEXT NOT NULL DEFAULT 'recording'
    );
    CREATE INDEX sessions_started ON sessions(started_at);
    CREATE INDEX sessions_kind ON sessions(kind, started_at);
    CREATE TABLE segments (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
      start_s REAL NOT NULL,
      end_s REAL NOT NULL,
      speaker TEXT NOT NULL DEFAULT 'S1',
      text TEXT NOT NULL,
      confidence REAL NOT NULL DEFAULT 0,
      live INTEGER NOT NULL DEFAULT 0,
      words TEXT
    );
    CREATE INDEX segments_session ON segments(session_id, start_s);
    CREATE VIRTUAL TABLE segments_fts USING fts5(
      text, title, session_id UNINDEXED, segment_id UNINDEXED,
      tokenize = 'unicode61 remove_diacritics 2'
    );
    CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """,
    # 2: per-session transcription statistics (silence gate, dropped hallucinations)
    """
    ALTER TABLE sessions ADD COLUMN stats TEXT NOT NULL DEFAULT '{}';
    """,
]


def check_sqlite() -> None:
    version = tuple(int(p) for p in sqlite3.sqlite_version.split("."))
    if version < MIN_SQLITE:
        raise RuntimeError(f"SQLite {sqlite3.sqlite_version} is too old; need {'.'.join(map(str, MIN_SQLITE))}+.")
    probe = sqlite3.connect(":memory:")
    try:
        probe.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
    except sqlite3.OperationalError as error:  # pragma: no cover - depends on the build
        raise RuntimeError("This Python's SQLite has no FTS5 support; Scribe needs it.") from error
    finally:
        probe.close()


class Database:
    """One connection shared by every thread, guarded by a re-entrant lock.

    The app is the only writer; the MCP bridge never opens this file.
    """

    def __init__(self, path: Path):
        check_sqlite()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.migrate()

    def migrate(self) -> None:
        with self.lock:
            self.conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
            row = self.conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
            current = row["v"] or 0
            for index, sql in enumerate(MIGRATIONS, start=1):
                if index <= current:
                    continue
                script = f"BEGIN;\n{sql}\nINSERT INTO schema_version(version) VALUES ({index});\nCOMMIT;"
                try:
                    self.conn.executescript(script)
                except Exception:
                    if self.conn.in_transaction:
                        self.conn.execute("ROLLBACK")
                    raise

    def close(self) -> None:
        with self.lock:
            try:
                self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass
            self.conn.close()
