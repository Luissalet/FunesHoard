"""The shared model backend: config persistence, the Models settings API,
and the "Write my day" feature -- all offline, against a FakeLink."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from funes_hoard import backend
from funes_hoard.api import create_app
from funes_hoard.hoard_link import LinkConfig, Unavailable

from .fakes import FakeLink, chat_text, resolved_llm

PORT = 18833


# --------------------------------------------------------- backend.py --
def test_backend_json_round_trip(tmp_path):
    assert backend.read_backend_json(tmp_path) == {}
    backend.write_backend_json(tmp_path, {"only_resident": False})
    assert backend.read_backend_json(tmp_path) == {"only_resident": False}


def test_backend_json_tolerates_a_bom_and_garbage(tmp_path):
    (tmp_path / backend.BACKEND_FILE).write_bytes(b"\xef\xbb\xbf{\"only_resident\": true}")
    assert backend.read_backend_json(tmp_path) == {"only_resident": True}
    (tmp_path / backend.BACKEND_FILE).write_text("not json", encoding="utf-8")
    assert backend.read_backend_json(tmp_path) == {}


def test_apply_config_patch_sets_and_clears_faustus_and_capabilities(tmp_path):
    raw = backend.apply_config_patch(tmp_path, {
        "faustus_url": "http://127.0.0.1:7000", "faustus_token": "ody_x",
        "capabilities": {"llm": {"url": "http://127.0.0.1:8081", "model": "qwen"}},
    })
    assert raw["faustus"] == {"url": "http://127.0.0.1:7000", "token": "ody_x"}
    assert raw["capabilities"]["llm"] == {"url": "http://127.0.0.1:8081", "model": "qwen"}

    # Clearing the token (empty string) leaves the url; clearing both drops the section.
    raw = backend.apply_config_patch(tmp_path, {"faustus_token": ""})
    assert raw["faustus"] == {"url": "http://127.0.0.1:7000"}
    raw = backend.apply_config_patch(tmp_path, {"faustus_url": ""})
    assert "faustus" not in raw


def test_apply_config_patch_never_touches_untouched_keys(tmp_path):
    backend.write_backend_json(tmp_path, {"only_resident": False, "comfy": {"url": "http://127.0.0.1:8188"}})
    raw = backend.apply_config_patch(tmp_path, {"faustus_url": "http://127.0.0.1:7000"})
    assert raw["only_resident"] is False
    assert raw["comfy"] == {"url": "http://127.0.0.1:8188"}


def test_load_link_config_reads_the_saved_file(tmp_path):
    backend.write_backend_json(tmp_path, {"only_resident": False})
    cfg = backend.load_link_config(tmp_path, env={})
    assert cfg.only_resident is False
    assert cfg.app == backend.APP_ID


def test_build_narrative_messages_carries_only_the_summary():
    summary = {"active_human": "3 h", "by_category": {"Coding": 9000}}
    messages = backend.build_narrative_messages(summary)
    assert messages[0]["role"] == "system"
    assert json.loads(messages[1]["content"]) == summary


# ------------------------------------------------------------- routes --
@pytest.fixture()
def app_link(demo_data_dir):
    link = FakeLink(LinkConfig(app="funes"))
    app = create_app(demo_data_dir, None, demo=True, port=PORT, link=link, link_factory=lambda cfg: FakeLink(cfg))
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as c:
        yield c, link, demo_data_dir


def test_backend_status_reports_unavailable_honestly(app_link):
    client, _link, _ = app_link
    body = client.get("/api/backend").json()
    assert body["capabilities"]["llm"]["state"] == "unavailable"
    assert body["faustus_token_set"] is False
    assert body["write_my_day_enabled"] is True
    # Hoard Link always has a *default* Faustus probe address internally,
    # but nothing was ever explicitly configured here -- Settings must not
    # show (and later silently re-save) that default as if it were one.
    assert body["faustus_url"] is None
    assert body["llm_url_override"] is None
    assert body["llm_model_override"] is None


def test_backend_status_reflects_a_saved_override(app_link):
    client, _link, _ = app_link
    client.put("/api/backend/config", json={"faustus_url": "http://127.0.0.1:7000", "capabilities": {"llm": {"model": "qwen"}}})
    body = client.get("/api/backend").json()
    assert body["faustus_url"] == "http://127.0.0.1:7000"
    assert body["llm_model_override"] == "qwen"


def test_backend_status_reports_a_resolved_model(app_link):
    client, link, _ = app_link
    link.resolution = resolved_llm()
    body = client.get("/api/backend").json()
    assert body["capabilities"]["llm"]["state"] == "resolved"
    assert body["capabilities"]["llm"]["model"] == "qwen3.8-27b-q8-llamacpp"


def test_backend_config_put_persists_and_never_returns_the_token(app_link):
    client, link, data_dir = app_link
    r = client.put("/api/backend/config", json={"faustus_url": "http://127.0.0.1:7000", "faustus_token": "ody_secret"})
    assert r.status_code == 200
    body = r.json()
    assert body["faustus_token_set"] is True
    assert "ody_secret" not in json.dumps(body)
    assert link.config.faustus_token == "ody_secret"
    on_disk = json.loads((data_dir / "backend.json").read_text(encoding="utf-8"))
    assert on_disk["faustus"]["token"] == "ody_secret"


def test_backend_config_put_sets_a_capability_override(app_link):
    client, link, _ = app_link
    client.put("/api/backend/config", json={"capabilities": {"llm": {"url": "http://127.0.0.1:8081", "model": "qwen"}}})
    assert link.config.capability("llm").url == "http://127.0.0.1:8081"
    assert link.config.capability("llm").model == "qwen"


def test_backend_recheck_swaps_the_link_and_closes_the_old_one(app_link):
    client, link, _ = app_link
    r = client.post("/api/backend/recheck")
    assert r.status_code == 200
    assert link.closed is True


# ------------------------------------------------------- write my day --
def test_day_narrative_reports_unavailable_when_no_model_resolves(app_link):
    client, _link, _ = app_link
    body = client.get("/api/day-narrative", params={"day": "yesterday"}).json()
    assert body["available"] is False
    assert body["text"] is None
    assert "reason" in body


def test_day_narrative_generates_and_caches(app_link):
    client, link, _ = app_link
    link.resolution = resolved_llm()
    link.chat_result = chat_text("You spent the morning on Atlas, in Coding.")
    r = client.post("/api/day-narrative", json={"day": "yesterday"})
    assert r.status_code == 200
    body = r.json()
    assert body["text"] == "You spent the morning on Atlas, in Coding."
    assert body["model"] == "qwen3.8-27b-q8-llamacpp"
    assert body["cached"] is False
    assert len(link.chat_calls) == 1

    # Second call is served from the cache: no new chat call.
    r2 = client.post("/api/day-narrative", json={"day": "yesterday"})
    assert r2.json()["cached"] is True
    assert len(link.chat_calls) == 1

    get_body = client.get("/api/day-narrative", params={"day": "yesterday"}).json()
    assert get_body["text"] == body["text"]


def test_day_narrative_force_regenerates(app_link):
    client, link, _ = app_link
    link.resolution = resolved_llm()
    link.chat_result = chat_text("First version.")
    client.post("/api/day-narrative", json={"day": "yesterday"})
    link.chat_result = chat_text("Second version.")
    r = client.post("/api/day-narrative", json={"day": "yesterday", "force": True})
    assert r.json()["text"] == "Second version."
    assert len(link.chat_calls) == 2


def test_day_narrative_prompt_carries_only_the_compact_summary_never_titles(app_link):
    client, link, _ = app_link
    link.resolution = resolved_llm()
    link.chat_result = chat_text("A narrative.")
    client.post("/api/day-narrative", json={"day": "yesterday"})
    payload = json.loads(link.chat_calls[0][1]["content"])
    assert "by_category" in payload
    # The demo day's window titles must never reach the prompt.
    assert "DuckDB" not in json.dumps(payload)


def test_day_narrative_unavailable_model_is_a_clear_400(app_link):
    client, link, _ = app_link
    link.chat_error = Unavailable("llm", ["no llama.cpp server found on ports 8080-8090"])
    r = client.post("/api/day-narrative", json={"day": "yesterday"})
    assert r.status_code == 400
    body = r.json()
    assert body["error"] == "llm_unavailable"
    assert "Faustus" in body["message"]


def test_day_narrative_disabled_feature_is_rejected(app_link):
    client, link, _ = app_link
    link.resolution = resolved_llm()
    link.chat_result = chat_text("Should not be used.")
    client.put("/api/settings/write-my-day", json={"enabled": False})
    r = client.post("/api/day-narrative", json={"day": "yesterday"})
    assert r.status_code == 400
    assert r.json()["error"] == "feature_disabled"
    assert link.chat_calls == []

    status = client.get("/api/day-narrative", params={"day": "yesterday"}).json()
    assert status["enabled"] is False
    assert status["available"] is False


def test_write_my_day_setting_round_trip(app_link):
    client, _link, _ = app_link
    assert client.get("/api/settings/write-my-day").json()["enabled"] is True
    client.put("/api/settings/write-my-day", json={"enabled": False})
    assert client.get("/api/settings/write-my-day").json()["enabled"] is False


def test_backend_routes_are_not_part_of_the_agent_surface(app_link):
    client, _link, _ = app_link
    paths = {getattr(r, "path", "") for r in client.app.routes}
    for p in ("/api/backend", "/api/backend/config", "/api/backend/recheck", "/api/day-narrative", "/api/settings/write-my-day"):
        assert p in paths
        assert not p.startswith("/api/agent/")


def test_day_narrative_prompt_includes_apps_and_projects(app_link):
    client, link, _ = app_link
    link.resolution = resolved_llm()
    link.chat_result = chat_text("A narrative.")
    client.post("/api/day-narrative", json={"day": "yesterday"})
    payload = json.loads(link.chat_calls[0][1]["content"])
    # The system prompt tells the model to use app and project names, so
    # they must actually be in the data it gets.
    assert payload["by_app"] and payload["by_project"]


def test_day_narrative_with_no_activity_never_calls_the_model(app_link):
    client, link, _ = app_link
    link.resolution = resolved_llm()
    link.chat_result = chat_text("An invented day.")
    r = client.post("/api/day-narrative", json={"day": "2020-01-01"})
    assert r.status_code == 400
    assert r.json()["error"] == "no_activity"
    assert link.chat_calls == []
    assert client.get("/api/day-narrative", params={"day": "2020-01-01"}).json()["text"] is None


def test_day_narrative_empty_model_answer_is_not_cached(app_link):
    client, link, _ = app_link
    link.resolution = resolved_llm()
    link.chat_result = chat_text("   ")
    r = client.post("/api/day-narrative", json={"day": "yesterday"})
    assert r.status_code == 400
    assert r.json()["error"] == "llm_empty"
    assert client.get("/api/day-narrative", params={"day": "yesterday"}).json()["text"] is None


def test_day_narrative_through_the_real_link_over_http(demo_data_dir, monkeypatch):
    """The real Hoard Link (explicit HOARD_LLM_URL) against a mocked
    OpenAI-compatible server: the request that leaves the app, and the
    reasoning Hoard Link strips from what gets cached."""
    import httpx

    from funes_hoard.hoard_link import Link

    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {
            "role": "assistant", "content": "<think>short plan</think>You spent most of the day on Atlas."}}]})

    monkeypatch.setenv("HOARD_LLM_URL", "http://127.0.0.1:18899/v1")
    monkeypatch.setenv("HOARD_LLM_MODEL", "test-model")
    client_http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = create_app(demo_data_dir, None, demo=True, port=PORT, link_factory=lambda cfg: Link(cfg, client=client_http))
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as c:
        status = c.get("/api/backend").json()["capabilities"]["llm"]
        assert status["state"] == "resolved" and status["model"] == "test-model"
        body = c.post("/api/day-narrative", json={"day": "yesterday"}).json()
    assert body["text"] == "You spent most of the day on Atlas."
    assert body["model"] == "test-model"
    assert len(seen) == 1 and seen[0]["model"] == "test-model"
    assert seen[0]["messages"][0]["role"] == "system"
