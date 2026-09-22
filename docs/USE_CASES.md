# Use cases

Concrete scenarios Funes's Hoard has to serve. Each one names who is using
it, the goal, the starting state, the exact steps (in the interface, or the
words a person says to Faustus and the tool calls a local model should make)
and what "done" looks like. They are walked end to end, as a person in the
browser and as an agent over MCP, in [USABILITY_REPORT.md](USABILITY_REPORT.md);
`scripts/uxtest_data.py` builds the realistic data they are walked on and
`scripts/agent_walkthrough.py` replays the agent ones over real MCP stdio.

The persona is always the same fictional person, Alex: a senior engineer
on Windows who builds local AI tools (Faustus and its plugin apps) in a
folder of dozens of independent repos, writes a novel in Word and Obsidian,
keeps photos on disk, is looking for a new job, and watches fan
productions in the evening.

---

## UC1 - Monday morning: "where was I?"

- **Who:** Alex, back at the desk after a weekend.
- **Goal:** pick up exactly where Friday's coding stopped, without scrolling
  through git logs or editor history.
- **Starting state:** the app has been recording for two weeks; Friday ended
  with a VS Code session on `funes-hoard`, a terminal and a docs page, then a
  locked screen.
- **Steps (UI):**
  1. Open `http://127.0.0.1:8813`. The Today view is empty-ish (the day just
     started), but its **Where was I?** card lists the last three work
     contexts before now -- Friday's -- with project, last titles, files and
     commits.
  2. Click **Show in timeline** on the first one: Friday opens with that
     moment pinned (or switch the day by hand and read the timeline end).
  3. Open **Files & commits** to see the last files opened and the last
     commit subject.
- **Done when:** in under a minute Alex can say "I was in
  `collector.py` in funes-hoard, last commit was X, the tab I had open was Y",
  and none of that came from memory.

## UC2 - Agent: "Faustus, ¿dónde lo dejé ayer?"

- **Who:** Alex, talking to Faustus with a 27B local model and a small
  context window.
- **Goal:** a two-sentence resume context for yesterday's last work, in
  Spanish, with the file Alex was in.
- **Starting state:** Faustus has Funes's Hoard connected as a nearby app.
- **Prompt:** "Faustus, ¿dónde lo dejé ayer? Dime el proyecto, el archivo y
  lo último que hice."
- **Expected tool calls:** `activity_where_was_i(before="ayer", contexts=3)`;
  if the answer names a project but no file, `activity_search("<project>",
  since="ayer", until="ayer")` or `activity_recent_files(since="ayer")`.
- **Done when:** the model answers from the `human` strings without doing
  arithmetic, names the right project and window, and never quotes a title
  it was not given. Total tool output fits comfortably in a few thousand
  tokens.

## UC3 - Agent + Faustus notes: weekly review for the job hunt

- **Who:** Alex on Friday afternoon, preparing a weekly note.
- **Goal:** "How much of this week went to Faustus and its apps, how much to
  the job search, and how much to the novel?", then save that as a note with
  Faustus's own file/notes tool.
- **Prompt:** "Faustus, hazme un resumen de esta semana: horas por proyecto,
  cuánto dediqué a buscar trabajo y cuánto a la novela, y guárdalo en mis
  notas como 'Semana 38'."
- **Expected tool calls:** `activity_summary(day="esta semana",
  group_by="all")`, `activity_projects(since="esta semana")`, optionally
  `activity_search("LinkedIn InfoJobs", since="esta semana")` to size the job
  search (its `windows_open_human` is how long those windows were open);
  then Faustus's own note-writing tool with the text it composed.
- **Done when:** the numbers in the note are the `*_human` strings the tools
  returned, projects are real ones (not random repo names or words from
  window titles), and the job search and the novel are identifiable.

## UC4 - "That FTS5 page I had open on Tuesday"

- **Who:** Alex, mid-task, remembering a page but not its URL.
- **Goal:** find when (and in which tab title) that documentation page was
  open, then what else was going on around it.
- **Steps (UI):** **Search** -> type `fts5` (or `sqlite fts`) -> narrow the
  date to last week -> click the hit -> land on that day's timeline at that
  moment.
- **Agent variant:** "Faustus, ¿cuándo estuve mirando lo de FTS5?" ->
  `activity_search("fts5")` -> `activity_timeline(around=<the hit's ts>)`.
- **Done when:** the right hit is first, its day and hour are readable, and
  there is a way to see the surrounding activity without retyping dates.

## UC5 - Privacy before an interview and online banking

- **Who:** Alex before a video interview and a bank transfer.
- **Goal:** nothing about the interview window, the bank page or a private
  browser window is stored; afterwards, delete the half hour Alex forgot to
  pause.
- **Steps (UI):**
  1. **Privacy** -> Pause 1 h. Check the header says it is paused and until
     when.
  2. After the pause, open a private window (Chrome in Spanish: "Nueva
     pestaña de incógnito") and a bank page ("Banco ... - Área de clientes").
  3. **Privacy** -> Delete a time range -> **Last 30 min** (or the exact
     start and end) -> Delete -> confirm, for the forgotten half hour.
- **Agent variant:** "Faustus, no me grabes la próxima media hora" ->
  `activity_pause(minutes=30)` -> "¿me estás grabando?" -> `activity_now()`.
- **Done when:** search for the interview company or the bank finds nothing
  recorded, the private window never produced a row, and the deleted range
  is gone from the timeline, search and totals.

## UC6 - First real setup on Windows: dozens of repos

- **Who:** Alex on a Windows PC, pointing the commits source at
  `C:\Users\me\Desktop\Projects`, which holds dozens of
  repos: active apps, old experiments, and many clones of other people's
  projects that are never worked on.
- **Goal:** commits from the active projects show up quickly; the Projects
  list shows the projects Alex actually spends time on, not every repo name
  that happens to appear in a window title.
- **Steps (UI):** **Files & commits** -> add the root path -> (optionally) set
  the author names -> wait for commits -> open **Projects** for "this week".
- **Done when:** adding the folder gives feedback (it exists, how many repos
  were found, when it was last scanned), the scan takes seconds rather than
  minutes and never blocks recording, and the Projects list contains Alex's
  own projects only.

## UC7 - Coming back after a long time away

- **Who:** Alex starts the app (or the PC wakes up and Faustus starts
  it) after 50 minutes away from the keyboard, with the chat window still in
  the foreground.
- **Goal:** those 50 minutes show as "away", the first active span starts
  when Alex actually touches the keyboard, and nothing overlaps the previous
  session.
- **Steps:** start the app with the system reporting ~3100 s idle -> wait a
  minute -> move the mouse -> open Today.
- **Done when:** the timeline shows an away span (never a 50-minute active
  span on the chat app), day totals do not double-count, and `activity_now`
  says idle/away until input resumes.

## UC8 - Combining with Laplace's Hoard: a month of hours as a table

- **Who:** Alex wants a chart of hours per project per day for a
  portfolio write-up.
- **Goal:** get the raw data out of Funes's Hoard and into the data-analysis
  app.
- **Steps:** **Privacy** -> Export -> (optionally a From/To day range) ->
  **Spans as CSV** -> open the file in Laplace's Hoard -> group by `date` and
  `project`, sum `duration_min`.
- **Done when:** the export is a well-formed file with local-readable times
  and the project/category fields the analysis needs, and it never contains
  titles that were redacted.
