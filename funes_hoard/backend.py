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
from pathlib import Path
from typing import Any, Mapping, Optional

from funes_hoard.hoard_link import LinkConfig

APP_ID = "funes"
BACKEND_FILE = "backend.json"

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
