"""Saving one field must not overwrite the others: a form that sends only what the user changed (settings, audio
session title/notes/tags) keeps an edit the assistant made to another field in the meantime."""
from __future__ import annotations

import json
import threading

import pytest
from fastapi.testclient import TestClient

from funes_hoard.api import create_app
from funes_hoard.hoard_link import LinkConfig
from tests.conftest import agent_headers
from tests.fakes import FakeLink

PORT = 18851


@pytest.fixture()
def app_and_client(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIBE_AUDIO", "none")
    monkeypatch.setenv("SCRIBE_TRANSCRIBER", "fake")
    link = FakeLink(LinkConfig(app="funes"))
    app = create_app(tmp_path, None, demo=True, port=PORT, link=link, link_factory=lambda cfg: FakeLink(cfg))
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client:
        client.headers.update(agent_headers())
        yield app, client, tmp_path


def _session(app, title="Reunion", **fields):
    store = app.state.audio_app.state.services.sessions
    created = store.create(title, "meeting", True, False, "es", status="done")
    if fields:
        store.update(created["id"], **fields)
    return created["id"], store


# ---------------------------------------------------------------- settings --
def _backend_json(data_dir):
    return json.loads((data_dir / "backend.json").read_text(encoding="utf-8"))


def test_backend_config_put_merges_per_key(app_and_client):
    _app, client, data_dir = app_and_client
    client.put("/api/backend/config", json={"faustus_url": "http://127.0.0.1:7000", "faustus_token": "ody_secret",
                                            "capabilities": {"llm": {"url": "http://127.0.0.1:8081", "model": "qwen"}}})
    # The form later sends only the model it changed: the URL, the token and the Faustus address stay.
    client.put("/api/backend/config", json={"capabilities": {"llm": {"model": "llama"}}})
    saved = _backend_json(data_dir)
    assert saved["capabilities"]["llm"] == {"url": "http://127.0.0.1:8081", "model": "llama"}
    assert saved["faustus"] == {"url": "http://127.0.0.1:7000", "token": "ody_secret"}
    # Only the Faustus address: the capability override is untouched.
    client.put("/api/backend/config", json={"faustus_url": "http://127.0.0.1:7001"})
    saved = _backend_json(data_dir)
    assert saved["faustus"] == {"url": "http://127.0.0.1:7001", "token": "ody_secret"}
    assert saved["capabilities"]["llm"]["model"] == "llama"


def test_backend_config_put_empty_string_clears_only_that_key(app_and_client):
    _app, client, data_dir = app_and_client
    client.put("/api/backend/config", json={"capabilities": {"llm": {"url": "http://127.0.0.1:8081", "model": "qwen"}}})
    client.put("/api/backend/config", json={"capabilities": {"llm": {"model": ""}}})
    assert _backend_json(data_dir)["capabilities"]["llm"] == {"url": "http://127.0.0.1:8081"}


# ----------------------------------------------------------- audio session --
def test_session_patch_touches_only_the_fields_sent(app_and_client):
    app, client, _ = app_and_client
    sid, _store = _session(app, notes="nota del asistente", tags=["cliente"])
    r = client.patch(f"/audio/api/sessions/{sid}", json={"title": "Reunion con Marta"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["title"], body["notes"], body["tags"], body["kind"]) == ("Reunion con Marta", "nota del asistente", ["cliente"], "meeting")
    body = client.patch(f"/audio/api/sessions/{sid}", json={"notes": "editadas"}).json()
    assert (body["title"], body["notes"], body["tags"]) == ("Reunion con Marta", "editadas", ["cliente"])
    # An emptied field is a real change, not "missing".
    assert client.patch(f"/audio/api/sessions/{sid}", json={"notes": ""}).json()["notes"] == ""
    assert client.patch(f"/audio/api/sessions/{sid}", json={"kind": "interview"}).json()["title"] == "Reunion con Marta"


def test_session_patch_add_and_remove_tags_merge_into_the_stored_list(app_and_client):
    app, client, _ = app_and_client
    sid, store = _session(app)
    # The assistant tags the session while the page still holds the old (empty) list.
    store.change_tags(sid, add=["presupuesto"])
    body = client.patch(f"/audio/api/sessions/{sid}", json={"add_tags": ["Cliente"]}).json()
    assert body["tags"] == ["presupuesto", "cliente"]
    body = client.patch(f"/audio/api/sessions/{sid}", json={"remove_tags": ["presupuesto"], "add_tags": ["cliente", "urgente"]}).json()
    assert body["tags"] == ["cliente", "urgente"]
    assert client.patch(f"/audio/api/sessions/{sid}", json={"remove_tags": ["nope"]}).json()["tags"] == ["cliente", "urgente"]


def test_session_patch_unknown_id_is_404(app_and_client):
    _app, client, _ = app_and_client
    assert client.patch("/audio/api/sessions/does-not-exist", json={"add_tags": ["x"]}).status_code == 404


def test_change_tags_is_atomic_under_concurrent_writers(app_and_client):
    app, _client, _ = app_and_client
    sid, store = _session(app)
    names = [f"tag{i}" for i in range(20)]
    threads = [threading.Thread(target=store.change_tags, args=(sid,), kwargs={"add": [n]}) for n in names]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(store.get(sid)["tags"]) == sorted(names)
    assert store.change_tags("missing", add=["x"]) is None


def test_scribe_tag_tool_keeps_tags_added_elsewhere(app_and_client):
    app, client, tmp_path = app_and_client
    sid, store = _session(app, tags=["viejo"])
    token = (tmp_path / "audio" / "mcp-token").read_text(encoding="utf-8")
    response = client.post("/audio/api/agent/call", json={"name": "scribe_tag", "arguments": {"session_id": sid, "add": ["nuevo"], "remove": ["VIEJO"]}},
                           headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    assert response.json()["tags"] == ["nuevo"]
    assert store.get(sid)["tags"] == ["nuevo"]
