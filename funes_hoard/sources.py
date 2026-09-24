"""Registry of federated Hoard sources `recall.py` can pull a timeline from.

A source is one sibling app that exposes the family contract (`GET
/api/health`, `GET /api/agent/tools`, `POST /api/agent/call` with a Bearer
token read from `<app>/data/mcp-token`). Defaults point at the well-known
sibling folders and ports documented in the project brief; they can be
overridden per-field by `data/sources.json` (persisted from `/api/sources`)
and, on top of that, by the `FUNES_SOURCES` environment variable (a JSON
list of `{id, name, base_url, token_path, enabled}` patches) -- handy for
tests and for a deployment that does not use the default folder layout.

Nothing here ever raises for a source that is down: `check_health` and
`call_source_tool` turn a connection failure, a timeout or a 401 into a
`SourceCallError`/`SourceHealth` the caller reports as "unavailable".
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_TIMEOUT = 3.0  # seconds: short per-source budget for a parallel fan-out

# id -> (sibling folder name on the user's machine, default port)
_DEFAULTS: tuple[tuple[str, str, int], ...] = (
    ("argus", "Argus's Hoard", 5183),
    ("echo", "Echo's Hoard", 5188),
    ("scribe", "Scribe's Hoard", 5185),
)


def _sibling_dir(folder_name: str) -> Path:
    return REPO_ROOT.parent / folder_name


def _default_base_url(folder_name: str, port: int) -> str:
    # A sibling app can be moved to another port; it publishes the real one
    # in `data/url` (mirrors how Faustus itself discovers nearby apps).
    url_file = _sibling_dir(folder_name) / "data" / "url"
    try:
        text = url_file.read_text(encoding="utf-8").strip()
        if text:
            return text.rstrip("/")
    except OSError:
        pass
    return f"http://127.0.0.1:{port}"


def _default_token_path(folder_name: str) -> str:
    return str(_sibling_dir(folder_name) / "data" / "mcp-token")


@dataclass
class Source:
    id: str
    name: str
    base_url: str
    token_path: str
    enabled: bool = True

    def read_token(self) -> Optional[str]:
        try:
            text = Path(self.token_path).read_text(encoding="utf-8").strip()
            return text or None
        except OSError:
            return None

    def to_dict(self) -> dict:
        return asdict(self)


def default_sources() -> list[Source]:
    return [
        Source(id=sid, name=folder, base_url=_default_base_url(folder, port), token_path=_default_token_path(folder))
        for sid, folder, port in _DEFAULTS
    ]


def _patch_source(source: Source, patch: dict) -> Source:
    data = asdict(source)
    for key in ("name", "base_url", "token_path", "enabled"):
        if key in patch and patch[key] is not None:
            data[key] = patch[key]
    return Source(**data)


def _new_from_patch(source_id: str, patch: dict) -> Optional[Source]:
    if not patch.get("base_url"):
        return None
    return Source(
        id=source_id,
        name=patch.get("name", source_id),
        base_url=str(patch["base_url"]).rstrip("/"),
        token_path=patch.get("token_path", ""),
        enabled=bool(patch.get("enabled", True)),
    )


def _keyed_by_id(raw) -> dict[str, dict]:
    items = raw if isinstance(raw, list) else (raw or {}).get("sources", [])
    out: dict[str, dict] = {}
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict) and item.get("id"):
                out[str(item["id"])] = item
    return out


class SourceRegistry:
    """Resolves the effective source list: defaults, then `data/sources.json`
    (persisted edits from the UI), then `FUNES_SOURCES` (env override)."""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self._path = self.data_dir / "sources.json"

    def _stored(self) -> dict[str, dict]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return _keyed_by_id(raw)

    def _env_overrides(self) -> dict[str, dict]:
        raw = os.environ.get("FUNES_SOURCES")
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
        return _keyed_by_id(data)

    def list(self) -> list[Source]:
        sources: dict[str, Source] = {s.id: s for s in default_sources()}
        for layer in (self._stored(), self._env_overrides()):
            for sid, patch in layer.items():
                if sid in sources:
                    sources[sid] = _patch_source(sources[sid], patch)
                else:
                    created = _new_from_patch(sid, patch)
                    if created is not None:
                        sources[sid] = created
        return list(sources.values())

    def get(self, source_id: str) -> Optional[Source]:
        for source in self.list():
            if source.id == source_id:
                return source
        return None

    def save_patch(self, source_id: str, patch: dict) -> Source:
        """Persist a partial edit (base_url/enabled/name/token_path) for one
        source, defaulting/creating from `default_sources()` when the id is
        not one of the built-ins."""
        stored = self._stored()
        existing = stored.get(source_id, {"id": source_id})
        merged = {**existing, **{k: v for k, v in patch.items() if v is not None}}
        merged["id"] = source_id
        stored[source_id] = merged
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps({"sources": list(stored.values())}, indent=2), encoding="utf-8")
        result = self.get(source_id)
        if result is None:  # pragma: no cover - defensive, save_patch always creates a resolvable entry
            raise KeyError(source_id)
        return result


@dataclass
class SourceHealth:
    id: str
    name: str
    ok: bool
    reason: Optional[str] = None
    detail: Optional[dict] = None

    def to_dict(self) -> dict:
        return asdict(self)


class SourceCallError(Exception):
    """A source could not answer a tool call; `reason` is a short machine
    code (disabled, no_token, unauthorized, timeout, unreachable, http_xxx)."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


async def check_health(source: Source, client: Optional[httpx.AsyncClient] = None, timeout: float = DEFAULT_TIMEOUT) -> SourceHealth:
    if not source.enabled:
        return SourceHealth(source.id, source.name, False, "disabled")
    owns_client = client is None
    active = client or httpx.AsyncClient(timeout=timeout)
    try:
        resp = await active.get(f"{source.base_url}/api/health", timeout=timeout)
        if resp.status_code >= 400:
            return SourceHealth(source.id, source.name, False, f"http_{resp.status_code}")
        try:
            detail = resp.json()
        except ValueError:
            detail = None
        return SourceHealth(source.id, source.name, True, None, detail)
    except httpx.TimeoutException:
        return SourceHealth(source.id, source.name, False, "timeout")
    except httpx.HTTPError:
        return SourceHealth(source.id, source.name, False, "unreachable")
    finally:
        if owns_client:
            await active.aclose()


async def _call_via_hub(
    source: Source, tool: str, arguments: dict, active: httpx.AsyncClient, timeout: float, reason: str,
) -> dict:
    """The Hoard Hub proxy (`POST /api/apps/<id>/call`): the hub holds every
    sibling's token, Funes only presents its own. Used when the direct
    route cannot work (no readable token, refused token, nothing at the
    configured URL) — so a moved app or a Node app that rotated its token
    on restart still answers. Raises the *original* reason when the hub is
    not there either."""
    from .hoard_link import family

    own = family.status().get("token_file") or ""
    try:
        own_token = Path(own).read_text(encoding="utf-8").strip() if own else ""
    except OSError:
        own_token = ""
    if not own_token:
        raise SourceCallError(reason)
    hub = family._hub()
    try:
        resp = await active.post(
            f"{hub}/api/apps/{source.id}/call",
            json={"tool": tool, "arguments": arguments, "timeout_s": timeout},
            headers={"Authorization": f"Bearer {own_token}"},
            timeout=timeout + 2.0,
        )
    except httpx.HTTPError as exc:
        raise SourceCallError(reason) from exc
    if resp.status_code == 401:
        raise SourceCallError("unauthorized")
    try:
        body = resp.json()
    except ValueError:
        raise SourceCallError(f"http_{resp.status_code}")
    if resp.status_code >= 400 or not isinstance(body, dict) or not body.get("ok", True):
        err = (body or {}).get("error", "") if isinstance(body, dict) else ""
        raise SourceCallError("unreachable" if "not reachable" in str(err) else f"http_{resp.status_code}")
    return body.get("result", body)


async def call_source_tool(
    source: Source, tool: str, arguments: dict, client: Optional[httpx.AsyncClient] = None, timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    if not source.enabled:
        raise SourceCallError("disabled")
    token = source.read_token()
    owns_client = client is None
    active = client or httpx.AsyncClient(timeout=timeout)
    if not token:
        try:
            return await _call_via_hub(source, tool, arguments, active, timeout, "no_token")
        finally:
            if owns_client:
                await active.aclose()
    try:
        resp = await active.post(
            f"{source.base_url}/api/agent/call",
            json={"name": tool, "arguments": arguments},
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )
        if resp.status_code == 401:
            return await _call_via_hub(source, tool, arguments, active, timeout, "unauthorized")
        if resp.status_code >= 400:
            raise SourceCallError(f"http_{resp.status_code}")
        return resp.json()
    except httpx.TimeoutException as exc:
        raise SourceCallError("timeout") from exc
    except httpx.HTTPError:
        return await _call_via_hub(source, tool, arguments, active, timeout, "unreachable")
    finally:
        if owns_client:
            await active.aclose()
