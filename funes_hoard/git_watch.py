"""Git commits source: scans configured repo roots for new commits.

`scan_repo()` shells out to `git log` (available on both Windows and Linux
once git is installed) and only keeps commits whose author name or email
matches one of the configured names -- so a shared machine does not record
someone else's commits. When no names are configured, the repo's own git
identity (`git config user.name` / `user.email`) is used as the filter.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import List, NamedTuple, Optional

from funes_hoard.db import Database

_LOG_FORMAT = "%H%x1f%at%x1f%s%x1f%an%x1f%ae%x1e"

# B3: a repo name only doubles as a project name (matched as a word in every
# window title) while it has a commit by the configured/own author within
# this many days. A clone of someone else's project never gets a commit row
# at all (its authors never match), so it never qualifies; an old, abandoned
# repo of the user's own ages out instead of matching forever.
PROJECT_RECENCY_DAYS = 90


class CommitEvent(NamedTuple):
    ts: float
    repo: str
    sha: str
    subject: str
    author: str


def find_git_repos(root: Path, max_depth: int = 2) -> List[Path]:
    """Find `.git` directories under `root` up to `max_depth` levels deep."""
    found: List[Path] = []
    root = Path(root)
    if not root.is_dir():
        return found

    def walk(path: Path, depth: int) -> None:
        if depth > max_depth:
            return
        if (path / ".git").exists():
            found.append(path)
            return
        try:
            children = [c for c in path.iterdir() if c.is_dir() and not c.name.startswith(".")]
        except OSError:
            return
        for c in children:
            walk(c, depth + 1)

    walk(root, 0)
    return found


# subprocess.CREATE_NO_WINDOW: without it every `git` call made from a
# windowless app (started by Faustus, or with pythonw) flashes a console.
_CREATE_NO_WINDOW = 0x08000000


def _subprocess_kwargs() -> dict:
    kwargs = {"capture_output": True, "text": True, "encoding": "utf-8", "errors": "replace", "timeout": 15}
    if sys.platform == "win32":
        kwargs["creationflags"] = _CREATE_NO_WINDOW
    return kwargs


def repo_identity(repo_path: Path, run=subprocess.run) -> List[str]:
    """The user.name / user.email git would use in this repo (local or global)."""
    out: List[str] = []
    for key in ("user.name", "user.email"):
        try:
            res = run(["git", "config", "--get", key], cwd=str(repo_path), **_subprocess_kwargs())
        except Exception:
            continue
        if res.returncode == 0 and res.stdout.strip():
            out.append(res.stdout.strip())
    return out


def configured_authors(db: Database) -> List[str]:
    raw = db.get_meta("commit_authors", "") or ""
    return [a.strip() for a in raw.replace("\n", ",").split(",") if a.strip()]


def scan_repo(
    repo_path: Path,
    since_ts: float,
    author_filters: List[str],
    run=subprocess.run,
) -> Optional[List[CommitEvent]]:
    """`None` means the scan itself failed (timed out, git errored) and
    should be retried later; `[]` means it ran fine and found nothing new
    -- the poller must tell these apart to know whether it is safe to move
    this repo's checkpoint forward (A1)."""
    since_arg = f"--since=@{int(since_ts)}" if since_ts else "--since=1970-01-01"
    try:
        out = run(
            ["git", "log", "--all", since_arg, f"--format={_LOG_FORMAT}"],
            cwd=str(repo_path), **_subprocess_kwargs(),
        )
    except Exception:
        return None
    if out.returncode != 0:
        return None
    if not out.stdout:
        return []
    events: List[CommitEvent] = []
    repo_name = Path(repo_path).name
    lowered_filters = [f.lower() for f in author_filters if f.strip()]
    for record in out.stdout.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        parts = record.split("\x1f")
        if len(parts) != 5:
            continue
        sha, ts_raw, subject, author_name, author_email = parts
        if lowered_filters:
            hay = f"{author_name} {author_email}".lower()
            if not any(f in hay for f in lowered_filters):
                continue
        try:
            ts = float(ts_raw)
        except ValueError:
            continue
        events.append(CommitEvent(ts=ts, repo=repo_name, sha=sha, subject=subject, author=author_name))
    return events


def _refs_mtime(repo_dir: Path) -> float:
    """A cheap fingerprint of "has anything changed in this repo's refs":
    the newest mtime among HEAD, packed-refs, logs/HEAD and every file
    under refs/. Pure filesystem stat calls, no subprocess -- comparing it
    to the value from the last scan is what lets a poll skip a repo
    entirely (A1) instead of always spending 3 `git` processes on it."""
    git_dir = repo_dir / ".git"
    mtimes = []
    for p in (git_dir / "HEAD", git_dir / "packed-refs", git_dir / "logs" / "HEAD"):
        try:
            mtimes.append(p.stat().st_mtime)
        except OSError:
            pass
    try:
        for p in (git_dir / "refs").rglob("*"):
            try:
                if p.is_file():
                    mtimes.append(p.stat().st_mtime)
            except OSError:
                pass
    except OSError:
        pass
    return max(mtimes) if mtimes else 0.0


class GitCommitsPoller:
    def __init__(self, db: Database, author_filters: Optional[List[str]] = None, interval_s: float = 600.0) -> None:
        self.db = db
        self.author_filters = author_filters  # None -> read from settings on every poll
        self.interval_s = interval_s

    def _retention_floor(self) -> float:
        try:
            days = int(float(self.db.get_meta("retention_days", "180") or 180))
        except ValueError:
            days = 180
        return time.time() - days * 86400.0

    def poll_once(self) -> int:
        repos = self.db.query("SELECT * FROM commit_repos WHERE enabled = 1")
        configured = self.author_filters if self.author_filters is not None else configured_authors(self.db)
        retention_floor = self._retention_floor()
        identity_cache: dict = {}
        total = 0
        for r in repos:
            path = Path(r["path"])
            found = find_git_repos(path)
            for repo_dir in found:
                repo_path = str(repo_dir)
                current_mtime = _refs_mtime(repo_dir)
                cached = self.db.query_one("SELECT * FROM git_repo_scan WHERE path = ?", (repo_path,))
                if cached is not None and cached["refs_mtime"] == current_mtime:
                    continue  # nothing changed since we last looked: no git process needed
                # A repo's own checkpoint, never older than the retention
                # window: a first scan (or one that never completed before)
                # need not import history retention would delete right away.
                since = max(cached["last_scan_ts"] if cached is not None else 0.0, retention_floor)
                if repo_path not in identity_cache:
                    identity_cache[repo_path] = configured or repo_identity(repo_dir)
                events = scan_repo(repo_dir, since, identity_cache[repo_path])
                if events is None:
                    continue  # timed out or errored: leave the checkpoint alone, retry next poll
                with self.db.transaction() as conn:
                    for ev in events:
                        exists = conn.execute(
                            "SELECT 1 FROM commits WHERE repo = ? AND sha = ?", (ev.repo, ev.sha)
                        ).fetchone()
                        if exists:
                            continue
                        cur = conn.execute(
                            "INSERT OR IGNORE INTO commits(ts, repo, sha, subject, author) VALUES (?, ?, ?, ?, ?)",
                            (ev.ts, ev.repo, ev.sha, ev.subject, ev.author),
                        )
                        if cur.lastrowid and self.db.fts_available:
                            conn.execute(
                                "INSERT INTO search_fts(text, source, ref_id, ts) VALUES (?, ?, ?, ?)",
                                (f"{ev.subject} {ev.repo}", "commit", cur.lastrowid, ev.ts),
                            )
                        total += 1
                    conn.execute(
                        "INSERT INTO git_repo_scan(path, refs_mtime, last_scan_ts) VALUES (?, ?, ?)"
                        " ON CONFLICT(path) DO UPDATE SET refs_mtime = excluded.refs_mtime,"
                        " last_scan_ts = excluded.last_scan_ts",
                        (repo_path, current_mtime, time.time()),
                    )
            self.db.execute(
                "UPDATE commit_repos SET last_scan_ts = ?, last_repo_count = ? WHERE id = ?",
                (time.time(), len(found), r["id"]),
            )
        # B3: repo names double as project names for classification, but a
        # repo only qualifies while it actually has a recent commit by the
        # configured/own author -- a clone of someone else's project (whose
        # commits are never kept, since their author never matches) must
        # never become a project just because its `.git` folder exists.
        cutoff = time.time() - PROJECT_RECENCY_DAYS * 86400
        recent = self.db.query("SELECT DISTINCT repo FROM commits WHERE ts >= ?", (cutoff,))
        self.db.set_meta("known_repos", json.dumps(sorted(row["repo"] for row in recent)))
        return total
