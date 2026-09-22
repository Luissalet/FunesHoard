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

## Fix pass: what changed and why

Every commit below adds regression tests and keeps pytest, `npm run build`,
the MCP protocol test and the manifest test green (test count 205 -> 251).
Git identity, trailers and message rules follow CONTRACT.md §0 as in every
other commit in this repo; each commit is small and self-contained.

| # | Fix | Commit |
|---|---|---|
| B1 | Default exclude/redact regexes are now `(?i)` and accent-tolerant (`inc[oó]gnito`, `iniciar sesi[oó]n`); a version-gated migration in `db.py` upgrades an existing database's still-unmodified defaults, never a user's own edits. | `cea6a59` |
| B2 | `SpanBuilder` gets a `raise_floor()` a span may never start before; `interrupt()` raises it itself after a pause/exclusion, and `Collector` raises it once at startup from `MAX(end_ts)`. Fixes the 52-minute-early away span, the restart duplicate, and the pause-crossing case. | `a3af50d` |
| B3 | A repo only counts as a project (matched as a word in every title) while it has a commit by the configured/own author within the last 90 days -- a clone of someone else's project never earns one; `known_repos` (git_watch) and `_known_repo_names` (collector) both apply the same cutoff. | `6c56bc9` |
| A1 | A repo whose refs (HEAD/packed-refs/logs/refs) have not changed since its last scan is skipped entirely (a stat, no subprocess); `--since` is capped to the retention window; a timed-out scan leaves its checkpoint alone so it is retried; a repo's commit rows are inserted in one transaction. `Database.query`/`query_one` also stopped committing on a plain read (sqlite3's `with conn:` does that unconditionally). | `8c3f025` |
| A2 | `POST /api/commit-repos` rejects a non-folder with `400 bad_path` and kicks off a background scan immediately; the UI shows "N repos found, last scanned HH:MM" per root. | `8d75166` |
| A5 | Weekday names (English/Spanish, with or without a prefix) resolve to the most recent past occurrence of that day; "this morning"/"esta mañana"; a bare `HH:MM`. Dropped the "before lunch"/"antes de comer" keyword that nothing ever parsed. | `01ab1cd` |
| A7 | `activity_summary`'s `by_category`/`by_app`/`by_project` now have a `by_*_human` sibling map. | `a1868d4` |
| C5 | Faustus.exe (Coding) and Microsoft.Photos.exe (Media) added to the default classify rules, with the same kind of version-gated migration as B1. | `a1868d4` |
| C7 | The mcp SDK's own "Processing request of type ..." INFO log is silenced, the same way httpx's already was. | `a1868d4` |
| C1 | A closed span with zero duration (the active-span-swallowed-by-backdate stub) is deleted/never inserted instead of showing up as a "0m" row and, if active, in the search index. | `12b72c6` |
| A3 | The collector looks up the foreground span's category before deciding away: Meetings/Media get a separate, longer idle threshold (default 30 min, configurable in Settings and via `GET`/`PUT /api/settings/meetings-away`) instead of the ordinary 2 minutes. | `0909131` |
| A4 | `where_was_i` skips Media/Communication/Games by default and ranks a context with a known project ahead of a bare app name; `all_categories=true` on `activity_where_was_i` restores the old behaviour. | `7e84129` |
| A6 | `activity_timeline`'s default `limit` is 20 (was 40); leaving `min_minutes` unset now picks 2 min for a day or less and 5 min beyond that, instead of always 2. | `eb958ca` |
| C9 | `activity_projects` items now carry `last_touched_human` next to the epoch value. | `eb958ca` |
| C8 | Folded into B1: `iniciar sesi[oó]n` added as a default redact rule. | `cea6a59` |

### Left for a later pass, and why

*All of these were done in the re-walk; see "Re-walk after the fixes" below.*

- **A8 (search hits cannot be traced to their surroundings)** and **A9
  (delete-range presets)** and **A10 (CSV export + a range picker in the
  UI)** are UI/UX additions (click-through routing, preset buttons, a new
  export format) rather than bug fixes; none is a blocker or a regression,
  and each is sized for its own pass with its own screenshots.
- **A4's UI half** -- a "Where was I?" card on Today -- is not built; the
  ranking/filtering fix (this pass) is what a UI card would call, so adding
  the card is now a small, isolated follow-up.
- **C2** (away drawn like "not recording"), **C3** (header says "Today" on
  another day), **C4** (no-model reason stays in English in Spanish), **C6**
  (timeline segments not keyboard-focusable) are small, purely visual
  changes best done together with a screenshot re-walk rather than blind.
- This pass did not re-run the Playwright/agent walkthrough (`scripts/
  ui_walkthrough.py`, `scripts/agent_walkthrough.py`): the shared cloud
  environment for this task (2 CPUs, other agents on sibling apps) asked
  for pytest, `npm run build`, the MCP protocol test and the manifest test
  as the bar to keep green, not a full browser re-walk. Every fix above has
  its own unit/integration regression test instead. A screenshot re-walk
  after the deferred UI items land would be the natural next step.

## Re-walk after the fixes

Every use case was walked again, on data regenerated with the fixed
collector (38 repos, 14 days, 90,329 samples -> 572 spans; zero overlapping
and zero zero-length spans), as a person (`scripts/ui_walkthrough.py`, 1280x800
English and 1920x1080 Spanish, every screenshot read) and as an agent
(`scripts/agent_walkthrough.py`, 22 calls over real MCP stdio). Both scripts
now assert each use case's "done when" and exit non-zero when one fails; the
final runs pass every check with no console errors and no result carrying an
image. Tests: 251 -> 270 (every fix below has its regression test; the
interface changes are checked by the walkthrough's assertions), plus
`npm ci && npm run build`, the MCP protocol test and the manifest test.

### What the re-walk still found, and what was done

| Use case | Found | Fix | Commit |
|---|---|---|---|
| UC3 | The first A3 fix did not hold on real data: the 45-minute Zoom interview was still 45 minutes away and every fan film was away. Once past the threshold, the away span was back-dated to the last input (the start of the call), and films in a browser tab were Browsing with the 2-minute threshold. | Meetings/Media away starts when the threshold is crossed, not at the last input; default 60 min; a streaming-site title rule (YouTube, Netflix, Twitch...) placed before the browser rules, by an ordered migration. Both interviews are now 45 min of Meetings, the films 9.2 h of Media. | `487166a` |
| UC4 (agent) | Chaining a hit into "what was around it" needed ISO arithmetic by the model. | `activity_timeline(around=<hit ts>)`, +-30 min, every span. | `8968650` |
| UC3 (agent) | The job search and the novel could be counted, not measured. | Window hits carry `duration_s`/`human`; the result totals them in `windows_open_human` (job boards 38 min, novel 1 h 36 min this week). | `8968650` |
| UC1/UC2 | "Where was I" named the project but its title was the final terminal (`pwsh - daguerres-hoard`); the file was nowhere, and unrelated files came first. | `recent_titles` (the editor title is the second one); files inside the project's folder first; `app` matches the last title. | `8968650`, `ef2d9a3` |
| UC1 (person) | No "where was I" in the interface; header said "Today" on Monday. | A "Where was I?" card on Today (before now, or before the shown day ended) with "Show in timeline"; header says Yesterday/Day. | `db73474` |
| UC4 (person) | A8: a hit was plain text; reload lost the day, Back left the app. | Hash routes for view, day and moment; a hit opens its day with the segment pinned; reload and Back work (checked in the walkthrough). | `db73474` |
| UC5 | A9: delete range was two bare pickers. | Last 15 min / 30 min / hour / today shortcuts; the confirmation is unchanged. | `db73474` |
| UC8 | A10: JSON with epoch seconds only. | `GET /api/privacy/export.csv` and a From/To day range in the UI; grouping the CSV by `date` and `project` gives hours per day with stdlib `csv` alone. | `ef2d9a3`, `db73474` |
| all | A browser reporting `en-US@posix` (headless Chromium on this box) made every date format throw and blanked the whole app. | Such a locale falls back to the default; the walkthrough opens the app once with the browser's own locale. | `db73474` |
| cosmetic | C2, C3, C4, C6; "0m" for 12-second blips. | Away/locked drawn hatched with legend entries; header; English backend reason behind "Technical details"; focusable segments (Enter pins); "<1m". | `db73474` |

### Verdict per use case

- **UC1 Monday "where was I?"** -- works. The card shows the last three work
  contexts (project, the editor title naming the file, files, commits) on
  opening; "Show in timeline" pins the moment.
- **UC2 "¿dónde lo dejé ayer?"** -- works. One call, ~480 tokens, project
  first, music and chat skipped, the file in `recent_titles`.
- **UC3 weekly review** -- works with a caveat: hours per project, the job
  search and the novel all come as human strings, meetings are counted; the
  note itself is written by Faustus's notes tool, which was not available
  here (composed, not saved).
- **UC4 "that FTS5 page"** -- works, in the UI (click -> pinned moment) and
  over MCP (`around`).
- **UC5 privacy** -- works: pause shown with its end time, "incógnito" and
  the bank page find nothing in the UI or over MCP, delete shortcuts.
- **UC6 dozens of repos** -- works on Linux: 38 repos, first scan 0.5 s,
  bad folder refused, "38 repos found, last scanned ...", Projects shows the
  six own projects only. Git process cost on Windows is still unmeasured.
- **UC7 back after 50 minutes** -- works: restarted with 3,100 s idle, the
  away span starts at the end of the last recorded span (no overlap, no
  52-minute back-dating), `activity_now` says away, and the first input
  opens an active span.
- **UC8 hours for a chart** -- works: CSV export with local dates, times,
  minutes and projects; redacted titles stay `[redacted]`, excluded windows
  are absent.

### Still open

- The Win32 probe, Windows Recent Items and git cost on Windows remain
  untested from this environment (see "Not tested").
- The Models panel in Settings shows the backend's diagnostic reason in
  English in both languages (it is the diagnostic; the sentence above it is
  translated).
- In the interface, "Where was I?" only looks three days back (the same
  window the tool uses); after a longer absence it says there is nothing to
  pick up.
- A title rule still decides streaming vs. browsing: a video on a site the
  rule does not know stays Browsing with the ordinary threshold.
