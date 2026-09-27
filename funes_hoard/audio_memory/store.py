"""Sessions and segments in SQLite, with the FTS5 index kept in sync by hand."""

from __future__ import annotations

import json
import re
import secrets
import shutil
import time
from datetime import datetime
from pathlib import Path

from .db import Database
from .merge import Labelled

KINDS = ("meeting", "interview", "note", "other")
NO_SPEECH_NOTE = "(sin voz detectada)"
STATUSES = ("recording", "processing", "done", "failed")


def new_session_id(now: float | None = None) -> str:
    stamp = datetime.fromtimestamp(now or time.time()).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{secrets.token_hex(2)}"


def fts_query(raw: str) -> str:
    """Safe FTS5 MATCH expression: quoted tokens, implicit AND, prefix on the last."""
    tokens = [t for t in re.findall(r"[\w'’\-]+", raw, flags=re.UNICODE) if t.strip("-'’")]
    if not tokens:
        return ""
    parts = [f'"{t.replace(chr(34), "")}"' for t in tokens]
    if len(tokens[-1]) >= 3:
        parts[-1] += "*"
    return " ".join(parts)


def normalize_tags(tags) -> list[str]:
    seen: list[str] = []
    for tag in tags or []:
        clean = str(tag).strip().lower()[:40]
        if clean and clean not in seen:
            seen.append(clean)
    return seen[:30]


def session_row(row) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "kind": row["kind"],
        "status": row["status"],
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
        "sources": {"system": bool(row["source_system"]), "mic": bool(row["source_mic"])},
        "language": row["language"],
        "audio_path": row["audio_path"],
        "duration_s": round(row["duration_s"], 2),
        "notes": row["notes"],
        "tags": json.loads(row["tags"] or "[]"),
        "error": row["error"],
        "origin": row["origin"],
        "stats": json.loads(row["stats"] or "{}"),
    }


def segment_row(row) -> dict:
    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "start_s": row["start_s"],
        "end_s": row["end_s"],
        "speaker": row["speaker"],
        "text": row["text"],
        "confidence": row["confidence"],
        "live": bool(row["live"]),
    }


class SessionStore:
    def __init__(self, db: Database, sessions_dir: Path):
        self.db = db
        self.sessions_dir = sessions_dir

    # ---------- sessions ----------
    def create(self, title: str, kind: str, mic: bool, system: bool, language: str, origin: str = "recording", status: str = "recording", started_at: float | None = None) -> dict:
        started = started_at or time.time()
        session_id = new_session_id(started)
        title = title.strip() or self.default_title(kind, started)
        with self.db.lock:
            self.db.conn.execute(
                "INSERT INTO sessions(id, title, kind, status, started_at, source_system, source_mic, language, origin) VALUES (?,?,?,?,?,?,?,?,?)",
                (session_id, title, kind if kind in KINDS else "other", status, started, int(system), int(mic), language, origin),
            )
        (self.sessions_dir / session_id).mkdir(parents=True, exist_ok=True)
        return self.get(session_id)

    @staticmethod
    def default_title(kind: str, started: float) -> str:
        names = {"meeting": "Reunión", "interview": "Entrevista", "note": "Nota de voz", "other": "Sesión"}
        return f"{names.get(kind, 'Sesión')} {datetime.fromtimestamp(started).strftime('%d/%m %H:%M')}"

    def session_dir(self, session_id: str) -> Path:
        return self.sessions_dir / session_id

    def get(self, session_id: str) -> dict | None:
        with self.db.lock:
            row = self.db.conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return session_row(row) if row else None

    def update(self, session_id: str, **fields) -> dict | None:
        allowed = {"title", "kind", "status", "ended_at", "audio_path", "duration_s", "notes", "tags", "error", "language", "stats"}
        data = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if "stats" in data and not isinstance(data["stats"], str):
            data["stats"] = json.dumps(data["stats"])
        if "tags" in data:
            data["tags"] = json.dumps(normalize_tags(data["tags"]))
        if "title" in data:
            data["title"] = data["title"].strip()[:200]
        if not data:
            return self.get(session_id)
        assignments = ", ".join(f"{key} = ?" for key in data)
        with self.db.lock:
            self.db.conn.execute(f"UPDATE sessions SET {assignments} WHERE id = ?", (*data.values(), session_id))
            if "title" in data:
                self.db.conn.execute("UPDATE segments_fts SET title = ? WHERE session_id = ?", (data["title"], session_id))
        return self.get(session_id)

    def append_notes(self, session_id: str, text: str) -> dict | None:
        current = self.get(session_id)
        if current is None:
            return None
        notes = (current["notes"] + "\n" if current["notes"] else "") + text.strip()
        return self.update(session_id, notes=notes[:20000])

    def append_notes_once(self, session_id: str, text: str) -> tuple[dict | None, bool]:
        """Append one agent note, or return the identical trailing note.

        The read and write share a lock so simultaneous MCP retries cannot both
        observe the old value and append the same block twice.
        """
        addition = text.strip()
        if not addition:
            raise ValueError("The note cannot be empty.")
        with self.db.lock:
            row = self.db.conn.execute("SELECT notes FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if row is None:
                return None, False
            current = row["notes"] or ""
            if current == addition or current.endswith("\n" + addition):
                return self.get(session_id), True
            notes = (current + "\n" if current else "") + addition
            if len(notes) > 20000:
                raise ValueError("The session notes would exceed 20000 characters.")
            self.db.conn.execute("UPDATE sessions SET notes = ? WHERE id = ?", (notes, session_id))
            return self.get(session_id), False

    def delete(self, session_id: str) -> bool:
        with self.db.lock:
            self.db.conn.execute("DELETE FROM segments_fts WHERE session_id = ?", (session_id,))
            cursor = self.db.conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        shutil.rmtree(self.session_dir(session_id), ignore_errors=True)
        return cursor.rowcount > 0

    def list(self, q: str = "", kind: str | None = None, tag: str | None = None, start: float | None = None, end: float | None = None, status: str | None = None, limit: int = 100, offset: int = 0) -> list[dict]:
        where, params = [], []
        if kind:
            where.append("kind = ?")
            params.append(kind)
        if status:
            where.append("status = ?")
            params.append(status)
        if start is not None:
            where.append("started_at >= ?")
            params.append(start)
        if end is not None:
            where.append("started_at <= ?")
            params.append(end)
        if tag:
            where.append("EXISTS (SELECT 1 FROM json_each(sessions.tags) WHERE value = ?)")
            params.append(tag.strip().lower())
        match = fts_query(q) if q else ""
        if match:
            where.append("(title LIKE ? OR id IN (SELECT DISTINCT session_id FROM segments_fts WHERE segments_fts MATCH ?))")
            params += [f"%{q.strip()}%", match]
        elif q:
            where.append("title LIKE ?")
            params.append(f"%{q.strip()}%")
        sql = "SELECT * FROM sessions" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY started_at DESC LIMIT ? OFFSET ?"
        with self.db.lock:
            rows = self.db.conn.execute(sql, (*params, limit, offset)).fetchall()
        return [session_row(r) for r in rows]

    def count(self) -> int:
        with self.db.lock:
            return self.db.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]

    def first_line(self, session_id: str) -> str:
        """First transcribed line, or the written note when a finished session has no speech."""
        with self.db.lock:
            row = self.db.conn.execute("SELECT text FROM segments WHERE session_id = ? ORDER BY start_s LIMIT 1", (session_id,)).fetchone()
            status = self.db.conn.execute("SELECT status FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row:
            return row["text"][:160]
        return NO_SPEECH_NOTE if status and status["status"] == "done" else ""

    def tags(self) -> list[dict]:
        with self.db.lock:
            rows = self.db.conn.execute("SELECT value AS tag, COUNT(*) AS n FROM sessions, json_each(sessions.tags) GROUP BY value ORDER BY n DESC, value").fetchall()
        return [{"tag": r["tag"], "count": r["n"]} for r in rows]

    # ---------- segments ----------
    def add_segments(self, session_id: str, segments: list[Labelled], live: bool) -> list[dict]:
        session = self.get(session_id)
        if session is None:
            raise LookupError(f"Unknown session: {session_id}")
        stored: list[dict] = []
        with self.db.lock:
            for seg in segments:
                cursor = self.db.conn.execute(
                    "INSERT INTO segments(session_id, start_s, end_s, speaker, text, confidence, live, words) VALUES (?,?,?,?,?,?,?,?)",
                    (session_id, seg.start_s, seg.end_s, seg.speaker, seg.text, seg.confidence, int(live), json.dumps(seg.words) if seg.words else None),
                )
                segment_id = cursor.lastrowid
                self.db.conn.execute(
                    "INSERT INTO segments_fts(text, title, session_id, segment_id) VALUES (?,?,?,?)",
                    (seg.text, session["title"], session_id, segment_id),
                )
                stored.append({"id": segment_id, "session_id": session_id, "start_s": seg.start_s, "end_s": seg.end_s, "speaker": seg.speaker, "text": seg.text, "confidence": seg.confidence, "live": live})
        return stored

    def replace_segments(self, session_id: str, segments: list[Labelled]) -> list[dict]:
        """Final pass: drop every segment (live or not) and store the definitive ones."""
        with self.db.lock:
            self.db.conn.execute("BEGIN")
            try:
                self.db.conn.execute("DELETE FROM segments_fts WHERE session_id = ?", (session_id,))
                self.db.conn.execute("DELETE FROM segments WHERE session_id = ?", (session_id,))
                stored = self.add_segments(session_id, segments, live=False)
                self.db.conn.execute("COMMIT")
            except Exception:
                self.db.conn.execute("ROLLBACK")
                raise
        return stored

    def segments(self, session_id: str, from_s: float | None = None, to_s: float | None = None, limit: int | None = None) -> list[dict]:
        where, params = ["session_id = ?"], [session_id]
        if from_s is not None:
            where.append("start_s >= ?")
            params.append(from_s)
        if to_s is not None:
            where.append("start_s <= ?")
            params.append(to_s)
        sql = "SELECT * FROM segments WHERE " + " AND ".join(where) + " ORDER BY start_s, id"
        if limit:
            sql += f" LIMIT {int(limit)}"
        with self.db.lock:
            rows = self.db.conn.execute(sql, params).fetchall()
        return [segment_row(r) for r in rows]

    # ---------- search ----------
    def search(self, q: str, kind: str | None = None, start: float | None = None, end: float | None = None, limit: int = 60) -> dict:
        match = fts_query(q)
        if not match:
            return {"query": q, "total": 0, "sessions": []}
        where, params = ["segments_fts MATCH ?"], [match]
        if kind:
            where.append("s.kind = ?")
            params.append(kind)
        if start is not None:
            where.append("s.started_at >= ?")
            params.append(start)
        if end is not None:
            where.append("s.started_at <= ?")
            params.append(end)
        sql = f"""SELECT g.id, g.session_id, g.start_s, g.end_s, g.speaker, g.text,
                         s.title, s.kind, s.started_at, s.duration_s, s.status,
                         bm25(segments_fts, 1.0, 3.0) AS rank,
                         snippet(segments_fts, 0, '<mark>', '</mark>', '…', 14) AS snip
                  FROM segments_fts
                  JOIN segments g ON g.id = segments_fts.segment_id
                  JOIN sessions s ON s.id = g.session_id
                  WHERE {' AND '.join(where)}
                  ORDER BY rank LIMIT ?"""
        with self.db.lock:
            rows = self.db.conn.execute(sql, (*params, limit)).fetchall()
        grouped: dict[str, dict] = {}
        for row in rows:
            entry = grouped.setdefault(
                row["session_id"],
                {"session": {"id": row["session_id"], "title": row["title"], "kind": row["kind"], "started_at": row["started_at"], "duration_s": row["duration_s"], "status": row["status"]}, "hits": [], "best_rank": row["rank"]},
            )
            entry["hits"].append({"segment_id": row["id"], "start_s": row["start_s"], "end_s": row["end_s"], "speaker": row["speaker"], "snippet": re.sub(r"\s+", " ", row["snip"] or "").strip(), "text": row["text"]})
        sessions = sorted(grouped.values(), key=lambda e: e["best_rank"])
        for entry in sessions:
            entry["hits"].sort(key=lambda h: h["start_s"])
            entry.pop("best_rank")
        return {"query": q, "total": len(rows), "sessions": sessions}

    # ---------- storage ----------
    def storage_bytes(self) -> int:
        total = 0
        if self.sessions_dir.is_dir():
            for path in self.sessions_dir.rglob("*"):
                if path.is_file():
                    try:
                        total += path.stat().st_size
                    except OSError:
                        pass
        return total
