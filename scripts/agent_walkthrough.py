#!/usr/bin/env python3
"""Walk the agent use cases (docs/USE_CASES.md) over real MCP stdio.

    python scripts/uxtest_data.py serve --port 18830 &
    .venv/bin/python scripts/agent_walkthrough.py --port 18830

Spawns `funes_hoard/mcp_server.py` as a subprocess, exactly as Faustus
does, and plays the part of a small local model: it starts from
`list_tools`, picks tools by their descriptions, chains values from one
result into the next call, and reacts to errors by reading the message.
Every call is printed with the size of its result (characters and a rough
token estimate at 4 characters per token) so a heavy result stands out, and
any non-text content (an image) in a result is flagged: a text-only model
receiving an image makes a local llama.cpp server fail the whole turn.

It changes one thing in the app: UC5 pauses recording for 30 minutes (the
only write the agent has); the script resumes it at the end through the
human UI API so the data dir is left recording.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO_ROOT = Path(__file__).resolve().parent.parent
ADAPTER = REPO_ROOT / "funes_hoard" / "mcp_server.py"

TOTAL = {"calls": 0, "chars": 0, "errors": 0, "images": 0}
FAILED: list = []


def check(ok: bool, what: str) -> None:
    """A use case's "done when", checked on what the model actually received."""
    print(f"  {'check ok' if ok else 'CHECK FAILED'}: {what}")
    if not ok:
        FAILED.append(what)


def _tok(n: int) -> int:
    return (n + 3) // 4


async def call(session: ClientSession, tool: str, args: dict, why: str):
    """One tool call the way a model makes it; returns parsed JSON or None on error."""
    res = await session.call_tool(tool, args)
    TOTAL["calls"] += 1
    kinds = [c.type for c in res.content]
    text = "".join(getattr(c, "text", "") for c in res.content)
    TOTAL["chars"] += len(text)
    if any(k != "text" for k in kinds):
        TOTAL["images"] += 1
    status = "ERROR" if res.isError else "ok"
    print(f"\n> {tool}({json.dumps(args, ensure_ascii=False)})  # {why}")
    print(f"  {status}; {len(text)} chars (~{_tok(len(text))} tokens); content: {kinds}")
    if res.isError:
        TOTAL["errors"] += 1
        print(f"  message: {text}")
        return None
    try:
        data = json.loads(text)
    except ValueError:
        print(f"  (not JSON) {text[:200]}")
        return None
    preview = json.dumps(data, ensure_ascii=False)
    print(f"  {preview[:600]}{' ...' if len(preview) > 600 else ''}")
    return data


async def walk(port: int) -> None:
    params = StdioServerParameters(
        command=sys.executable, args=[str(ADAPTER)], env={"FUNES_URL": f"http://127.0.0.1:{port}"},
    )
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as session:
            init = await session.initialize()
            instr = init.instructions or ""
            print(f"server instructions: {len(instr)} chars (~{_tok(len(instr))} tokens)")
            tools = (await session.list_tools()).tools
            desc_total = 0
            print(f"list_tools: {len(tools)} tools")
            for t in tools:
                d = t.description or ""
                schema = json.dumps(t.inputSchema)
                desc_total += len(d) + len(schema)
                ro = t.annotations.readOnlyHint if t.annotations else None
                print(f"  {t.name}: description {len(d)} chars, schema {len(schema)} chars, readOnly={ro}")
            print(f"  tool catalogue total ~{_tok(desc_total + len(instr))} tokens of the model's context")

            print("\n=== UC2: 'Faustus, ¿dónde lo dejé ayer? Proyecto, archivo y lo último que hice.'")
            where = await call(session, "activity_where_was_i", {"before": "ayer", "contexts": 3},
                               "keywords 'dónde estaba / donde lo dejé' -> where_was_i")
            if where and where["contexts"]:
                top = where["contexts"][0]
                key = top["project"] or top["app"]
                named = next((t for t in top.get("recent_titles", []) if "Visual Studio Code" in t), top["title"])
                print(f"  model would say: '{key}: {named}' ({top['human']}); files={top['files']}")
                check(top["project"] is not None and top["app"] not in ("Spotify.exe", "WhatsApp.exe"),
                      "the first context is a project, not the music player (A4)")
                check(any("Visual Studio Code" in t for t in top.get("recent_titles", [])),
                      "an editor title (the file) is among recent_titles even if the context ended in a terminal")
                if not top["files"]:
                    await call(session, "activity_recent_files", {"since": "ayer", "limit": 5},
                               "no files in the context -> recent files since yesterday")
                    await call(session, "activity_search", {"query": key, "since": "ayer", "until": "ayer", "limit": 5},
                               "what else about this project yesterday")

            print("\n=== UC3: 'Resumen de esta semana: horas por proyecto, búsqueda de trabajo, novela; guárdalo en notas'")
            summ = await call(session, "activity_summary", {"day": "esta semana", "group_by": "all"},
                              "'resumen semanal' -> summary, all groupings")
            await call(session, "activity_projects", {"since": "esta semana"}, "'horas por proyecto'")
            jobs = await call(session, "activity_search", {"query": "LinkedIn InfoJobs", "since": "esta semana", "limit": 20},
                              "size the job search: search job-board titles")
            novel = await call(session, "activity_search", {"query": "mapa perdido", "since": "esta semana"},
                               "size the novel: search the novel's title")
            if summ:
                note = [f"Semana: activo {summ['active_human']}, ausente {summ['away_human']}"]
                for k, v in list((summ.get("by_project_human") or {}).items())[:8]:
                    note.append(f"- {k}: {v}")
                if jobs:
                    note.append(f"- búsqueda de empleo: {jobs.get('windows_open_human', '?')} en portales de empleo")
                if novel:
                    note.append(f"- novela: {novel.get('windows_open_human', '?')}")
                meetings = (summ.get("by_category_human") or {}).get("Meetings")
                note.append(f"- reuniones: {meetings or '0'}")
                print("  note the model could write with Faustus's notes tool:\n    " + "\n    ".join(note))
                check("by_project_human" in summ, "per-project totals come as human strings (A7)")
                check(bool(jobs and jobs.get("windows_open_human")) and bool(novel and novel.get("windows_open_human")),
                      "the job search and the novel are measurable, not just counted (A8)")
                check(not ({"python", "tools", "docs", "react", "fastapi"} & set(summ.get("by_project") or {})),
                      "no clone of someone else's repo counts as a project (B3)")

            print("\n=== UC4: 'Faustus, ¿cuándo estuve mirando lo de FTS5?' then 'what was I doing around it?'")
            hits = await call(session, "activity_search", {"query": "fts5", "limit": 5}, "'cuándo vi' -> search")
            if hits and hits["items"]:
                ts = hits["items"][0]["ts"]
                around = await call(session, "activity_timeline", {"around": ts},
                                    "chain the hit's ts straight into the timeline, no date arithmetic (A8)")
                check(bool(around and any("FTS5" in (i["title"] or "") for i in around["items"])),
                      "the timeline around the hit contains the hit's own window")
            tue = await call(session, "activity_search", {"query": "fts5", "since": "el martes", "until": "el martes"},
                             "a model saying 'el martes' (Tuesday) as the user did (A5)")
            check(tue is not None, "'el martes' is understood")
            await call(session, "activity_search", {"query": "fts5", "since": "last tuesday", "until": "last tuesday"},
                       "same in English")

            print("\n=== UC5/UC7: 'no me grabes la próxima media hora' / '¿me estás grabando?'")
            await call(session, "activity_now", {}, "'¿me estás grabando?' before")
            await call(session, "activity_pause", {"minutes": 30}, "'no me grabes media hora'")
            now2 = await call(session, "activity_now", {}, "confirm paused")
            check(bool(now2 and now2["paused"] and now2["title"] is None), "while paused nothing is reported as current")
            await call(session, "activity_pause", {"minutes": 0}, "a model trying to 'unpause' with 0")

            for q in ("incógnito", "Banco Ejemplo"):
                r = await call(session, "activity_search", {"query": q}, "UC5: nothing private is searchable")
                check(bool(r is not None and not r["items"]), f"'{q}' finds nothing")

            print("\n=== Error handling as a small model makes mistakes")
            await call(session, "activity_summary", {"day": "esta semana", "group_by": "projects"}, "plural typo")
            await call(session, "activity_timeline", {"start": "ayer", "limit": 500}, "limit over the cap")
            await call(session, "activity_where_was_i", {"before": "antes de comer"}, "a phrase no longer advertised (A5)")
            await call(session, "activity_timeline", {"around": "ayer 12:00", "start": "ayer"}, "around and start together")
            await call(session, "activity_search", {"query": "¿?"}, "no words")
            await call(session, "activity_timeline", {"start": "ayer"}, "whole yesterday, default page")
            tl = await call(session, "activity_timeline", {"start": "esta semana", "limit": 100}, "a week at max limit")
            if tl and tl.get("has_more"):
                await call(session, "activity_timeline", {"start": "esta semana", "limit": 100, "offset": tl["next_offset"]},
                           "follow next_offset")

    # leave the app recording (the agent cannot resume; the human UI can)
    httpx.post(f"http://127.0.0.1:{port}/api/privacy/resume", timeout=5)
    print(f"\nTOTAL: {TOTAL['calls']} calls, {TOTAL['chars']} chars (~{_tok(TOTAL['chars'])} tokens), "
          f"{TOTAL['errors']} errors, {TOTAL['images']} results with non-text content")
    check(TOTAL["images"] == 0, "no tool result carries an image")
    print("failed checks:", FAILED or "none")
    if FAILED:
        sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18830)
    asyncio.run(walk(ap.parse_args().port))


if __name__ == "__main__":
    main()
