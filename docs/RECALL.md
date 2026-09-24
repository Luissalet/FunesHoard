# Recall: Funes as the single timeline of the day

Today the assistant has to ask several apps separately to answer "what was
I doing at 16:00?": Funes (episodic memory of the PC: windows, files,
git...), [Argus's Hoard](../../ArgusHoard) (screen memory: OCR of frames,
grouped in "moments"), [Echo's Hoard](../../EchoHoard) (clipboard history)
and [Scribe's Hoard](../../ScribeHoard) (audio transcripts). `recall` and
`recall_search` (`funes_hoard/recall.py`) ask all of them in parallel and
merge the answers into one time-sorted, citable timeline, so a single tool
call answers the question.

## How it works

1. `funes_hoard/sources.py` holds a small registry of federated sources
   (`argus`, `echo`, `scribe`), each `{id, name, base_url, token_path,
   enabled}`. Defaults point at the well-known sibling folders and ports
   (`Argus's Hoard` on 5183, `Echo's Hoard` on 5188, `Scribe's Hoard` on
   5185, resolved next to this repo); a sibling's `data/url` file overrides
   the port if it was moved, and the registry itself can be edited from
   **Settings -> Sources** or `PUT /api/sources/<id>`, persisted to
   `data/sources.json`. `FUNES_SOURCES` (a JSON list of `{id, ...}`
   patches) overrides both, for tests or an unusual deployment.
2. `recall(at, window_minutes)` resolves `at` with Funes's own
   [timeparse.py](../funes_hoard/timeparse.py) (the same words as every
   other tool: `now`/`ahora`, `"a las 16:00"`, `"hace 10 minutos"`, `"ayer
   por la tarde"`, a weekday name, or an ISO date/datetime), builds a window
   `at +/- window_minutes`, and fans out to every enabled source in
   parallel with a 3 s per-source timeout (`funes_hoard/sources.py`).
3. Each source is asked with its own tools over the family contract
   (`POST /api/agent/call`, Bearer token from `<app>/data/mcp-token`):
   Argus's `screen_timeline`, Echo's `clip_recent` (filtered to the window,
   since Echo has no window-bounded "recent" tool), and Scribe's
   `scribe_sessions` + `scribe_transcript` for the sessions that overlap the
   window. `recall_search(query)` uses each source's own search tool
   instead (`screen_search`, `clip_search`, `scribe_search`).
4. A source that is disabled, unreachable, times out or rejects the token
   never fails the call: it is reported in `summary.unavailable` with a
   short machine-readable reason (`disabled`, `unreachable`, `timeout`,
   `unauthorized`, `http_5xx`, ...) and the rest of the timeline still comes
   back. Funes's own episodes always come back, since they need no network
   call at all.
5. Every item is normalised to `{time, source, kind, text, citation, ref}`
   and the merged list is sorted newest first.

## The citation format

`citation` is a short bracket tag the assistant can quote verbatim so the
user can trace an answer back to where it came from:

| Source | Format | Example |
| --- | --- | --- |
| Funes (own episodes) | `[funes:episode <span id> <HH:MM>]` | `[funes:episode 4131 16:00]` |
| Funes (search hits) | `[funes:<span\|file\|commit> <id> <HH:MM>]` | `[funes:span 26 10:30]` |
| Argus (screen) | `[argus:moment <frame id> <HH:MM>]` | `[argus:moment 88 16:02]` |
| Echo (clipboard) | `[echo:clip <clip id>]` | `[echo:clip 512]` |
| Scribe (audio) | `[scribe:seg <segment id/index> <HH:MM>]` | `[scribe:seg 17 16:04]` |

`ref` carries the payload needed to open the item with that source's own
tools for more detail -- e.g. an Argus frame id for `screen_frame_text`, a
Scribe `session_id` and `start_s` for `scribe_transcript`. `recall` never
invents a citation for an item it did not itself return: if a source is
unavailable, its items are simply absent, not guessed.

## Adding a source

A new federated source needs, in `funes_hoard/sources.py`:

1. An entry in `_DEFAULTS` (id, sibling folder name, default port), or the
   same fields supplied at runtime via `data/sources.json` / the
   `FUNES_SOURCES` environment variable -- no code change is required just
   to point Funes at a source running somewhere else.
2. A window fetcher in `_WINDOW_FETCHERS` (`funes_hoard/recall.py`) that
   calls one of that app's own agent tools with a time range and maps its
   result into `{time, source, kind, text, citation, ref}` items.
3. A search fetcher in `_SEARCH_FETCHERS`, the same shape but calling that
   app's search tool with `query`.

Both fetchers receive a shared `httpx.AsyncClient` (for a 3 s timeout and,
in tests, an `httpx.MockTransport`) and should let `SourceCallError`
propagate for any failure `sources.call_source_tool` did not already turn
into one -- `recall`/`recall_search` catch it and report the source as
unavailable rather than failing the whole call.

## What Funes never does

Funes calls only each sibling app's own read-only `/api/agent/call`
surface, over loopback, with that app's own token -- it never opens
another app's SQLite file, screenshots or audio directly. A source with no
readable token (`data/mcp-token` missing or unreadable) is reported as
`no_token`/unavailable, the same as one that is not running.
