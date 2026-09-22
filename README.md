# Funes's Hoard
### Where was I? What was I doing before lunch? How much of this week actually went to Faustus?
**Your computer's episodic memory: foreground app, window, files opened and commits made, kept in a local SQLite file and filtered for privacy before anything is written.**

[Español](README.es.md) · [Run locally](#run-locally-on-windows) · [Connect an AI](docs/MCP.md) · [Portfolio](https://luissalet.github.io/Portfolio/#projects)

![Day view: yesterday's timeline zoomed to the active hours, one segment pinned, totals by category, app and project](docs/media/today.png)
*Actual application, three days of synthetic demo data (`--demo`, no real window titles).*

## Why

A local assistant has no record of what was on the user's screen. Asked
"where was I?", "how did today go?" or "what was that DuckDB page I had
open yesterday?", a language model can only invent an answer or ask the
user to reconstruct it. Funes's Hoard records the one thing the model
cannot see: the sequence of windows in front of the user, with idle and
locked time, the files they opened and the commits they made. It turns
that into spans, day summaries, focus blocks and resume-context answers,
and gives the assistant eight small tools to ask for them.

## What is implemented

| Area | Available now | Boundary |
| --- | --- | --- |
| Collection | Foreground app, window title, idle and locked state sampled every second on Windows (`ctypes` + `psutil`); merged into active/away/locked spans; away time back-dated to when input stopped; sleep gaps and excluded or paused moments close the span instead of bridging it; the open span is flushed every 30 s and a crashed session's span is closed on the next start | The Linux probe (`xdotool`/`xprintidle`) is a development aid; the Win32 calls themselves were not run in this build environment (see the Windows note) |
| Privacy | Exclusion rules drop a sample before it is stored (password managers, private/incognito windows, EN+ES); redaction rules keep the app and replace the title; invalid rules are rejected, not silently ignored; pause 15 min / 1 h / until resumed, with automatic resume; retention purge; delete a range (overlapping spans and their search rows included); JSON export | Rules apply from the moment they are added, not retroactively to stored titles |
| Classification | Ordered app / title-regex / domain-in-title rules with defaults for common Windows apps; project detection from VS Code (hyphenated folders, remote and Insiders titles), JetBrains and Visual Studio titles, plus the names of discovered git repos; live preview ("would reclassify N spans"); reapply to history as a background job with progress | Browser domains are not read from the address bar; a "domain" rule matches text in the title |
| Derived knowledge | Day, week and range totals by category/app/project, clipped at the window edges; first/last activity; context switches (>= 10 s dwell); focus blocks (>= 25 min, each interruption <= 2 min); "where was I" with distinct contexts, their last title, files and commits | Focus is measured from window time only; it says nothing about attention |
| Other sources | Recent files from a Shell Link (`.lnk`) parser written from the spec (Unicode paths, path suffixes, truncated files rejected); git commits from configured roots, filtered by configured authors or, by default, each repo's own git identity | Files opened without passing through Windows Recent Items are not seen |
| Search | SQLite FTS5 over titles, file paths and commit subjects, accent-insensitive, prefix words, safe for any input; falls back to "any word" when all words find nothing | If the platform's sqlite3 lacks FTS5 the app uses `LIKE` search (checked at startup) |
| Agent API | Eight tools, read-only except a pause that can only extend; local ISO times and human strings in every result; small limits with `has_more`/`next_offset`; every call audited, including rejected ones | The agent cannot resume, change rules, delete or export, by design |
| Interface | Today (zoomable timeline, legend, pinned details), Week (navigable), Search (date filter), Projects (range picker), Files & commits, Rules, Privacy, Assistant activity; English/Spanish; light/dark | Desktop layout; not designed for phones |

More screens: [Search](docs/media/search.png) · [Privacy](docs/media/privacy.png) ·
[Assistant activity](docs/media/assistant-activity.png) (real calls made through
the agent API by `scripts/screenshots.py`, including a rejected one).

## Connect it to Faustus

The app declares itself with `faustus-plugin.json`. Start it, then in
Faustus go to **Connectors -> Nearby apps -> Add**.

| Tool | What it does | Read-only? |
| --- | --- | --- |
| `activity_now` | Current app, title, project, idle seconds, recording or paused (and until when) | yes |
| `activity_where_was_i` | Resume context: last distinct contexts before a moment, with title, files and commits | yes |
| `activity_timeline` | Spans for a day or range, paginated with `offset` | yes |
| `activity_summary` | Totals grouped by category/app/project, focus blocks, context switches | yes |
| `activity_search` | When a title, file or commit containing some words appeared | yes |
| `activity_recent_files` | Recently opened files | yes |
| `activity_projects` | Time per project, last touched, commits | yes |
| `activity_pause` | Pause recording; never shortens an existing pause | **no** (the only write) |

It works with any MCP client over stdio; [docs/MCP.md](docs/MCP.md) has the
output of every tool, the error codes and a config snippet. The skill
[`skills/where-was-i/SKILL.md`](skills/where-was-i/SKILL.md) tells a local
model which tool to pick and the traps.

## Run locally on Windows

Double-click **`Iniciar Funes's Hoard.cmd`**. It runs `scripts\start.ps1`,
which on first run creates `.venv` (Python 3.11+, preferring
`C:\Python313`), installs `requirements-lock.txt` and builds the interface
if `frontend\dist` is missing; later runs reinstall only when the lock
changed. It then starts the app hidden, with the repo root as working
directory, waits for `/api/health` and opens `http://127.0.0.1:8813`. If the
app is already running it just opens it. **`Detener Funes's Hoard.cmd`**
stops it. `scripts\start.ps1 -Demo` runs on synthetic data.

Manual steps:

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements-lock.txt
cd frontend; npm ci; npm run build; cd ..
.venv\Scripts\python -m funes_hoard
```

Flags: `--demo` (synthetic data in `data-demo/`, nothing recorded from the
desktop), `--data-dir`, `--port`, `--no-browser`. Data lives in `data/`
(or `FUNES_DATA_DIR`). The app only listens on loopback; `--host` refuses
anything else.

## Architecture

FastAPI, one collector thread sampling once a second, one scheduler thread
for recent files, git and retention, and SQLite in WAL mode. The span,
privacy, classification and summary logic is pure Python with no FastAPI
or sqlite imports. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Tests

```
.venv/bin/python -m pytest -q          # 179 passed in about 10 s
cd frontend && npm ci && npm run build  # 0 TypeScript errors
```

The suite covers span merging, away/locked back-dating, sleep gaps,
excluded and paused interludes, crash flushing and stale open spans;
privacy rules (asserting the excluded row never exists) and rule
validation; pause expiry and the agent's extend-only pause; retention and
delete-range including the search index; the `.lnk` parser on fixtures
built in the tests (ANSI, Unicode, suffix, truncated); project detection
from editor titles; focus blocks, switches, clipping and where-was-i on
scripted days; date words in both languages and their range edges; search
on both the FTS5 and `LIKE` paths with hostile input; the browser guard
(host and origin ports, `null` origin), the SPA path-traversal fix and the
exact agent route list; the Windows probe logic with the Win32 calls faked
(tick wraparound, access-denied executables); git scanning with UTF-8
subjects, author filters and hidden consoles; the CLI; the manifest check;
and an MCP protocol test that spawns `mcp_server.py` over stdio against a
live app and checks keywords, annotations, error passthrough and the audit
log.

## Privacy and limits

- Everything stays in `data/funes.sqlite3` on this computer. No telemetry
  and no network use; `git log` runs locally.
- Exclusion rules run before a sample is written; a test asserts the row
  never exists. Redacted titles are never put in the search index.
- The assistant sees only what the eight tools return and can do exactly
  one thing: pause (or lengthen a pause). Every call is listed in
  "Assistant activity".
- Results are capped (default 5-40 items, maximum 100) and long titles are
  truncated, because the intended consumer is a local model with a finite
  context window.

### Boundaries

- No browser address-bar reader: the optional UI Automation reader in the
  original plan was descoped rather than shipped fragile. Title rules cover
  most cases.
- Retention and delete-range also remove `file_events` and `commits` in the
  same window, not only spans, so deleting history means all of it.

### Windows-specific risk not testable from this Linux build environment

Built and tested on Linux with Python 3.11; the target is Windows with
Python 3.13. Check on the first real run:

1. **The Win32 calls** in `WindowsProbe` (`GetForegroundWindow`,
   `GetLastInputInfo`, `OpenInputDesktop`) never ran here. Their signatures
   are declared and the logic around them is tested with the calls faked,
   but lock detection relies on `OpenInputDesktop` failing on the secure
   desktop, which will also report a UAC prompt as "locked".
2. **FTS5 in the python.org 3.13 sqlite3**: the app falls back to `LIKE`
   search if it is missing.
3. **The PowerShell launchers** were parsed and the start flow was run under
   PowerShell 7 on Linux (without `-WindowStyle Hidden`, which only exists on
   Windows); `stop.ps1` uses `Get-CimInstance` and `Get-NetTCPConnection`,
   which were not run.
