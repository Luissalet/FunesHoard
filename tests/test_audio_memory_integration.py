"""Audio memory runs in Funes's process, with no recorder service port."""
import json
import socket
import threading
import time

import httpx
import uvicorn
from fastapi.testclient import TestClient

from funes_hoard.api import create_app
from funes_hoard.audio_memory.merge import Labelled
from tests.fakes import FakeLink


def test_embedded_audio_status_and_agent_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIBE_AUDIO", "none")
    monkeypatch.setenv("SCRIBE_TRANSCRIBER", "fake")
    app = create_app(tmp_path, None, demo=True, port=18877, link_factory=FakeLink)
    with TestClient(app, base_url="http://127.0.0.1:18877") as client:
        assert client.get("/api/health").json()["service"] == "funes-hoard"
        status = client.get("/audio/api/status")
        assert status.status_code == 200, status.text
        assert status.json()["recording"] is None
        tools = client.get("/audio/api/agent/tools")
        assert tools.status_code == 200, tools.text
        assert "scribe_transcript" in str(tools.json())
        assert client.get("/audio/").status_code == 200
        assert "/audio/assets/" in client.get("/audio/").text
        assert client.get("/audio/manifest.webmanifest").json()["scope"] == "/audio/"
        assert "funes-audio-assets" in client.get("/audio/sw.js").text
        assert (tmp_path / "audio" / "mcp-token").is_file()
        source = app.state.sources_registry.get("scribe")
        assert source.base_url == "http://127.0.0.1:18877/audio"
        assert source.token_path == str(tmp_path / "audio" / "mcp-token")


def test_audio_session_tools_change_embedded_state(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIBE_AUDIO", "none")
    monkeypatch.setenv("SCRIBE_TRANSCRIBER", "fake")
    app = create_app(tmp_path, None, demo=True, port=18878, link_factory=FakeLink)
    with TestClient(app, base_url="http://127.0.0.1:18878") as client:
        store = app.state.audio_app.state.services.sessions
        created = store.create("Reunión de prueba", "meeting", True, False, "es", status="done")
        sid = created["id"]
        store.update(sid, duration_s=90, ended_at=created["started_at"] + 90)
        store.add_segments(sid, [Labelled(12, 17, "S1", "Aprobamos el presupuesto de 42 euros")], live=False)
        token = (tmp_path / "audio" / "mcp-token").read_text(encoding="utf-8")

        def call(name, arguments):
            response = client.post("/audio/api/agent/call", json={"name": name, "arguments": arguments},
                                   headers={"Authorization": f"Bearer {token}"})
            assert response.status_code == 200, response.text
            return response.json()

        hits = call("scribe_search", {"q": "presupuesto"})
        assert hits["sessions"][0]["session"]["id"] == sid
        transcript = call("scribe_transcript", {"session_id": sid})
        assert "42 euros" in transcript["segments"][0]["text"]
        assert call("scribe_note", {"session_id": sid, "notes": "Acción: enviar acta"})["existing"] is False
        assert call("scribe_note", {"session_id": sid, "notes": "Acción: enviar acta"})["existing"] is True
        assert call("scribe_tag", {"session_id": sid, "add": ["presupuesto"]})["tags"] == ["presupuesto"]
        assert "Acción: enviar acta" in call("scribe_export", {"session_id": sid, "format": "md"})["text"]
        assert store.get(sid)["notes"] == "Acción: enviar acta"
        assert store.get(sid)["tags"] == ["presupuesto"]
        assert call("scribe_delete", {"session_id": sid})["deleted"] is True
        assert store.get(sid) is None


def test_configured_mobile_host_can_use_audio_api(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNES_ALLOWED_HOSTS", "funes.example")
    monkeypatch.setenv("SCRIBE_AUDIO", "none")
    monkeypatch.setenv("SCRIBE_TRANSCRIBER", "fake")
    app = create_app(tmp_path, None, demo=True, port=18879, link_factory=FakeLink)
    with TestClient(app, base_url="https://funes.example") as client:
        response = client.get("/audio/api/status")
        assert response.status_code == 200, response.text
        assert client.get("/audio/manifest.webmanifest").status_code == 200


def test_recall_search_reads_audio_inside_same_process(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIBE_AUDIO", "none")
    monkeypatch.setenv("SCRIBE_TRANSCRIBER", "fake")
    monkeypatch.setenv("FUNES_SOURCES", json.dumps([
        {"id": "argus", "base_url": "http://127.0.0.1:1"},
        {"id": "echo", "base_url": "http://127.0.0.1:1"},
    ]))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    app = create_app(tmp_path, None, demo=True, port=port, link_factory=FakeLink)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                if httpx.get(base + "/api/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        else:
            raise AssertionError("isolated Funes did not start")
        store = app.state.audio_app.state.services.sessions
        created = store.create("Reunión de arquitectura", "meeting", True, False, "es", status="done")
        store.add_segments(created["id"], [Labelled(1, 3, "S1", "Migrar el proyecto Atlas")], live=False)
        result = httpx.post(base + "/api/agent/recall_search", json={"query": "Atlas", "sources": ["scribe"]}, timeout=10)
        assert result.status_code == 200, result.text
        body = result.json()
        assert any(item["source"] == "scribe" and "Atlas" in item["text"] for item in body["items"])
        assert not any(item["id"] == "scribe" for item in body["summary"]["unavailable"])
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_embedded_recorder_captures_and_finalizes_two_tracks(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIBE_AUDIO", "fake")
    monkeypatch.setenv("SCRIBE_TRANSCRIBER", "fake")
    monkeypatch.setenv("SCRIBE_FAKE_SPEED", "0")
    app = create_app(tmp_path, None, demo=True, port=18880, link_factory=FakeLink)
    with TestClient(app, base_url="http://127.0.0.1:18880") as client:
        token = (tmp_path / "audio" / "mcp-token").read_text(encoding="utf-8")
        headers = {"Authorization": f"Bearer {token}"}
        started = client.post("/audio/api/agent/call", headers=headers,
                              json={"name": "scribe_start", "arguments": {"title": "Ensayo de dos pistas"}})
        assert started.status_code == 200, started.text
        sid = started.json()["session"]["id"]
        for _ in range(100):
            current = client.get("/audio/api/status").json()["recording"]
            if current and current["elapsed_s"] >= 7.9:
                break
            time.sleep(0.05)
        stopped = client.post("/audio/api/agent/call", headers=headers,
                              json={"name": "scribe_stop", "arguments": {"session_id": sid}})
        assert stopped.status_code == 200, stopped.text
        for _ in range(100):
            detail = client.get(f"/audio/api/sessions/{sid}").json()
            if detail["status"] == "done":
                break
            time.sleep(0.05)
        assert detail["status"] == "done"
        assert detail["segments"]
        assert {segment["speaker"] for segment in detail["segments"]} == {"yo", "otros"}
        assert (tmp_path / "audio" / "sessions" / sid / "mic.wav").is_file()
        assert (tmp_path / "audio" / "sessions" / sid / "system.wav").is_file()
