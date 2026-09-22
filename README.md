# Funes's Hoard
### Where was I? What was I doing before lunch? How much of this week actually went to Faustus?
**Your computer's episodic memory: foreground app, window, files opened and commits made, kept in a local SQLite file and filtered for privacy before anything is written.**

[Español](README.es.md) · [Run locally](#run-locally-on-windows) · [Connect an AI](docs/MCP.md) · [Portfolio](https://luissalet.github.io/Portfolio/#projects)

![Today view: a day timeline by category, active/away totals, and focus blocks](docs/media/today.png)
*Actual application, three days of synthetic demo data (`--demo`, no real window titles).*

## Why

A local assistant with a 27B model and no memory of the desktop can only
guess when you ask "where was I?" or "how did today go?". It has no record
of what was actually in front of you, so it either invents an answer or
asks you to explain everything from scratch, every time. Funes's Hoard
records the one thing a language model structurally cannot see on its
own -- the sequence of what was on screen -- turns it into spans, day
summaries and focus blocks, and answers those questions from data instead
of guesses.

## What is implemented

| Area | Available now | Boundary |
| --- | --- | --- |
| Collection | Foreground app/window/idle/locked sampled at 1 Hz; merged into active/away/locked spans; away time back-dated to when input actually stopped; sleep/hibernate gaps detected and not bridged; open span flushed every 30s (crash loses <30s) | Windows collector uses `ctypes`/`psutil`; the Linux collector (dev-only, best effort via `xdotool`/`xprintidle`) is not the production target |
| Privacy | Exclusion rules drop a sample before it is ever stored (password managers, incognito/private browsing, EN+ES); redaction rules replace only the title (banking/login keywords, EN+ES); pause 15m/1h/until-resumed with automatic expiry; retention purge; delete-a-range; export JSON | Browser **domain** extraction from titles is not implemented (unreliable from title text alone) -- see Boundaries below |
| Classification | Ordered app/title-regex/domain rules with sensible defaults for common Windows apps; project auto-detection from VS Code titles and known git repo names; live preview ("this rule would reclassify N spans") before committing; reapply-to-history as a background job, versioned so reads stay cheap | JetBrains title parsing is generic (`A - B` heuristic), not IDE-specific |
| Derived knowledge | Day/week/range summaries (active/away, by category/app/project, first/last activity), context switches (>=10s dwell), focus blocks (>=25 min, interruptions <=2 min), "where was I" (resume-context) merging by project | — |
| Other sources | Recent files via a from-scratch `.lnk` (Shell Link) binary parser -- no COM dependency; git commits scanned from configured repo roots, filtered by author | The optional browser-address-bar UI Automation reader described as a stretch goal was not built: title-based project/category detection already covers the common case, and a robust cross-browser UI Automation reader is a project of its own -- documented here as descoped, not silently dropped |
| Search | SQLite FTS5 over titles/files/commit subjects, with snippet highlighting | Falls back to substring `LIKE` search if the platform's `sqlite3` build lacks FTS5 (checked at startup) -- confirmed present on the Linux build used for development; **not verified on the Windows 3.13 python.org build**, see Windows risk below |
| Agent API | Read-only except `activity_pause`; every call recorded and shown in "Assistant activity"; verified with a real MCP client over stdio against a live app | — |
| UI | Today (timeline, totals, focus blocks), Week, Search, Projects, Files & commits, Rules (with live preview), Privacy, Assistant activity; EN/ES; light/dark | — |

More screens: [Privacy](docs/media/privacy.png) (pause controls, exclusion/
redaction rules, retention) · [Search](docs/media/search.png) (FTS5 with
snippet highlighting) · [Assistant activity](docs/media/assistant-activity.png)
(every agent call, auditable).

## Connect it to Faustus

The app declares itself with `faustus-plugin.json`. Start it, then in
Faustus go to **Connectors -> Nearby apps -> Add**.

| Tool | What it does | Read-only? |
| --- | --- | --- |
| `activity_now` | Current app/title/project, idle seconds, recording state | yes |
| `activity_where_was_i` | Resume context: last work contexts before a moment, with files/commits | yes |
| `activity_timeline` | Merged spans for a range | yes |
| `activity_summary` | Day/range totals, focus blocks, context switches | yes |
| `activity_search` | Find when a title/file/commit appeared | yes |
| `activity_recent_files` | Recently opened files | yes |
| `activity_projects` | Time per project, last touched, commits | yes |
| `activity_pause` | Pause recording (cannot resume early, change rules, delete or export) | **no** (the only write) |

It also works with any MCP client over stdio -- see
[docs/MCP.md](docs/MCP.md) for the full reference and a config snippet.

## Run locally on Windows

Double-click **`Iniciar Funes's Hoard.cmd`** (or run
`scripts\start.ps1`), which creates the venv, installs pinned dependencies
and builds the frontend on first run, then starts the app at
`http://127.0.0.1:8813`.

Manual steps:

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements-lock.txt
cd frontend; npm ci; npm run build; cd ..
.venv\Scripts\python -m funes_hoard
```

Add `--demo` to run against synthetic data in `data-demo/` instead of
recording your real desktop (`--data-dir` and `--no-browser` are also
available; see `python -m funes_hoard --help`).

## Architecture

FastAPI + a 1 Hz collector thread + SQLite (WAL), with pure, unit-tested
core logic (span building, privacy, classification, summaries) that never
imports FastAPI or sqlite. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Tests

```
.venv/bin/python -m pytest tests/ -q
```

**85 tests pass in about 10 seconds**, covering: span merging and away/
locked back-dating, sleep-gap handling, crash-safe flushing, privacy
exclusion (asserting the row never exists) and redaction, pause expiry,
retention purge, the `.lnk` parser against a fixture built in the test
itself (a real bug -- `LocalBasePathOffset` was being read from the wrong
byte offset per MS-SHLLINK -- was caught and fixed by this test),
classification and project detection, focus blocks and context switches,
date-word parsing (hoy/ayer/this week/-2h), the full FastAPI surface
including the browser-attack guard, the manifest check, and an end-to-end
MCP protocol test that spawns `mcp_server.py` as a real subprocess against
a live app.

`npm ci && npm run build` (in `frontend/`) passes with zero TypeScript
errors.

## Privacy and limits

- Binds `127.0.0.1` only. No telemetry, no outbound network calls except
  the git commit scan (local `git log`, no network) and this UI's own
  fetches to itself.
- Exclusion rules run **before** a sample is ever written to disk; this is
  tested by asserting the row never exists, not just that it gets deleted
  later.
- The agent's write access is exactly one action (pause) and nothing else;
  the enumerated `/api/agent/<tool>` routes are the entire agent-facing
  surface, verified by a test that every other write path 404s under that
  prefix.
- Results are capped (5-100 items depending on the tool) with an explicit
  `truncated`/`has_more` flag, since the intended consumer is a small local
  model with a finite context window.

### Boundaries (things this deliberately does not do)

- No browser-domain extraction from titles (unreliable without a UI
  Automation reader; descoped rather than shipped half-working).
- Retention purge, in addition to spans, also clears `file_events` and
  `commits` in the same window -- a deliberate widening from "spans only"
  so that "delete my history" actually means it; see
  `docs/ARCHITECTURE.md`.
- FTS5 search falls back to substring search if unavailable; see the
  Windows risk note below.

### Windows-specific risk not testable from this Linux build environment

This was built and tested on Linux (Python 3.11); the production target is
Windows (Python 3.13 at `C:\Python313`). Two things could not be verified
here and should be checked on first real Windows run:

1. **SQLite FTS5 availability** in the python.org Windows 3.13 build. The
   app checks for it at startup and falls back to substring search
   automatically (`Database.fts_available`, see `docs/ARCHITECTURE.md`),
   so search still works either way -- but the *quality* of ranking differs.
2. **`WindowsProbe`** (`ctypes` calls into `user32`/`kernel32`, plus
   `psutil`) cannot run at all on Linux; its logic around idle detection
   and lock-screen detection is exercised in tests only through the
   `Probe` interface with a fake, never against real Win32 calls.
