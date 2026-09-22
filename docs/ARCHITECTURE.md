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
  retention.py      purge spans/files/commits older than N days
  scheduler.py      background thread driving the three pollers above
  jobs.py           tiny in-memory job runner (reclassify, retention-now)
  db.py             sqlite3 (WAL), one shared connection, schema + seeding
  queries.py        read functions shared by the UI API and the agent API
  timeparse.py      date-word / relative-offset parsing (hoy/ayer/-2h/...)
  demo.py           --demo synthetic data, built by driving the real Collector
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
per `Database` instance (not one connection per query -- the app samples at
1 Hz, and opening a fresh connection every time was the single biggest cost
in early profiling: it took the test suite from ~9s to ~34s). All access
goes through `Database.execute`/`query`/`query_one`, serialized by a
`threading.RLock`. Search uses SQLite FTS5 when the platform's `sqlite3`
build supports it (checked at startup, `Database.fts_available`), falling
back to `LIKE` queries otherwise -- see the README's Windows-risk note.

## Decisions worth explaining

- **Agent surface is a fixed set of HTTP endpoints, not general SQL.** The
  `/api/agent/<tool>` routes are individually declared FastAPI routes,
  nothing under that prefix can reach the rules or privacy tables --
  verified by `tests/test_api.py::test_agent_api_has_no_rules_or_delete_or_export_endpoints`.
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
