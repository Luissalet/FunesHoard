"""Source registry: defaults, persisted overrides, env overrides, health/call."""
import json

import httpx
import pytest

from funes_hoard.sources import (
    SourceCallError, SourceRegistry, call_source_tool, check_health, default_sources,
)


def test_default_sources_have_the_well_known_ids_and_ports():
    ids = {s.id: s for s in default_sources()}
    assert ids["argus"].base_url == "http://127.0.0.1:5183"
    assert ids["echo"].base_url == "http://127.0.0.1:5188"
    assert ids["scribe"].base_url == "http://127.0.0.1:5185"
    assert all(s.enabled for s in ids.values())


def test_registry_persists_a_patch_across_instances(tmp_path):
    reg = SourceRegistry(tmp_path)
    updated = reg.save_patch("argus", {"base_url": "http://127.0.0.1:9999", "enabled": False})
    assert updated.base_url == "http://127.0.0.1:9999"
    assert updated.enabled is False

    reg2 = SourceRegistry(tmp_path)
    argus = reg2.get("argus")
    assert argus.base_url == "http://127.0.0.1:9999"
    assert argus.enabled is False
    # Untouched sources keep their defaults.
    assert reg2.get("echo").base_url == "http://127.0.0.1:5188"


def test_registry_creates_a_new_source_from_a_patch_with_a_base_url(tmp_path):
    reg = SourceRegistry(tmp_path)
    reg.save_patch("custom", {"name": "Custom Hoard", "base_url": "http://127.0.0.1:9001"})
    custom = reg.get("custom")
    assert custom is not None
    assert custom.name == "Custom Hoard"


def test_registry_sources_json_survives_a_partial_file(tmp_path):
    (tmp_path / "sources.json").write_text(json.dumps({"sources": [{"id": "argus", "enabled": False}]}), encoding="utf-8")
    reg = SourceRegistry(tmp_path)
    argus = reg.get("argus")
    assert argus.enabled is False
    assert argus.base_url == "http://127.0.0.1:5183"  # unspecified field keeps the default


def test_env_override_wins_over_stored_file(tmp_path, monkeypatch):
    reg = SourceRegistry(tmp_path)
    reg.save_patch("argus", {"base_url": "http://127.0.0.1:1111"})
    monkeypatch.setenv("FUNES_SOURCES", json.dumps([{"id": "argus", "base_url": "http://127.0.0.1:2222"}]))
    assert reg.get("argus").base_url == "http://127.0.0.1:2222"


def test_env_override_survives_malformed_json(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNES_SOURCES", "{not json")
    reg = SourceRegistry(tmp_path)
    assert reg.get("argus").base_url == "http://127.0.0.1:5183"


@pytest.mark.asyncio
async def test_check_health_ok(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"service": "argus", "status": "ok"})

    reg = SourceRegistry(tmp_path)
    source = reg.get("argus")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        health = await check_health(source, client=client)
    assert health.ok is True
    assert health.detail["service"] == "argus"


@pytest.mark.asyncio
async def test_check_health_reports_disabled_without_a_network_call(tmp_path):
    reg = SourceRegistry(tmp_path)
    reg.save_patch("argus", {"enabled": False})
    source = reg.get("argus")
    health = await check_health(source)
    assert health.ok is False
    assert health.reason == "disabled"


@pytest.mark.asyncio
async def test_check_health_reports_timeout(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("slow", request=request)

    reg = SourceRegistry(tmp_path)
    source = reg.get("argus")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        health = await check_health(source, client=client)
    assert health.ok is False
    assert health.reason == "timeout"


@pytest.mark.asyncio
async def test_check_health_reports_unreachable_on_connect_error(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    reg = SourceRegistry(tmp_path)
    source = reg.get("argus")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        health = await check_health(source, client=client)
    assert health.ok is False
    assert health.reason == "unreachable"


@pytest.mark.asyncio
async def test_call_source_tool_needs_a_token(tmp_path, monkeypatch):
    # The real sibling app may be running on this machine: point at a token that does not exist.
    monkeypatch.setenv("FUNES_SOURCES", json.dumps([{"id": "argus", "token_path": str(tmp_path / "no-token")}]))
    # ...and no hub to fall back to either (the real one may be running here).
    from funes_hoard.hoard_link import family
    monkeypatch.setattr(family, "_hub", lambda: "http://127.0.0.1:1")
    reg = SourceRegistry(tmp_path)
    source = reg.get("argus")
    with pytest.raises(SourceCallError) as exc_info:
        await call_source_tool(source, "screen_status", {})
    assert exc_info.value.reason == "no_token"


@pytest.mark.asyncio
async def test_call_source_tool_sends_bearer_token_and_returns_json(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("s3cr3t", encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer s3cr3t"
        body = json.loads(request.content)
        assert body["name"] == "screen_status"
        return httpx.Response(200, json={"state": "watching"})

    reg = SourceRegistry(tmp_path)
    reg.save_patch("argus", {"token_path": str(token_file)})
    source = reg.get("argus")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await call_source_tool(source, "screen_status", {}, client=client)
    assert result == {"state": "watching"}


@pytest.mark.asyncio
async def test_call_source_tool_maps_401_and_5xx(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("bad", encoding="utf-8")
    reg = SourceRegistry(tmp_path)
    reg.save_patch("argus", {"token_path": str(token_file)})
    source = reg.get("argus")

    async def _call(status):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status, json={"error": "nope"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await call_source_tool(source, "screen_status", {}, client=client)

    with pytest.raises(SourceCallError) as exc_info:
        await _call(401)
    assert exc_info.value.reason == "unauthorized"

    with pytest.raises(SourceCallError) as exc_info:
        await _call(500)
    assert exc_info.value.reason == "http_500"
