#!/usr/bin/env python3
"""Realistic synthetic data for walking the use cases (docs/USE_CASES.md).

    python scripts/uxtest_data.py generate [--out data-uxtest]
    python scripts/uxtest_data.py serve --port 18830 [--out data-uxtest]

`generate` builds, under `--out` (gitignored):
  - `repos/Projects/`: ~40 git repos like the user's folder of independent
    projects -- a handful of active projects with commits over the last two weeks,
    old experiments, clones of other people's projects (other authors, one
    with thousands of commits), generic names (`docs`, `notes`, `chat`, ...);
  - `app/funes.sqlite3`: two weeks of activity produced by driving the real
    Collector with a scripted probe every 5 s (realistic Windows titles in
    EN/ES, alt-tab blips, lunch away, locked screen, overnight gaps, videos
    and meetings watched without input, a password manager, a private
    window, a bank page) plus recent-file rows.

`serve` runs the real app on that data dir with a probe that reads
`<out>/live-probe.json` on every sample, so a walkthrough can say "the user
has been idle for 3121 s in the chat app" and watch what the collector does.
Everything is synthetic: no real window titles, names or files.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from funes_hoard.collector import Collector  # noqa: E402
from funes_hoard.core.spans import Sample  # noqa: E402
from funes_hoard.db import Database  # noqa: E402
from funes_hoard.git_watch import GitCommitsPoller  # noqa: E402

STEP_S = 5.0
OWNER = ("Luissalet", "luissalet@users.noreply.github.com")
OTHERS = [("Ana Torres", "ana@example.org"), ("dev-bot", "bot@example.org"), ("Kenji Sato", "kenji@example.net")]

ACTIVE = ["Faustus", "funes-hoard", "laplace-hoard", "daguerres-hoard", "scheherazade-hoard", "Portfolio"]
OLD_OWN = ["tetris-rl", "tfg-vision", "dotfiles", "notes", "scripts", "blog", "home", "cv-latex",
           "whisper-tests", "rag-playground", "api", "test"]
CLONES = ["llama.cpp", "whisper.cpp", "ComfyUI", "open-webui", "docs", "chat", "ui", "react",
          "fastapi", "vite", "python", "tools", "awesome-selfhosted", "stable-diffusion-webui",
          "ollama", "transformers", "shadcn-ui", "tauri", "vscode-extension-samples", "data"]
FILES = {
    "Faustus": ["App.tsx", "chat.ts", "mcp_bridge.py", "models.py", "README.md", "plugins.ts"],
    "funes-hoard": ["collector.py", "spans.py", "queries.py", "TodayView.tsx", "test_spans.py", "git_watch.py"],
    "laplace-hoard": ["loader.py", "charts.tsx", "test_csv.py", "README.es.md"],
    "daguerres-hoard": ["exif.py", "dedupe.py", "Gallery.tsx"],
    "scheherazade-hoard": ["world.py", "entities.ts", "session.py"],
    "Portfolio": ["index.html", "projects.json", "style.css"],
}
DOC_PAGES = [
    "SQLite FTS5 Extension - Google Chrome",
    "python - ctypes GetLastInputInfo idle time - Stack Overflow - Google Chrome",
    "Lifespan Events - FastAPI - Google Chrome",
    "GetTickCount function (sysinfoapi.h) - Win32 apps | Microsoft Learn - Google Chrome",
    "Playwright Python - Screenshots - Google Chrome",
    "React – useEffect - Google Chrome",
    "llama.cpp server README - GitHub - Google Chrome",
]
JOB_PAGES = [
    "Senior Python Engineer (Remote, EU) | LinkedIn - Google Chrome",
    "Ingeniero/a de IA - Madrid - InfoJobs - Google Chrome",
    "Staff Engineer, Developer Tools - Careers - Google Chrome",
    "Empleos: machine learning engineer en España | LinkedIn - Google Chrome",
]
NOVEL = [
    ("WINWORD.EXE", "El mapa perdido - Capítulo 7.docx - Word"),
    ("WINWORD.EXE", "El mapa perdido - Capítulo 8.docx - Word"),
    ("Obsidian.exe", "Personajes - Novela - Obsidian v1.6.7"),
    ("Obsidian.exe", "Cronología del mundo - Novela - Obsidian v1.6.7"),
]
FAN_VIDEOS = [
    "Fan film: The Last Signal (Full Movie) - YouTube - Google Chrome",
    "Cortometraje fan: La Última Señal - YouTube - Google Chrome",
    "Behind the scenes of a fan production - YouTube - Google Chrome",
]


@dataclass
class Block:
    minutes: float
    app: str
    title: str
    passive: bool = False  # no input for the whole block (video, meeting)
    locked: bool = False
    away: bool = False


def _exe(app: str) -> str:
    return f"C:/Program Files/{app.split('.')[0]}/{app}"


def coding(rng: random.Random, project: str, minutes: int) -> List[Block]:
    out: List[Block] = []
    left = minutes
    while left > 0:
        m = min(left, rng.randint(4, 18))
        f = rng.choice(FILES[project])
        out.append(Block(m, "Code.exe", f"{f} - {project} - Visual Studio Code"))
        roll = rng.random()
        if roll < 0.25:
            out.append(Block(rng.choice([0.1, 0.2, 2, 4]), "WindowsTerminal.exe", f"pwsh - {project}"))
        elif roll < 0.4:
            out.append(Block(rng.choice([0.1, 3, 6]), "chrome.exe", rng.choice(DOC_PAGES)))
        elif roll < 0.5:
            out.append(Block(rng.choice([2, 5]), "Faustus.exe", f"Faustus - {project} plan"))
        elif roll < 0.55:
            out.append(Block(0.2, "Discord.exe", "#general | Fan Productions ES - Discord"))
        left -= m
    return out


def day_script(rng: random.Random, d: date) -> List[Block]:
    wd = d.weekday()
    main = ACTIVE[(d.toordinal()) % 4]
    other = ACTIVE[(d.toordinal() + 1) % 6]
    if wd >= 5:  # weekend: novel, photos, fan films, a little coding
        blocks = [Block(40, "explorer.exe", "Photos 2026 - Explorador de archivos"),
                  Block(35, "Microsoft.Photos.exe", "IMG_20260912_1843.jpg - Fotos"),
                  *[Block(rng.randint(15, 40), *rng.choice(NOVEL)) for _ in range(4)],
                  Block(70, "unknown", "", away=True),
                  *coding(rng, "Portfolio", 45),
                  Block(50, "chrome.exe", rng.choice(FAN_VIDEOS), passive=True),
                  Block(30, "steam.exe", "Steam")]
        return blocks
    blocks: List[Block] = [
        Block(8, "Outlook.exe", "Bandeja de entrada - alex@example.com - Outlook"),
        Block(3, "KeePassXC.exe", "Passwords.kdbx - KeePassXC"),
        *coding(rng, main, 110),
        Block(12, "unknown", "", away=True),  # coffee
        Block(15, "ms-teams.exe", "Daily standup | Microsoft Teams", passive=True),
        *coding(rng, main, 70),
        Block(4, "chrome.exe", "Nueva pestaña de incógnito - Google Chrome"),
        Block(6, "chrome.exe", "Banco Ejemplo - Área de clientes - Google Chrome"),
        Block(75, "unknown", "", away=True),  # lunch
        Block(2, "LockApp.exe", "Windows Default Lock Screen", locked=True),
        *[Block(rng.randint(5, 15), "chrome.exe", rng.choice(JOB_PAGES)) for _ in range(3)],
        Block(20, "WINWORD.EXE", "CV 2026 - EN.docx - Word"),
        *coding(rng, other, 80),
        Block(10, "WhatsApp.exe", "WhatsApp"),
        Block(35, "chrome.exe", rng.choice(FAN_VIDEOS), passive=True),
        *[Block(rng.randint(15, 35), *rng.choice(NOVEL)) for _ in range(2)],
        Block(25, "Spotify.exe", "Lo-fi para programar"),
    ]
    if wd == 1:
        blocks.insert(3, Block(20, "chrome.exe", "SQLite FTS5 Extension - Google Chrome"))
    if wd == 3:
        blocks.insert(10, Block(45, "Zoom.exe", "Entrevista técnica - Zoom Meeting", passive=True))
    return blocks


class ScriptProbe:
    def __init__(self, blocks: List[Block], start_ts: float, rng: random.Random) -> None:
        self.rng = rng
        self.samples = []
        ts = start_ts
        for b in blocks:
            n = max(1, int(round(b.minutes * 60 / STEP_S)))
            for i in range(n):
                if b.away:
                    idle = 60 + i * STEP_S  # crosses 120 s -> back-dated away span
                elif b.passive:
                    idle = i * STEP_S
                else:
                    idle = rng.choice([0, 0, 1, 2, 3, 8, 15])
                self.samples.append((ts, b.app, b.title, idle, b.locked))
                ts += STEP_S
        self.end_ts = ts
        self.i = 0

    def sample(self) -> Sample:
        ts, app, title, idle, locked = self.samples[min(self.i, len(self.samples) - 1)]
        self.i += 1
        return Sample(ts=ts, app=app, exe=_exe(app) if app != "unknown" else "", title=title,
                      pid=1000 + len(app), idle_s=float(idle), locked=locked)

    def status(self) -> str:
        return "uxtest script probe"


def _git(cwd: Path, *args: str, env: Optional[dict] = None) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=env)


def _make_repo(path: Path, author, commits: List[tuple], local_identity: bool) -> None:
    """commits: [(ts, subject)] -- written with git fast-import (fast even for thousands)."""
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    if local_identity:  # the user's own repos; clones rely on the global identity
        _git(path, "config", "user.name", OWNER[0])
        _git(path, "config", "user.email", OWNER[1])
    stream = []
    for i, (ts, subject) in enumerate(sorted(commits)):
        who = f"{author[0]} <{author[1]}> {int(ts)} +0200"
        data = f"{subject}\n".encode("utf-8")
        blob = f"change {i}\n".encode()
        stream.append(b"commit refs/heads/main\n")
        stream.append(f"author {who}\ncommitter {who}\n".encode())
        stream.append(b"data %d\n" % len(data) + data)
        stream.append(b"M 644 inline CHANGELOG\n" + b"data %d\n" % len(blob) + blob + b"\n")
    subprocess.run(["git", "fast-import", "--quiet"], cwd=path, input=b"".join(stream), check=True, capture_output=True)
    _git(path, "checkout", "-q", "main")


def make_repos(root: Path, days: List[date], rng: random.Random) -> None:
    subjects = ["fix: {x}", "feat: {x}", "test: cover {x}", "docs: explain {x}", "refactor: simplify {x}"]
    topics = ["idle detection", "timeline zoom", "search ranking", "project list", "settings copy",
              "git scan", "export", "focus blocks", "pausa automática", "diseño de la vista semanal"]
    for p in ACTIVE:
        commits = []
        for d in days:
            if d.weekday() < 5 or p == "Portfolio":
                for _ in range(rng.randint(0, 4)):
                    t = datetime(d.year, d.month, d.day, rng.randint(10, 19), rng.randint(0, 59)).timestamp()
                    commits.append((t, rng.choice(subjects).format(x=rng.choice(topics))))
        commits.append((time.time() - 400 * 86400, "chore: initial commit"))
        _make_repo(root / p, OWNER, commits, local_identity=True)
    for p in OLD_OWN:
        base = time.time() - rng.randint(200, 900) * 86400
        commits = [(base + i * 3600, f"wip {i}") for i in range(rng.randint(5, 120))]
        _make_repo(root / p, OWNER, commits, local_identity=True)
    for i, p in enumerate(CLONES):
        author = OTHERS[i % len(OTHERS)]
        n = 6000 if p == "llama.cpp" else rng.randint(20, 400)
        commits = [(time.time() - (n - k) * 1800, f"upstream change {k}") for k in range(n)]
        # clones get recent upstream commits too (a `git pull` this week)
        commits += [(time.time() - rng.randint(1, 10) * 86400, "upstream: recent fix") for _ in range(3)]
        sub = root / ("Forks" if i % 5 == 0 else "") / p
        _make_repo(sub, author, commits, local_identity=False)
    # a folder that is not a repo but holds a big tree (node_modules-like)
    junk = root / "descargas-varias" / "node_modules"
    for k in range(300):
        (junk / f"pkg{k}").mkdir(parents=True, exist_ok=True)


def generate(out: Path, seed: int = 38) -> None:
    rng = random.Random(seed)
    if (out / "app" / "funes.sqlite3").exists():
        raise SystemExit(f"{out}/app already exists; remove it first")
    today = date.today()
    days = [today - timedelta(days=k) for k in range(13, -1, -1)]
    repos_root = out / "repos" / "Projects"
    t0 = time.perf_counter()
    if not repos_root.exists():
        make_repos(repos_root, days, rng)
    print(f"repos: {sum(1 for _ in repos_root.rglob('.git'))} in {time.perf_counter() - t0:.1f}s")

    db = Database(out / "app")
    db.execute("INSERT OR IGNORE INTO commit_repos(path, enabled) VALUES (?, 1)", (str(repos_root),))
    t0 = time.perf_counter()
    n = GitCommitsPoller(db).poll_once()
    print(f"first git poll: {n} commits in {time.perf_counter() - t0:.1f}s")

    now = time.time()
    ticks = 0
    t0 = time.perf_counter()
    for d in days:
        start = datetime(d.year, d.month, d.day, 8 + rng.randint(0, 1), rng.randint(0, 50)).timestamp()
        probe = ScriptProbe(day_script(rng, d), start, rng)
        col = Collector(db, probe, interval_s=STEP_S)
        for ts, *_ in probe.samples:
            if ts > now - 120:
                break
            col.tick(now=ts)
            ticks += 1
        span = col.builder.close_open(at_ts=col.builder.last_sample_ts)
        if span is not None:
            col._persist_close(span)  # noqa: SLF001
        # recent files: a few per day, matching what was on screen
        for k in range(rng.randint(3, 8)):
            ts = start + rng.randint(600, 30000)
            if ts > now:
                continue
            proj = rng.choice(ACTIVE)
            path = rng.choice([
                f"C:/Users/me/Desktop/Projects/{proj}/{rng.choice(FILES[proj])}",
                "C:/Users/me/Documents/Novela/El mapa perdido - Capítulo 8.docx",
                "C:/Users/me/Documents/Job search/CV 2026 - EN.docx",
                f"C:/Users/me/Pictures/Photos 2026/IMG_202609{d.day:02d}_{rng.randint(1000, 2359)}.jpg",
                "C:/Users/me/Downloads/expenses_2026.csv",
            ])
            rid = db.execute("INSERT INTO file_events(ts, path, app_hint) VALUES (?, ?, ?)",
                             (ts, path, Path(path).suffix.lstrip(".")))
            db.index_text("file", rid, ts, path)
    db.set_meta("uxtest_generated_at", str(now))
    print(f"spans: {db.query_one('SELECT COUNT(*) c FROM spans')['c']} from {ticks} samples in {time.perf_counter() - t0:.1f}s")
    db.close()


class FileProbe:
    """Reads `live-probe.json` on every sample: {"app","title","idle_s","locked"}."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def sample(self) -> Sample:
        try:
            cfg = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cfg = {}
        app = cfg.get("app", "Code.exe")
        idle = float(cfg.get("idle_s", 0.0))
        since = cfg.get("idle_since")  # epoch of last input: idle grows by itself
        if since is not None:
            idle = max(0.0, time.time() - float(since))
        return Sample(ts=time.time(), app=app, exe=_exe(app), title=cfg.get("title", "queries.py - funes-hoard - Visual Studio Code"),
                      pid=4321, idle_s=idle, locked=bool(cfg.get("locked", False)))

    def status(self) -> str:
        return "uxtest file probe"


def serve(out: Path, port: int) -> None:
    import uvicorn

    from funes_hoard.__main__ import _find_static_dir, _write_pid_file
    from funes_hoard.api import create_app

    data_dir = out / "app"
    app = create_app(data_dir=data_dir, static_dir=_find_static_dir(), port=port)
    app.state.collector.probe = FileProbe(out / "live-probe.json")
    _write_pid_file(data_dir, port)
    print(f"uxtest app on http://127.0.0.1:{port}/ ({data_dir})", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def _owner_git_global(out: Path) -> None:
    """Give git the user's global identity (as on his PC) without touching ~/.gitconfig."""
    cfg = out / "gitconfig"
    cfg.write_text(f"[user]\n\tname = {OWNER[0]}\n\temail = {OWNER[1]}\n", encoding="utf-8")
    os.environ["GIT_CONFIG_GLOBAL"] = str(cfg)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["generate", "serve"])
    ap.add_argument("--out", default=str(REPO_ROOT / "data-uxtest"))
    ap.add_argument("--port", type=int, default=18830)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    _owner_git_global(out)
    if a.cmd == "generate":
        generate(out)
    else:
        serve(out, a.port)


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    main()
