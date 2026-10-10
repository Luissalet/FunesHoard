# Agent diary

The day view has an independent Agents lane. It remains visible when the
person has no recorded desktop activity. It reads recorded Git commits from
Funes, currently closed Agora tasks from the Hub, and explicit Faustus turn
receipts from the Hub event bus. It does not infer working hours, attribute
Git authors to agents, or treat a task closure as independent success proof.

`GET /api/agents/day` returns epoch timestamps for the UI. The audited
`POST /api/agent/activity_agents_day` and same-named MCP tool return local
ISO timestamps through the normal agent projection. Arguments are `day`
(default today), `kind` (`turn`, `task`, `commit`, or omitted), `limit`
(1–100, default 50), and `offset` (0–10000). Read subsequent pages with
`next_offset`; inspect `has_more`, `truncated` and each entry in `sources`.

The Hub is discovered through the existing configuration and read without
starting it. No model is required and no sibling database is opened. Source
failures preserve the other records and are visible in the UI and API.
The current Agora API returns at most 500 currently closed tasks and has no
pagination: reopened tasks are absent. The bus is retained, not an archive.
Its retained start, unknown coverage, page cap and request budget are shown
as partial coverage. Local commits are bounded at 5000 per requested range.

Recognised receipts have source `faustus`, type `faustus.turn.finished`,
`faustus.turn.completed`, `faustus.turn.failed` or
`faustus.turn.cancelled`, and selected metadata in `data`: `receipt_id`,
`session_id`, `status`, and optional `repo`. The agreed #268 producer uses
`id` (session plus persisted message identity) and status `ok`, `error` or
`cancelled`; the reader also accepts the `receipt_id` alias. A repeated receipt is shown
once. Its timestamp is the Hub receipt time. Prompt, response and arbitrary
payload fields are omitted. Other bus events, including generic agent
calls, are not evidence of a completed turn.

The reader supports this receipt contract; Faustus production does not yet
publish these receipts (Agora task #268 is in review). That producer is a separate dependency, so an empty
Turns filter is not evidence that Faustus did no work. Integration of the
cross-app contract follows the agreement in Agora discussion #282.
