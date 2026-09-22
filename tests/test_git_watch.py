import subprocess
import time
from pathlib import Path

from funes_hoard.collector import _known_repo_names
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


def test_scan_repo_without_checkpoint_sees_all_history_ahead_of_utc(tmp_path, monkeypatch):
    # git reads a plain --since date in local time: "1970-01-01" in a
    # timezone ahead of UTC is before the epoch and matched nothing.
    repo = tmp_path / "atlas"
    _init_repo(repo, "Luissalet", "luissalet@users.noreply.github.com", "feat: mine")
    monkeypatch.setenv("TZ", "UTC-14")  # POSIX spelling of UTC+14
    events = scan_repo(repo, since_ts=0, author_filters=[])
    assert [e.subject for e in events] == ["feat: mine"]


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


def test_scan_repo_decodes_utf8_and_hides_console_on_windows(tmp_path, monkeypatch):
    import funes_hoard.git_watch as gw

    seen = {}

    class R:
        returncode = 0
        stdout = "abc\x1f1700000000\x1ffeat: añadir caché\x1fAlex\x1fl@x\x1e"

    def fake_run(args, **kwargs):
        seen.update(kwargs)
        return R()

    monkeypatch.setattr(gw.sys, "platform", "win32")
    events = scan_repo(tmp_path, 0, [], run=fake_run)
    assert events[0].subject == "feat: añadir caché"
    assert seen["encoding"] == "utf-8" and seen["errors"] == "replace"
    assert seen["creationflags"] == 0x08000000


def test_poller_defaults_to_the_repos_own_git_identity(tmp_path):
    root = tmp_path / "projects"
    mine = root / "atlas"
    _init_repo(mine, "Luissalet", "luissalet@users.noreply.github.com", "feat: mine")
    # Someone else's commit in the same repo must not be recorded.
    # -c overrides the repo's own identity for this one commit (author and committer).
    subprocess.run(
        ["git", "-c", "user.name=Other", "-c", "user.email=o@example.com", "commit", "-q", "--allow-empty", "-m", "theirs"],
        cwd=str(mine), check=True, capture_output=True,
    )
    db = Database(tmp_path / "data")
    db.execute("INSERT INTO commit_repos(path, enabled) VALUES (?, 1)", (str(root),))
    GitCommitsPoller(db).poll_once()
    subjects = [r["subject"] for r in db.query("SELECT subject FROM commits")]
    assert subjects == ["feat: mine"]
    assert "atlas" in db.get_meta("known_repos")


def test_configured_authors_override_identity(tmp_path):
    root = tmp_path / "projects"
    _init_repo(root / "atlas", "Someone", "someone@example.com", "feat: theirs")
    db = Database(tmp_path / "data")
    db.execute("INSERT INTO commit_repos(path, enabled) VALUES (?, 1)", (str(root),))
    db.set_meta("commit_authors", "luissalet, alex@example.com")
    assert GitCommitsPoller(db).poll_once() == 0


# --- B3 regression: a repo only counts as a project while it has a recent,
# author-matched commit; merely existing under the root is not enough -----
def test_a_clone_by_another_author_never_becomes_a_project(tmp_path):
    root = tmp_path / "projects"
    _init_repo(root / "atlas", "Luissalet", "luissalet@users.noreply.github.com", "feat: mine")
    # A clone of someone else's project, generic name -- exactly the "python"
    # / "tools" / "react" case from the usability report: the folder exists
    # and has commits, but none of them are the user's.
    _init_repo(root / "python", "Some Contributor", "contrib@example.com", "docs: readme")
    db = Database(tmp_path / "data")
    db.execute("INSERT INTO commit_repos(path, enabled) VALUES (?, 1)", (str(root),))
    db.set_meta("commit_authors", "luissalet")
    GitCommitsPoller(db).poll_once()

    names = _known_repo_names(db)
    assert "atlas" in names
    assert "python" not in names
    # ... and so a job ad or docs page merely containing the word "python"
    # must not be classified into that project.
    from funes_hoard.core.classify import detect_project

    assert detect_project("Senior Python Engineer (Remote, EU) | LinkedIn", names) is None


def test_second_poll_of_an_unchanged_repo_runs_no_git_log(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    repo = root / "atlas"
    _init_repo(repo, "Luissalet", "luissalet@users.noreply.github.com", "feat: mine")
    db = Database(tmp_path / "data")
    db.execute("INSERT INTO commit_repos(path, enabled) VALUES (?, 1)", (str(root),))
    poller = GitCommitsPoller(db, author_filters=["luissalet"])
    assert poller.poll_once() == 1

    import funes_hoard.git_watch as gw

    def _must_not_run(*a, **k):
        raise AssertionError("git log must not run for a repo whose refs have not changed")

    monkeypatch.setattr(gw, "scan_repo", _must_not_run)
    assert poller.poll_once() == 0


def test_a_repo_that_times_out_is_retried_next_poll_not_skipped(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    repo = root / "atlas"
    _init_repo(repo, "Luissalet", "luissalet@users.noreply.github.com", "feat: mine")
    db = Database(tmp_path / "data")
    db.execute("INSERT INTO commit_repos(path, enabled) VALUES (?, 1)", (str(root),))
    poller = GitCommitsPoller(db, author_filters=["luissalet"])

    import funes_hoard.git_watch as gw

    real_scan_repo = gw.scan_repo
    monkeypatch.setattr(gw, "scan_repo", lambda *a, **k: None)  # simulate a timeout
    assert poller.poll_once() == 0
    assert db.query("SELECT * FROM commits") == []

    monkeypatch.setattr(gw, "scan_repo", real_scan_repo)
    assert poller.poll_once() == 1  # retried, not silently skipped forever


def test_first_scan_does_not_ask_for_full_history_past_retention(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    repo = root / "atlas"
    _init_repo(repo, "Luissalet", "luissalet@users.noreply.github.com", "feat: mine")
    db = Database(tmp_path / "data")
    db.execute("INSERT INTO commit_repos(path, enabled) VALUES (?, 1)", (str(root),))
    db.set_meta("retention_days", "30")
    poller = GitCommitsPoller(db, author_filters=["luissalet"])

    import funes_hoard.git_watch as gw

    real_scan_repo = gw.scan_repo
    captured = {}

    def spy(repo_dir, since_ts, filters, **kw):
        captured["since_ts"] = since_ts
        return real_scan_repo(repo_dir, since_ts, filters, **kw)

    monkeypatch.setattr(gw, "scan_repo", spy)
    poller.poll_once()
    assert captured["since_ts"] >= time.time() - 31 * 86400
    assert captured["since_ts"] < time.time() - 29 * 86400


def test_commits_from_one_repo_share_a_single_transaction(tmp_path):
    root = tmp_path / "projects"
    repo = root / "atlas"
    _init_repo(repo, "Luissalet", "luissalet@users.noreply.github.com", "feat: one")
    for subject in ("feat: two", "feat: three", "feat: four"):
        subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", subject], cwd=str(repo), check=True, capture_output=True)
    db = Database(tmp_path / "data")
    db.execute("INSERT INTO commit_repos(path, enabled) VALUES (?, 1)", (str(root),))

    statements = []
    db.connect().set_trace_callback(lambda sql: statements.append(sql))
    try:
        n = GitCommitsPoller(db, author_filters=["luissalet"]).poll_once()
    finally:
        db.connect().set_trace_callback(None)
    assert n == 4
    commit_count = sum(1 for s in statements if s.strip().upper() == "COMMIT")
    # A fixed number of commits (one for the repo's whole batch of commit
    # rows, one for the root's own bookkeeping, one for the known_repos
    # meta write) -- not one per commit row, which would grow with `n`.
    assert commit_count < n
    assert commit_count == 3


def test_an_old_abandoned_repo_ages_out_of_being_a_project(tmp_path):
    root = tmp_path / "projects"
    _init_repo(root / "notes", "Luissalet", "luissalet@users.noreply.github.com", "feat: ancient")
    db = Database(tmp_path / "data")
    db.execute("INSERT INTO commit_repos(path, enabled) VALUES (?, 1)", (str(root),))
    db.set_meta("commit_authors", "luissalet")
    GitCommitsPoller(db).poll_once()
    assert "notes" in _known_repo_names(db)

    # Back-date the only commit past the 90-day recency window.
    db.execute("UPDATE commits SET ts = ?", (time.time() - 200 * 86400,))
    db.set_meta("known_repos", "[]")  # also simulate a poll since then finding nothing new
    assert "notes" not in _known_repo_names(db)
