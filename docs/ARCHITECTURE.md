# Architecture

## Modules

```
funes_hoard/
  core/
    spans.py       Sample/Span dataclasses + SpanBuilder (pure, unit-tested)
    privacy.py      exclusion/redaction rules + PauseState (pure)
    classify.py     classification rules + project detection (pure)
    summary.py      day totals, focus blocks, context switches, where-was-i (pure)
    lnk.py          minimal .lnk (Shell Link) binary parser (pure)
  collectors/
    base.py         Probe protocol
    fake.py         FakeProbe / ClockFakeProbe for tests and demo
    windows.py      WindowsProbe (ctypes user32/kernel32 + psutil), win32-only
    linux.py        LinuxProbe (best effort: xdotool/xprintidle)
  collector.py      Collector: probe -> privacy -> SpanBuilder -> sqlite, 1 Hz
  recent_files.py   .lnk scanning of the Windows Recent folder, every 60s
  git_watch.py      git log scanning of configured repo roots, every 10 min
  retention.py      purge / delete-range for spans, files, commits and their search rows
  scheduler.py      background thread driving the three pollers above
  jobs.py           tiny in-memory job runner (reclassify, retention-now)
  db.py             sqlite3 (WAL), one shared connection, schema + seeding
  queries.py        read functions shared by the UI API and the agent API
  timeparse.py      date-word / relative-offset parsing (hoy/ayer/-2h/...), ISO output
  errors.py         BadInput: {error, message} 400s raised from the query layer
  demo.py           --demo synthetic data, built by driving the real Collector
  backend.py        Hoard Link config load/save + the "Write my day" prompt
  hoard_link/       vendored shared model backend (see "Model backend" below)
  api.py            FastAPI app: guard middleware, UI API, /api/agent/*
  mcp_server.py     standalone stdio MCP adapter (HTTP client only)
  __main__.py       CLI entry point
```

Core modules (`core/`) never import FastAPI or sqlite -- they operate on
plain dataclasses so the hardest logic (span merging, focus blocks, the
`.lnk` parser) is unit-testable without spinning up the app.

## Data flow

```
Probe.sample() (1/s)
   -> apply_privacy()          drop or redact BEFORE anything is stored
   -> SpanBuilder.add_sample() merge into active/away/locked spans
   -> classify()               category + project, versioned by rules_version
   -> sqlite (spans table)     closed spans INSERT, open span UPDATEd every 30s
```

The **open span is flushed to sqlite every 30 seconds** (`FLUSH_INTERVAL_S`
in `collector.py`) so a crash loses at most the last 30s of the current
span, not the whole session -- proven by `tests/test_collector.py`.

A **sleep/hibernate gap** (no samples for >90s, `SpanBuilder.sleep_gap_s`)
closes the open span at the *last* sample's timestamp instead of bridging
the gap, so a laptop that slept for two hours does not get credited two
hours of continuous activity.

A sample dropped by an **exclusion rule**, or taken while **paused**, closes
the open span at that moment (`SpanBuilder.interrupt`). Otherwise returning
to the same window after a minute in a password manager would stretch the
old span over the private interlude. A process that is killed leaves its
open span flagged `open=1`; the next start closes such rows, so a stale row
is never reported as the current window.

The collector stops the span at the last real sample, not the wall clock.

## Threads

- **Collector** (`collector.py`): one Python thread, ticks every
  `sample_interval_s` (default 1s). Holds the single `SpanBuilder`.
- **BackgroundScheduler** (`scheduler.py`): one thread, checks every 5s
  whether it is time to run the recent-files poller (60s), the git poller
  (10 min), or the retention purge (24h). Kept separate from the collector
  so a slow git scan never delays a sample.
- **JobManager** (`jobs.py`): submits reclassify-all / purge-now onto a
  fresh daemon thread per job, so the HTTP handler returns immediately with
  a `job_id` and `/api/jobs/<id>` reports progress.
- FastAPI/uvicorn's own worker handles HTTP requests; all three background
  components talk to sqlite through the same `Database` instance, guarded
  by one re-entrant lock (see below).

## Database

SQLite (stdlib `sqlite3`), WAL mode, **one shared long-lived connection**
per `Database` instance rather than one per query (the collector writes
every second). All access goes through `Database.execute`/`query`/
`query_one`, serialised by a `threading.RLock`; the connection is closed on
shutdown. Tables: `spans`, `file_events`, `commits`, `commit_repos`,
`classify_rules`, `privacy_rules`, `agent_calls`, `day_narratives` (the
"Write my day" cache, one row per calendar day), `meta` (settings such as
`paused_until`, `retention_days`, `commit_authors`, `known_repos`,
`write_my_day_enabled`).

Search uses an FTS5 table (`search_fts`, `unicode61 remove_diacritics 2`)
when the platform's `sqlite3` supports it (checked at startup,
`Database.fts_available`), and `LIKE` otherwise. Queries are tokenised into
quoted prefix terms, never passed to FTS5 verbatim. Delete-range and
retention remove the index rows with the table rows.

## HTTP surface

- `browser_attack_guard` middleware: the `Host` header must be a loopback
  name *with this app's port*; non-GET requests with an `Origin` must come
  from `http://<loopback>:<this port>`, and `Sec-Fetch-Site: cross-site` is
  refused. No CORS.
- Errors are `{"error", "message"}` everywhere; request-validation errors
  become `400 bad_arguments` naming the field (and are audited when they
  hit an agent route).
- The SPA fallback resolves the requested path and only serves files that
  are inside `frontend/dist`; anything else gets `index.html`.
- UI endpoints return epoch seconds; `/api/agent/*` returns the same query
  results through `queries.agent_view`, which converts times to local ISO
  8601 with offset and truncates long titles.

## Windows specifics

- `collectors/windows.py` declares `argtypes`/`restype` for every Win32
  call (handles are pointer-sized) and computes idle time from the 32-bit
  `GetTickCount` modulo 2^32, the way `LASTINPUTINFO.dwTime` is defined.
- `git` runs with `encoding="utf-8"`, `errors="replace"` and, on Windows,
  `CREATE_NO_WINDOW`, so a hidden app does not flash consoles.
- `scripts/start.ps1` starts the app hidden with the repo root as working
  directory and waits for `/api/health`. The app writes `data/funes.pid`
  with its own PID (the venv `python.exe` is a launcher around the real
  interpreter, so the launcher's PID is not the one holding the port);
  `scripts/stop.ps1` uses it, verifies the command line, and falls back to
  the process listening on the port.

## Model backend

Funes's Hoard vendors [Hoard Link](../funes_hoard/hoard_link/), the shared
resolver every Faustus plugin app uses so a GPU-bound machine is never
asked to load a second model server: `funes_hoard/backend.py` turns
`data/backend.json` plus the environment into a `LinkConfig` for one `Link`
per app (`app.state.link`, created at startup and `await`-closed at
shutdown), and builds the prompt for the one feature that needs a model.

- **`GET /api/backend`** returns `await link.status()` (every capability's
  `Resolution`, honest about *why* nothing resolved) plus whether "Write my
  day" is enabled. **`PUT /api/backend/config`** merges a UI patch into
  `backend.json` (`funes_hoard/backend.py::apply_config_patch`) and, since
  Hoard Link never caches explicit configuration, just swaps
  `link.config` in place -- no reconnect needed. The Faustus token is
  never echoed back, only `faustus_token_set`. **`POST /api/backend/recheck`**
  clears Hoard Link's 30 s loopback-probe cache the only way that never
  touches the vendored file: it builds a fresh `Link` (via
  `app.state.link_factory`, real `Link` unless a test overrides it) and
  closes the old one.
- **"Write my day"** (`POST /api/day-narrative`, UI-only -- not one of the
  eight agent tools) sends `queries.agent_view(activity_summary(...))` --
  the exact compact shape the `activity_summary` MCP tool returns, never
  raw or redacted window titles -- to `link.chat(capability="llm")`, and
  caches the result in `day_narratives` keyed by calendar day
  (`day_start_ts` lets retention/delete-range purge it like any other
  table when its day is removed). A day with no active time is refused
  (`no_activity`) before any model call, so nothing invented is ever
  cached; an empty answer (a reasoning model that spent its whole
  `NARRATIVE_MAX_TOKENS` budget thinking) is `llm_empty` and not stored
  either. A `write_my_day_enabled` meta flag lets
  the human turn the feature off; either way the app works with no model
  running at all, `resolve("llm")`'s `reason` string explaining what is
  missing directly in the Settings screen and on the Today card.
- `link` / `link_factory` are constructor parameters of `create_app` purely
  for tests (`tests/fakes.py::FakeLink`): the suite never makes a real
  network call.

## Decisions worth explaining

- **Agent surface is a fixed set of HTTP endpoints, not general SQL.** The
  `/api/agent/<tool>` routes are individually declared FastAPI routes,
  nothing under that prefix can reach the rules or privacy tables --
  verified by `tests/test_security.py::test_agent_surface_is_exactly_the_eight_tools`.
- **The agent's pause can only extend.** `Collector.pause_at_least` keeps a
  longer or indefinite pause as it is, so the one write the agent has can
  never amount to resuming early.
- **Classification is versioned, not live-recomputed on every read.** Each
  span stores the `rules_version` it was classified under; "Reapply to
  history" (or the agent-inaccessible `/api/classify/reapply`) recomputes
  and bumps the version as a background job, so reads stay O(rows in range)
  instead of O(rows x rules) on every request.
- **The MCP adapter never imports the package.** It is launched by absolute
  path (`mcp_server.py`), so it has to work standalone; it talks to the app
  over loopback HTTP, which also means the exact same code path is
  exercised whether the caller is the MCP adapter, Faustus's HTTP client,
  or the tests' `TestClient`.
