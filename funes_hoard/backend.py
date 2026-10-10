"""App-specific wrapper around the vendored `hoard_link` package.

Funes's Hoard uses exactly one model capability -- `llm`, for "Write my
day" -- so this module stays small: it turns `data/backend.json` plus the
environment into a `LinkConfig` for this app, persists UI-made changes to
that file, and builds the prompt for the narrative feature. `api.py` stays
about HTTP routing; this is where the Hoard Link specifics live so the
vendored package (`funes_hoard/hoard_link/`) never has to be edited.
"""
from __future__ import annotations

import json
import os
import asyncio
import time
import math
from urllib.parse import urlencode
from pathlib import Path
from typing import Any, Mapping, Optional

from funes_hoard.hoard_link import LinkConfig

APP_ID = "funes"
BACKEND_FILE = "backend.json"


async def agent_activity_inputs(start: float, end: float, *, fetch=None) -> dict[str, Any]:
    """Read the Hub's public metadata with a bounded, read-only fan-out.

    No model resolution or Hub autostart. The event cursor advances through
    all Faustus events, not a post-filtered subset that could omit receipts.
    ``fetch`` is the normal HTTP seam for offline transport tests.
    """
    from funes_hoard.hoard_link import _hubclient

    base = _hubclient.hub_url()
    read = fetch or _hubclient.fetch_detailed

    async def request(path: str, params: dict, timeout: float = 2.0):
        url = base + path + "?" + urlencode(params)
        try:
            code, body, why = await asyncio.to_thread(read, url, method="GET", timeout=timeout)
            return body if code == 200 and isinstance(body, dict) else None
        except Exception:
            return None

    async def closed_tasks():
        body = await request('/api/agora/tasks', {'status': 'done', 'limit': 500})
        rows = body.get('tasks') if body and body.get('ok') else None
        if not isinstance(rows, list):
            return [], {'ok': False, 'reason': 'unavailable', 'truncated': False}
        return rows[:500], {'ok': True, 'truncated': len(rows) >= 500, 'reason': 'limit' if len(rows) >= 500 else ''}

    async def turn_events():
        rows, cursor, truncated = [], 0, False
        deadline = time.monotonic() + 8.0
        for _ in range(3):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                truncated = True
                break
            body = await request('/api/events', {'source': 'faustus', 'since': start, 'until': end,
                                               'order': 'asc', 'since_id': cursor, 'limit': 2000}, min(2.0, remaining))
            page = body.get('events') if body and body.get('ok') else None
            if not isinstance(page, list):
                return rows, {'ok': False, 'reason': 'unavailable', 'truncated': bool(rows)}
            page = page[:2000]
            rows.extend(page)
            if len(page) < 2000:
                break
            try:
                following = max(int(row['id']) for row in page if isinstance(row, dict))
            except (KeyError, ValueError, TypeError):
                following = cursor
            if following <= cursor:
                truncated = True
                break
            cursor = following
        else:
            truncated = True
        stats = await request('/api/events/stats', {}, min(2.0, max(.05, deadline - time.monotonic())))
        first = stats.get('first_ts') if stats else None
        valid_first = isinstance(first, (int, float)) and not isinstance(first, bool) and math.isfinite(first)
        coverage_known = valid_first or bool(stats is not None and stats.get('total') == 0 and first is None)
        if valid_first and first > start:
            truncated = True
        return rows, {'ok': True, 'reason': 'coverage_unknown' if not coverage_known else 'retained_window' if valid_first and first > start else 'limit' if truncated else '',
                      'truncated': truncated or not coverage_known, 'since': first if valid_first else None}

    tasks, events = await asyncio.gather(closed_tasks(), turn_events())
    return {'tasks': tasks[0], 'events': events[0], 'sources': {'agora': tasks[1], 'bus': events[1]}}

def mark_read_only_agent_tools(app, names):
    """Extend the vendored catalogue's older read-only name list locally.

    Keep its route, schemas, authentication and dispatcher; only annotate the
    explicitly supplied product tools in the generated catalogue.
    """
    names = frozenset(names)
    for route in app.routes:
        if getattr(route, 'path', None) != '/api/agent/tools':
            continue
        original = route.dependant.call

        async def annotated(*args, **kwargs):
            result = await original(*args, **kwargs)
            return {**result, 'tools': [
                {**tool, 'annotations': {**tool.get('annotations', {}), 'readOnlyHint': True}}
                if tool.get('name') in names else tool for tool in result['tools']]}

        route.endpoint = route.dependant.call = annotated
        return


# The answer itself is 2-4 sentences, but a reasoning model counts its
# hidden thinking against the same budget (Hoard Link strips it from the
# text); a tight cap would leave nothing but reasoning and an empty answer.
NARRATIVE_MAX_TOKENS = 1024

NARRATIVE_SYSTEM = (
    "You turn a compact summary of one day's computer activity into a short "
    "narrative for the person who lived it: second person, neutral and "
    "factual, 2-4 sentences (e.g. \"You spent the morning on...\"). Use only "
    "the categories, app names, project names, durations and focus blocks "
    "given in the data. Never invent an app, file, title or task that is "
    "not in it, and never mention seconds -- use the human-readable totals. "
    "No greeting, no sign-off, no markdown."
)


def backend_json_path(data_dir: Path) -> Path:
    return Path(data_dir) / BACKEND_FILE


def read_backend_json(data_dir: Path) -> dict[str, Any]:
    """The raw `backend.json` contents, or `{}` if absent or unreadable."""
    path = backend_json_path(data_dir)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except ValueError:
        return {}
    return raw if isinstance(raw, dict) else {}


def write_backend_json(data_dir: Path, raw: dict[str, Any]) -> None:
    path = backend_json_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_link_config(data_dir: Path, env: Optional[Mapping[str, str]] = None) -> LinkConfig:
    return LinkConfig.load(backend_json_path(data_dir), env=env if env is not None else os.environ, app=APP_ID)


def _set_or_clear(section: dict[str, Any], key: str, value: Optional[str]) -> None:
    if value is None:
        return
    if value == "":
        section.pop(key, None)
    else:
        section[key] = value


def apply_config_patch(data_dir: Path, patch: Mapping[str, Any]) -> dict[str, Any]:
    """Merge a UI-submitted config patch into `backend.json` and save it.

    Every key is optional and only touches what it names; an empty string
    clears that key (e.g. clearing a saved Faustus token). Returns the raw
    dict that was written, so a caller can report what is set without ever
    handing the token itself back to the UI.
    """
    raw = read_backend_json(data_dir)

    if patch.get("only_resident") is not None:
        raw["only_resident"] = bool(patch["only_resident"])

    faustus_url = patch.get("faustus_url")
    faustus_token = patch.get("faustus_token")
    if faustus_url is not None or faustus_token is not None:
        faustus = dict(raw.get("faustus") or {})
        _set_or_clear(faustus, "url", faustus_url)
        _set_or_clear(faustus, "token", faustus_token)
        if faustus:
            raw["faustus"] = faustus
        else:
            raw.pop("faustus", None)

    caps_patch = patch.get("capabilities") or {}
    if caps_patch:
        caps = dict(raw.get("capabilities") or {})
        for cap, cfg in caps_patch.items():
            entry = dict(caps.get(cap) or {})
            _set_or_clear(entry, "url", cfg.get("url"))
            _set_or_clear(entry, "model", cfg.get("model"))
            if entry:
                caps[cap] = entry
            else:
                caps.pop(cap, None)
        if caps:
            raw["capabilities"] = caps
        else:
            raw.pop("capabilities", None)

    write_backend_json(data_dir, raw)
    return raw


def build_narrative_messages(summary: Mapping[str, Any]) -> list[dict[str, str]]:
    """Chat messages for the "Write my day" feature.

    `summary` must be the same shape `activity_summary` (agent-view) hands
    the model: categories/apps/projects and their totals, focus blocks and
    switch counts -- never raw window titles, which the spec keeps out of
    this feature on purpose.
    """
    return [
        {"role": "system", "content": NARRATIVE_SYSTEM},
        {"role": "user", "content": json.dumps(summary, ensure_ascii=False)},
    ]
