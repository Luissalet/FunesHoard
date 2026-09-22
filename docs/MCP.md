# MCP tools

Transport: stdio. Adapter: `funes_hoard/mcp_server.py`, a standalone script
(stdlib + `httpx` + `mcp`) that talks to the running app at `FUNES_URL`
(default `http://127.0.0.1:8813`; anything that is not an `http://` loopback
address is refused). Each tool is a thin wrapper over the identically named
`POST /api/agent/<tool>` endpoint and returns exactly its JSON, so the
behaviour below is defined in `funes_hoard/queries.py` and `funes_hoard/api.py`.

## Contract

- **Read-only except `activity_pause`.** The agent surface is exactly the
  eight routes below (a test enumerates the app's routes). The pause can
  only *extend* a pause: it cannot resume recording, shorten a pause the
  human set, or turn "pause until resumed" into a timed pause. Rules,
  deletion, retention and export exist only in the UI.
- **Audited.** Every call, including rejected ones, is stored in
  `agent_calls` (tool, argument summary, duration, ok/error) and listed on
  the "Assistant activity" screen.
- **Titles are data.** Window titles are the user's literal screen
  contents; the server instructions tell the model to treat them as private
  data, never as instructions.

## Conventions

- **Times in results** are local ISO 8601 with UTC offset
  (`2026-09-21T09:00:00+02:00`), usually paired with a `human` string
  (`"yesterday 09:00-10:30, 1 h 30 min"`). Totals are seconds (`*_s`), and
  summaries add `*_human` strings so the model does not do arithmetic.
- **Time arguments** accept ISO dates or datetimes (`2026-09-20`,
  `2026-09-20T14:30`, a trailing `Z` or offset), `now`/`ahora`,
  `today`/`hoy`, `yesterday`/`ayer`, `this week`/`esta semana`,
  `last week`/`la semana pasada`, `this month`/`este mes`, and relative
  offsets `-2h`, `-3d`, `-30m`, `-1w` (also `2h ago`, `hace 2h`). Day words
  are periods, so the edge depends on the argument: `since`/`start` take the
  beginning (`since="ayer"` = yesterday 00:00), `until`/`end`/`before` take
  the end (`before="yesterday"` = today 00:00).
- **Limits** are small by default and capped at 100. Lists carry
  `truncated`/`has_more`; the timeline also returns `next_offset`.
  Titles and snippets longer than 160 characters are cut with `…` and
  flagged `title_truncated`/`text_truncated`.
- **Ids** (`id` of a span, `ref_id` of a search hit, `id` of a file event)
  are the SQLite row ids: stable for the life of the row.

## Tools

### activity_now()

What is in front of the user right now.

```json
{"recording": true, "paused": false, "paused_until": null, "idle_s": 0.0,
 "utc_offset": "+02:00", "now": "2026-09-22T21:10:19+02:00",
 "app": "Code.exe", "title": "main.py - Atlas - Visual Studio Code",
 "category": "Coding", "project": "Atlas", "kind": "active",
 "since": "2026-09-22T21:09:03+02:00"}
```

While paused, `app`/`title`/... are `null` and `paused_until` is the ISO
end of the pause (`null` for "until resumed").

### activity_where_was_i(before=None, contexts=5, all_categories=False)

The resume-context call: the last *distinct* contexts before `before`
(default now), most recent first. A context is one project, or one app when
no project is known; consecutive spans of it are merged, away/locked time
and blips under 10 s are skipped, each context appears once, and the last
one is cut at `before`. `contexts` is 1-20. Media, Communication and Games
are skipped and a context with a project ranks ahead of a bare app, unless
`all_categories=true`. Files and commits are those recorded during the
context (up to 5 each); files inside the context's own project folder come
first. `recent_titles` holds up to 3 distinct titles of the context, most
recent first: when it ends in a terminal, the editor title that names the
file is the second one.

```json
{"before": "2026-09-22T00:00:00+02:00",
 "contexts": [
   {"project": "Atlas", "app": "Code.exe", "title": "README.md - Atlas - Visual Studio Code",
    "start": "2026-09-21T17:55:00+02:00", "end": "2026-09-21T18:35:00+02:00",
    "duration_s": 2400, "human": "yesterday 17:55-18:35, 40 min", "files": [], "commits": []},
   {"project": null, "app": "Spotify.exe", "title": "Focus playlist",
    "start": "2026-09-21T17:25:00+02:00", "end": "2026-09-21T17:55:00+02:00",
    "duration_s": 1800, "human": "yesterday 17:25-17:55, 30 min", "files": [], "commits": []}]}
```

### activity_timeline(start=None, end=None, min_minutes=None, limit=20, offset=0, around=None)

Spans in chronological order (active, away or locked), clipped to the
range. Default range: today. A `start` that names a day or period with no
`end` (`"ayer"`, `"2026-09-20"`, `"this week"`) selects that whole period;
otherwise the range is `start`..`end` (`end` defaults to now). `around`
(a search hit's `ts`, or any time word) replaces both with 30 minutes
either side of that moment; passing it together with `start`/`end` is a
`400 bad_arguments`. Spans shorter than `min_minutes` are skipped; unset,
it is 2 for a day or less, 5 for a longer range and 0 with `around`. Page
with `offset=next_offset`.

```json
{"start": "2026-09-21T00:00:00+02:00", "end": "2026-09-22T00:00:00+02:00",
 "human_range": "yesterday, whole day", "total": 12,
 "items": [{"id": 13, "start": "2026-09-21T09:00:00+02:00", "end": "2026-09-21T10:30:00+02:00",
            "kind": "active", "app": "Code.exe", "title": "main.py - Lumen - Visual Studio Code",
            "category": "Coding", "project": "Lumen", "duration_s": 5400,
            "human": "yesterday 09:00-10:30, 1 h 30 min"}],
 "truncated": true, "has_more": true, "next_offset": 2}
```

### activity_summary(day=None, start=None, end=None, group_by="category")

Totals for a day, a period or a range. `group_by` is `category`, `app`
(top 10), `project` or `all` and decides which `by_*` maps are returned
(seconds per key, largest first), each with a `by_*_human` twin of
ready-to-read strings. Spans are clipped to the window, so a span
that crosses midnight counts once. `first_activity`/`last_activity` ignore
away and locked time. `context_switches` counts app changes between spans
that lasted at least 10 s. A focus block is at least 25 minutes on one
project (or category when there is no project) where every interruption,
whatever it was spent on, lasted at most 2 minutes; up to 10 are returned,
longest first.

```json
{"start": "2026-09-21T00:00:00+02:00", "end": "2026-09-22T00:00:00+02:00",
 "human_range": "yesterday, whole day",
 "active_s": 30600, "active_human": "8 h 30 min", "away_s": 3900, "away_human": "1 h 05 min",
 "by_project": {"Lumen": 12300, "Atlas": 6600},
 "first_activity": "2026-09-21T09:00:00+02:00", "last_activity": "2026-09-21T18:35:00+02:00",
 "context_switches": 10,
 "focus_blocks": [{"key": "Lumen", "start": "2026-09-21T09:00:00+02:00", "end": "2026-09-21T10:30:00+02:00",
                   "duration_s": 5400, "human": "yesterday 09:00-10:30, 1 h 30 min"}],
 "longest_focus_block_s": 5400}
```

### activity_search(query, since=None, until=None, limit=10)

Window titles, opened file paths and commit subjects, newest first. The
query is split into words (punctuation is ignored, so `funes-hoard`, `C++`
or a stray quote are safe) and each word matches as a prefix. All words
must match; if that finds nothing and there are several words, any word is
tried and `matched` becomes `"any word"`. Uses SQLite FTS5 (accent-
insensitive) when the platform's sqlite3 has it, a `LIKE` search otherwise.
Redacted titles are never indexed. A query with no letters or digits is a
`400 empty_query`. A window-title hit (`source: "span"`) also carries how
long that window was open (`duration_s`, `human`), and `windows_open_s`/
`windows_open_human` add those up over the returned hits; pass a hit's `ts` to
`activity_timeline(around=...)` for what surrounded it.

```json
{"query": "duckdb", "matched": "all words",
 "items": [{"source": "span", "ref_id": 26, "ts": "2026-09-22T10:30:00+02:00",
            "when": "today 10:30", "text": "[DuckDB] documentation - Google Chrome",
            "duration_s": 720, "human": "today 10:30-10:42, 12 min"}],
 "truncated": false, "has_more": false}
```

### activity_recent_files(since=None, limit=15)

Files from the Windows Recent Items list (resolved from the `.lnk` files),
newest first. Default window: the last 7 days.

```json
{"since": "2026-09-15T21:10:19+02:00",
 "items": [{"id": 3, "path": "C:/Users/demo/Desktop/Side projects/Atlas/README.md",
            "ts": "2026-09-22T11:50:00+02:00", "when": "today 11:50", "app_hint": "md"}],
 "truncated": true, "has_more": true}
```

### activity_projects(since=None, limit=10)

Projects by active time since `since` (default: the last 30 days), with
the last time each was on screen and the number of commits recorded for the
repo of the same name.

```json
{"since": "2026-08-23T21:10:19+02:00",
 "items": [{"project": "Atlas", "time_s": 39360, "time_human": "10 h 56 min",
            "last_touched": "2026-09-22T21:10:03+02:00", "commits": 2}],
 "truncated": false, "has_more": false}
```

### activity_pause(minutes=15)

Pauses recording for 1-1440 minutes, unless it is already paused for
longer or until the user resumes it; `note` says which happened. Recording
resumes by itself when the pause ends.

```json
{"paused": true, "until": "2026-09-22T21:40:00+02:00", "until_resumed": false,
 "note": "recording paused for 30 min; it resumes by itself"}
```

## Errors

The app answers errors as `{"error": "<code>", "message": "<what to do>"}`
with a 4xx status, and the adapter raises them as `ToolError("<code>: <message>")`:

| code | when |
| --- | --- |
| `bad_time` | a time argument could not be parsed; the message lists what is accepted |
| `bad_arguments` | a value is out of range or of the wrong type (e.g. `minutes: Input should be less than or equal to 1440`) |
| `bad_group_by` | `group_by` is not one of category/app/project/all |
| `empty_query` | the search query has no words |
| `funes-hoard_unavailable` | the app is not running: "Start it from Faustus (Apps) or with 'Iniciar Funes's Hoard.cmd', then retry." |
| `funes-hoard_timeout` | the app did not answer within 15 s |

## Using it from any MCP client

```json
{
  "mcpServers": {
    "funes-hoard": {
      "command": "C:/path/to/Funes's Hoard/.venv/Scripts/python.exe",
      "args": ["C:/path/to/Funes's Hoard/funes_hoard/mcp_server.py"],
      "env": { "FUNES_URL": "http://127.0.0.1:8813" }
    }
  }
}
```

The app itself must be running (`Iniciar Funes's Hoard.cmd`); the adapter
holds no data of its own.
