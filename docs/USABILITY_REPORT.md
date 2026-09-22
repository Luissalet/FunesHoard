# Usability report

First real use of Funes's Hoard, walking every scenario in
[USE_CASES.md](USE_CASES.md) twice: as a person in the browser (Playwright,
1280x800 and 1920x1080, English and Spanish, screenshots read one by one)
and as a local model over real MCP stdio. This pass records findings only;
the fixes come in the next pass and each finding says what that fix should
be.

## How it was walked

- **Data** (`scripts/uxtest_data.py generate`, into the gitignored
  `data-uxtest/`): 38 git repos laid out like the user's
  `Side projects` (6 active projects with commits over the last two
  weeks, 12 old experiments, 20 clones of other people's projects with other
  authors, one with 6,000 commits, generic names such as `docs`, `chat`,
  `python`, `tools`, plus a 300-folder non-repo tree), then two weeks of
  activity produced by driving the real `Collector` with a scripted probe
  every 5 s: 90,103 samples -> 641 spans. Titles are realistic Windows titles
  in English and Spanish (VS Code, Windows Terminal, Chrome docs pages, job
  boards, Word and Obsidian for the novel, Photos, fan films on YouTube,
  Teams and Zoom, a password manager, a Spanish private window, a bank page),
  with alt-tab blips, coffee and lunch away, a locked screen and overnight
  gaps. 86 recent-file rows were inserted directly (see "Not tested").
- **Person** (`scripts/ui_walkthrough.py`): the real app served by
  `scripts/uxtest_data.py serve` with a probe that reads
  `data-uxtest/live-probe.json`, so "idle for 3121 s in the chat app" could
  be set live. 27 screenshots, all read. No console errors. Every UI endpoint
  answered in 1-8 ms (`curl`, 641 spans); switching screens settled in under
  100 ms before the fixed wait the script adds.
- **Agent** (`scripts/agent_walkthrough.py`): spawns `mcp_server.py` over
  stdio against the running app and makes 22 calls as a small model would,
  chaining values between calls and provoking the usual mistakes. The tool
  catalogue costs about 1,900 tokens; no result contained anything but text.

## Findings, most harmful first

Severity: **blocker** (wrong or leaked data, or the scenario cannot be done),
**annoying** (the scenario works but costs time, context or trust),
**cosmetic**.

### Blockers

**B1. A Spanish private window is recorded and searchable.** *(UC5)*
Chrome's private tab in Spanish is `Nueva pestaña de incógnito - Google
Chrome`; the default exclusion rule is the case- and accent-sensitive regex
`Incognito`, so the window was stored as an active span on every working day
(10 spans) and indexed: Search for `incógnito` lists all ten. Every default title rule
is case-sensitive, so any other capitalisation escapes it in the same way.
*Fix:* case-insensitive, accent-tolerant defaults (`(?i)inc[oó]gnito`,
`(?i)private browsing`, ...), added to existing databases by a migration
(defaults are only seeded into an empty table). README should also state the
boundary: Chrome names the private window only on its new-tab page, so pages
opened in it afterwards are ordinary titles.

**B2. Away time is back-dated across the start of recording.** *(UC7; known
issue from the user's PC)* The kind is right: with the real collector and
the chat app in the foreground at 3121 s idle, the first span is `away`, never
active, and turns into an active span only when input resumes (checked live
with `activity_now` and the database). But the away span starts where input
stopped, 52 minutes *before the app's first sample*, with nothing bounding
it:
- restarting the app during the same idle period produced a second away span
  over the same 21:10-22:02 interval (two identical rows), so today's away
  total grew by 52 minutes;
- after a pause, the first idle sample back-dates the away span into the
  pause and over the span the pause closed (reproduced in `SpanBuilder`: the
  span closed at 1600 and the next away span starts at 1500).
*Fix:* `SpanBuilder` gets a floor below which a span may never start: the
moment recording (re)started, raised to the end of the last persisted span;
`Collector` passes `MAX(end_ts)` at startup and the interrupt time after a
pause or exclusion. Regression tests: a first sample with 3121 s idle opens
an away span at the floor and no active span; a restart during idle does not
overlap; an away span after a pause starts at the end of the pause.

**B3. The project list drowns in repo names he never works on.** *(UC6, UC3;
known issue)* Every repo name found under the root becomes a project name
matched as a word in *every* window title. With the 38-repo folder:
`Senior Python Engineer (Remote, EU) | LinkedIn` became project `python`
(1 h 50 min in 30 days), `Staff Engineer, Developer Tools` became `tools`,
docs pages became `fastapi`, `react` and `llama.cpp`: five of the eleven rows
in Projects are clones he never committed to, and the job search is counted as
Python work in Today, Week, `activity_summary` and `activity_projects`. Old
own repos with generic names (`notes`, `home`, `api`, `test`) are one title
away from the same fate.
*Fix:* a repo name counts as a project only when it has commits by the
configured authors (or the repo's own identity) in the last 90 days; the
editor-title detectors keep working for everything else. Existing history is
corrected by "Reapply to history". Regression test: a root with an active
repo, a clone by another author and an old repo; only the active repo is
matched in a browser title.

### Annoying

**A1. The commits scan does far more work than it needs.** *(UC6; known
issue)* Each poll starts 3 git processes per repo (`git config` twice,
`git log --all`), 114 for this folder every 10 minutes, even when nothing
changed. The first poll writes each commit with two SQLite commits: 6-10 s
here for 1,051 commits (5.1 s of 6.1 s in `sqlite3.Connection.commit` under a
profiler), and slower still on Windows disks. It imports each repo's whole
history and the 180-day retention, which runs right after it at startup,
deletes most of it (1,051 imported, 126 left). `last_scan_ts` is per root,
so a repo whose `git log` exceeds the 15 s timeout is skipped silently and
its history is never retried. Recording is not blocked (separate thread),
but the recent-files poller waits behind it.
*Fix:* one transaction per repo; `--since` no older than the retention
window; cache the identity per repo per poll; skip a repo whose refs have not
changed since its last scan (mtime of `.git/HEAD`, `refs/`, `packed-refs`,
`logs/HEAD`); per-repo scan time. Test: a second poll of an unchanged folder
starts no `git log`.

**A2. Adding a repo folder gives no feedback.** *(UC6)* A path that does not
exist is accepted silently and listed like a real one; nothing says how many
repos were found or when they were last scanned, and no commit appears until
the next 10-minute poll.
*Fix:* reject a path that is not a folder (`400 bad_path`), run a scan in the
background right after adding, and show "N repos, last scanned HH:MM" per
root.

**A3. Meetings and videos count as away.** *(UC3, UC5)* A 15-minute Teams
standup, a 45-minute Zoom interview and 35-50-minute fan films with no mouse
or keyboard input all become away after 2 minutes: the Meetings category
shows 0 min for the whole week, although the interview is exactly what the
job-search review needs.
*Fix:* a longer away threshold (e.g. 30 min) while the foreground span's
category is Meetings or Media, configurable in Settings; the collector
already classifies spans, so this is a lookup, not a new probe.

**A4. "Where was I?" answers with the music player.** *(UC1, UC2)*
`activity_where_was_i(before="ayer", contexts=3)` returned Spotify, Obsidian
and WhatsApp; the last work context (`daguerres-hoard`, 16:25-17:04) was not in
the three. The model then searched for `Spotify.exe` and found nothing. In
the UI there is no "where was I" at all: the endpoint exists
(`/api/where-was-i`) but no screen uses it, so UC1 means clicking timeline
segments one by one.
*Fix:* rank contexts with a project first and skip Media/Communication/Games
by default (a parameter keeps the old behaviour); a "Where was I?" card on
Today with the last contexts, their files and commits.

**A5. Everyday time words are refused.** *(UC4, UC2)* `since="el martes"`,
`since="last tuesday"` and `before="antes de comer"` are `bad_time`, although
the tool keywords advertise "before lunch / antes de comer". The error message
is good (it lists what is accepted), so the model recovers, at the cost of a
round trip and date arithmetic it may get wrong.
*Fix:* weekday names in both languages (`martes`, `el martes`, `last
tuesday` = the most recent past one, as a whole day), `this morning` / `esta
mañana`, `HH:MM` as today at that time; drop "before lunch" from the
keywords.

**A6. Timeline results are heavy for a small context.** *(UC4)* Whole
yesterday with the default limit is 14 KB (~3,500 tokens) for 40 items; a
week at `limit=100` is 35 KB (~8,900 tokens). Each item repeats `start`,
`end`, `duration_s` and a `human` string that already says all three.
*Fix:* a default `limit` of 20 and, for ranges longer than a day, a
`min_minutes` default of 5; say in the description that `activity_summary`
is the right call for a week. Keep every field available.

**A7. Totals per project are seconds only.** *(UC3)* `activity_summary`
returns `active_human` but `by_project`/`by_category`/`by_app` are bare
seconds, while the server instructions tell the model to answer from
`*_human` strings; the weekly note needs five conversions.
*Fix:* add `by_*_human` maps next to the seconds.

**A8. Search hits cannot be traced to their surroundings.** *(UC4)* In the
UI a hit is plain text: no click to open that day's timeline at that moment,
and no URL routing (reload returns to Today, Back leaves the app). Over MCP no
tool accepts a span id or a hit's `ref_id`, so "what was I doing around it"
needs ISO arithmetic by the model (the walkthrough did it for a +-15 min
window). Span hits also carry no duration, so the job search and the novel
can be found but not measured.
*Fix:* clicking a hit opens Today on that date with the segment pinned;
hash-based routing; `activity_timeline(around="<iso>")` returning +-30 min;
`duration_s` on span hits. The skill should mention that a title rule
(`LinkedIn|InfoJobs` -> project "Job search") makes such time measurable.

**A9. "Delete the half hour I forgot" is fiddly.** *(UC5)* Delete range
takes two datetime pickers and nothing else.
*Fix:* presets (last 15 min, last hour, today) that fill the pickers, with the
confirmation that already exists.

**A10. The export is not analysis-ready.** *(UC8)* JSON with epoch seconds
only, three nested arrays, always everything (the API accepts `start`/`end`,
the UI does not offer it), and it includes the zero-length and overlapping
spans of C1/B2.
*Fix:* a CSV export of spans (local date, start, end, duration, kind,
category, project, app; no title for redacted rows) and a range selector.

### Cosmetic

- **C1.** 50 zero-length active spans (the stub left when an away span is
  back-dated to the start of the current span) show up as `Meetings 0m`,
  `Discord.exe 0m`, `unknown 0m` rows in the totals and 26 of them are in the
  search index. *Fix:* hide rows under 30 s in the lists; do not persist
  zero-length spans.
- **C2.** Away time is drawn as a pale gap that looks exactly like "not
  recording"; the legend has no Away/Locked entry.
- **C3.** The page header says "Today" while showing another day.
- **C4.** In Spanish, the reason no model is available stays in English (it
  comes verbatim from the shared model backend).
- **C5.** `Faustus.exe` and `Microsoft.Photos.exe` fall into Other; no default
  rule for either.
- **C6.** Timeline segments are not keyboard-focusable; the sidebar and every
  button are, with a visible focus ring.
- **C7.** The MCP adapter prints "Processing request of type ..." on stderr
  for every call.
- **C8.** Redaction defaults catch `login` but not `Iniciar sesión`; rules are
  shown as raw regexes.
- **C9.** `activity_projects` has no human string for `last_touched`.

## What worked

- No console errors on any screen, in either language, at either size.
- Errors are actionable: every `bad_*` names the valid values, `limit` and
  `minutes` errors name the bound, `empty_query` gives an example.
- No tool result contains an image or any non-text content; the only write
  (`activity_pause`) cannot resume (`minutes=0` is refused with the bound).
- KeePassXC never produced a row; the bank page was stored as `[redacted]`
  and is not searchable.
- Search is instant and finds the FTS5 page on every day it was open,
  grouped by day; the any-word fallback made `LinkedIn InfoJobs` useful.
- Pause shows "Paused until 23:04" in the status card and the sidebar at
  once.

## Not tested

- The Win32 probe itself (foreground window, `GetLastInputInfo`, lock
  detection): only the user's live report and the faked tests cover it.
- Windows Recent Items polling: file events were inserted directly.
- Git process cost on Windows (it was measured on Linux at ~4 ms per spawn;
  Windows is typically an order of magnitude slower).
- "Write my day" with a real model (none was reachable), and Faustus's own
  notes tool in UC3 (the note was composed, not saved).
- The PowerShell launchers and the dark theme.
