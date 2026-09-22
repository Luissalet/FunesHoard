"""Git commits source: scans configured repo roots for new commits.

`scan_repo()` shells out to `git log` (available on both Windows and Linux
once git is installed) and only keeps commits whose author name or email
matches one of the configured names -- so a shared machine does not record
someone else's commits.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List, NamedTuple, Optional

from funes_hoard.db import Database

_LOG_FORMAT = "%H%x1f%at%x1f%s%x1f%an%x1f%ae%x1e"


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


def scan_repo(
    repo_path: Path,
    since_ts: float,
    author_filters: List[str],
    run=subprocess.run,
) -> List[CommitEvent]:
    since_arg = f"--since=@{int(since_ts)}" if since_ts else "--since=1970-01-01"
    try:
        out = run(
            ["git", "log", "--all", since_arg, f"--format={_LOG_FORMAT}"],
            cwd=str(repo_path), capture_output=True, text=True, timeout=15,
        )
    except Exception:
        return []
    if out.returncode != 0 or not out.stdout:
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


class GitCommitsPoller:
    def __init__(self, db: Database, author_filters: Optional[List[str]] = None, interval_s: float = 600.0) -> None:
        self.db = db
        self.author_filters = author_filters or []
        self.interval_s = interval_s

    def poll_once(self) -> int:
        repos = self.db.query("SELECT * FROM commit_repos WHERE enabled = 1")
        total = 0
        for r in repos:
            path = Path(r["path"])
            since = r["last_scan_ts"] or 0.0
            for repo_dir in find_git_repos(path):
                events = scan_repo(repo_dir, since, self.author_filters)
                for ev in events:
                    exists = self.db.query_one(
                        "SELECT 1 FROM commits WHERE repo = ? AND sha = ?", (ev.repo, ev.sha)
                    )
                    if exists:
                        continue
                    row_id = self.db.execute(
                        "INSERT OR IGNORE INTO commits(ts, repo, sha, subject, author) VALUES (?, ?, ?, ?, ?)",
                        (ev.ts, ev.repo, ev.sha, ev.subject, ev.author),
                    )
                    if row_id:
                        self.db.index_text("commit", row_id, ev.ts, f"{ev.subject} {ev.repo}")
                    total += 1
            self.db.execute("UPDATE commit_repos SET last_scan_ts = ? WHERE id = ?", (__import__("time").time(), r["id"]))
        return total
