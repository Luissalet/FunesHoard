import subprocess
from pathlib import Path

from funes_hoard.db import Database
from funes_hoard.git_watch import GitCommitsPoller, find_git_repos, scan_repo


def _init_repo(path: Path, author_name: str, author_email: str, subject: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    run = lambda args: subprocess.run(args, cwd=str(path), capture_output=True, text=True, check=True)
    run(["git", "init", "-q", "-b", "main"])
    run(["git", "config", "user.name", author_name])
    run(["git", "config", "user.email", author_email])
    (path / "f.txt").write_text("hello", encoding="utf-8")
    run(["git", "add", "f.txt"])
    run(["git", "commit", "-q", "-m", subject])


def test_find_git_repos_depth_limited(tmp_path):
    repo = tmp_path / "projects" / "atlas"
    _init_repo(repo, "Alex", "alex@example.com", "feat: init")
    found = find_git_repos(tmp_path, max_depth=2)
    assert repo in found


def test_find_git_repos_ignores_too_deep(tmp_path):
    repo = tmp_path / "a" / "b" / "c" / "atlas"
    _init_repo(repo, "Alex", "alex@example.com", "feat: init")
    found = find_git_repos(tmp_path, max_depth=1)
    assert found == []


def test_scan_repo_filters_by_author(tmp_path):
    repo = tmp_path / "atlas"
    _init_repo(repo, "Someone Else", "someone@example.com", "feat: not mine")
    events = scan_repo(repo, since_ts=0, author_filters=["Luissalet"])
    assert events == []
    events_all = scan_repo(repo, since_ts=0, author_filters=[])
    assert len(events_all) == 1
    assert events_all[0].subject == "feat: not mine"


def test_scan_repo_matches_configured_author(tmp_path):
    repo = tmp_path / "atlas"
    _init_repo(repo, "Luissalet", "luissalet@users.noreply.github.com", "feat: mine")
    events = scan_repo(repo, since_ts=0, author_filters=["luissalet"])
    assert len(events) == 1
    assert events[0].subject == "feat: mine"


def test_git_poller_persists_commits(tmp_path):
    repo_root = tmp_path / "projects"
    repo = repo_root / "atlas"
    _init_repo(repo, "Luissalet", "luissalet@users.noreply.github.com", "feat: from poller")
    db = Database(tmp_path / "data")
    db.execute("INSERT INTO commit_repos(path, enabled) VALUES (?, 1)", (str(repo_root),))
    poller = GitCommitsPoller(db, author_filters=["luissalet"])
    n = poller.poll_once()
    assert n == 1
    rows = db.query("SELECT * FROM commits")
    assert len(rows) == 1
    assert rows[0]["subject"] == "feat: from poller"
    # second poll finds no new commits
    assert poller.poll_once() == 0
