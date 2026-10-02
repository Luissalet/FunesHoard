"""Minutes and import-by-path through the real app: the audio UI API, the family tool surface and the pipeline hook."""
from __future__ import annotations

import time
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from funes_hoard.api import create_app
from funes_hoard.audio_memory.audio.fake import synth_speechlike
from funes_hoard.audio_memory.audio.wav import write_wav
from funes_hoard.audio_memory.merge import Labelled
from funes_hoard.hoard_link import family
from tests.fakes import ScriptedLink

MEETING_AT = datetime(2026, 10, 2, 10, 0).timestamp()
LINES = [
    (0, 6, "otros", "Buenos días a todos, empezamos con el presupuesto de la reforma."),
    (6, 14, "yo", "Vale, yo me encargo de enviar el presupuesto revisado a Marta el martes."),
    (14, 22, "otros", "Pedro dice: yo preparo el informe de costes para el viernes que viene."),
]
MINUTES = {
    "summary": ["Se revisa el presupuesto."],
    "decisions": [],
    "action_items": [{"owner": "yo", "action": "Enviar el presupuesto revisado", "counterpart": "Marta", "due_text": "el martes",
                      "quote": "yo me encargo de enviar el presupuesto revisado a Marta el martes"}],
    "open_questions": [],
    "participants": ["Marta", "Pedro"],
}


@pytest.fixture()
def booted(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIBE_AUDIO", "none")
    monkeypatch.setenv("SCRIBE_TRANSCRIBER", "fake")
    monkeypatch.setenv("HOARD_HUB_URL", "http://127.0.0.1:1")
    sent: list[tuple[str, dict]] = []
    real_emit = family.emit
    monkeypatch.setattr(family, "emit", lambda kind, data=None, **kw: sent.append((kind, data or {})) or True)
    link = ScriptedLink([MINUTES])
    app = create_app(tmp_path / "data", None, demo=True, port=18861, link=link)
    with TestClient(app, base_url="http://127.0.0.1:18861") as client:
        audio = app.state.audio_app.state.services
        family_headers = {"Authorization": "Bearer " + open(family.status()["token_file"], encoding="utf-8").read().strip()}
        yield type("Booted", (), {"client": client, "audio": audio, "link": link, "sent": sent, "headers": family_headers, "tmp": tmp_path, "real_emit": real_emit})


def seed(booted, lines=LINES, kind="meeting", status="done"):
    session = booted.audio.sessions.create("Reunión de la reforma", kind, True, True, "es", status=status, started_at=MEETING_AT)
    booted.audio.sessions.add_segments(session["id"], [Labelled(*line) for line in lines], live=False)
    return session["id"]


def call(booted, name, arguments, expect=200):
    response = booted.client.post("/api/agent/call", json={"name": name, "arguments": arguments}, headers=booted.headers)
    assert response.status_code == expect, response.text
    return response.json()


def wait_for(predicate, timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("timed out")


def test_family_surface_lists_the_audio_tools_with_proper_descriptions(booted):
    catalogue = booted.client.get("/api/agent/tools").json()
    tools = {t["name"]: t for t in catalogue["tools"]}
    assert {"scribe_minutes", "minutes_get", "scribe_import_file"} <= set(tools)
    assert len(tools) == 15  # the twelve activity tools plus these three: start/stop/delete stay off the family surface
    for name in ("scribe_minutes", "minutes_get", "scribe_import_file"):
        first = tools[name]["description"].split("\n")[0]
        assert len(first) <= 110 and "Sinónimos:" in tools[name]["description"]
    assert tools["scribe_minutes"]["inputSchema"]["required"] == ["session_id"]
    assert tools["minutes_get"]["inputSchema"]["required"] == ["minutes_id"]
    # the family catalogue guesses readOnlyHint from the name; the audio module states the truth: minutes_get may write
    audio = {t["name"]: t for t in booted.client.get("/audio/api/agent/tools").json()["tools"]}
    assert audio["minutes_get"]["annotations"]["readOnlyHint"] is False
    assert tools["scribe_import_file"]["inputSchema"]["properties"]["wait_s"]["maximum"] == 3600


def test_minutes_through_the_hub_proxy_surface(booted):
    sid = seed(booted)
    result = call(booted, "scribe_minutes", {"session_id": sid})
    assert result["status"] == "ready" and result["cached"] is False
    item = result["minutes"]["action_items"][0]
    assert item["due_date"] == "2026-10-06" and item["evidence"]["t"] == "00:06"
    assert result["minutes"]["started_at"] == "2026-10-02T10:00:00"
    assert call(booted, "scribe_minutes", {"session_id": sid})["cached"] is True
    assert len(booted.link.calls) == 1
    announced = [d for k, d in booted.sent if k == "funes.minutes.ready"]
    assert announced == [{"minutes_id": sid, "title": "Reunión de la reforma", "date": "2026-10-02", "attendees": ["Marta", "Pedro"],
                          "session_id": sid, "action_items": 1, "started_at": "2026-10-02T10:00:00"}]
    # the call is audited like the other agent tools
    calls = booted.client.get("/api/agent-calls").json()["items"]
    assert any(c["tool"] == "scribe_minutes" and c["ok"] == 1 for c in calls)
    missing = call(booted, "scribe_minutes", {"session_id": "nope"}, expect=404)
    assert missing["ok"] is False


def test_minutes_get_answers_in_the_shape_other_apps_ask_for(booted):
    sid = seed(booted)
    first = call(booted, "minutes_get", {"minutes_id": sid})
    assert first["ok"] is True and first["status"] == "ready" and first["cached"] is False
    assert first["minutes_id"] == sid and first["title"] == "Reunión de la reforma" and first["date"] == "2026-10-02"
    assert first["attendees"] == ["Marta", "Pedro"] and first["summary"] == "Se revisa el presupuesto."
    assert first["action_items"] == [{
        "text": "Enviar el presupuesto revisado", "owner": "yo", "counterpart": "Marta", "due": "2026-10-06", "due_text": "el martes",
        "quote": "yo me encargo de enviar el presupuesto revisado a Marta el martes", "t": "00:06"}]
    again = call(booted, "minutes_get", {"minutes_id": sid})
    assert again["cached"] is True and len(booted.link.calls) == 1, "stored minutes cost no model call"
    announced = [d for k, d in booted.sent if k == "funes.minutes.ready"]
    assert len(announced) == 1 and announced[0]["minutes_id"] == sid
    calls = booted.client.get("/api/agent-calls").json()["items"]
    assert any(c["tool"] == "minutes_get" and c["ok"] == 1 for c in calls)


def test_minutes_get_without_generating_and_for_a_session_not_ready(booted):
    sid = seed(booted)
    nothing = call(booted, "minutes_get", {"minutes_id": sid, "generate": False})
    assert nothing["ok"] is False and nothing["status"] == "not_generated" and len(booted.link.calls) == 0
    busy = seed(booted, status="processing")
    assert call(booted, "minutes_get", {"minutes_id": busy})["status"] == "not_ready"
    assert call(booted, "minutes_get", {"minutes_id": "nope"}, expect=404)["ok"] is False
    booted.link.resolution = type(booted.link.resolution)(capability="llm", provider=None, url=None, model=None, api=None, state="unavailable", reason="nothing loaded", details={})
    assert call(booted, "minutes_get", {"minutes_id": sid})["status"] == "no_model"


def test_attendees_count_named_owners_and_counterparts_but_not_the_user():
    from funes_hoard.audio_memory.minutes import attendees_of
    minutes = {"participants": ["Marta"], "action_items": [
        {"owner": "yo", "counterpart": "Pedro"}, {"owner": "Lucía", "counterpart": "yo"}, {"owner": "marta", "counterpart": ""}]}
    assert attendees_of(minutes) == ["Marta", "Pedro", "Lucía"]
    assert attendees_of({}) == []


def test_minutes_without_a_model_say_so(booted):
    booted.link.resolution = type(booted.link.resolution)(capability="llm", provider=None, url=None, model=None, api=None, state="unavailable", reason="nothing loaded", details={})
    sid = seed(booted)
    result = call(booted, "scribe_minutes", {"session_id": sid})
    assert result["status"] == "no_model" and "minutes" not in result


def test_audio_ui_api_queues_polls_and_exports(booted):
    sid = seed(booted)
    base = f"/audio/api/sessions/{sid}"
    state = booted.client.get(base + "/minutes").json()
    assert state["minutes"] is None and state["generating"] is False and state["model"]["available"] is True
    queued = booted.client.post(base + "/minutes", json={}).json()
    assert queued["queued"] is True
    state = wait_for(lambda: (lambda s: s if s["minutes"] and not s["generating"] else None)(booted.client.get(base + "/minutes").json()))
    assert state["minutes"]["action_items"][0]["owner"] == "yo"
    assert booted.client.get(base).json()["has_minutes"] is True
    listed = booted.client.get("/audio/api/sessions").json()["sessions"]
    assert next(s for s in listed if s["id"] == sid)["minutes_items"] == 1
    md = booted.client.get(base + "/minutes?format=md")
    assert md.status_code == 200 and "## " not in md.text.split("\n")[0] and "# Acta: Reunión de la reforma" in md.text
    exported = booted.client.get(base + "/export?format=md").text
    assert exported.index("## Acta") < exported.index("## Transcripción")
    assert "Acta" not in booted.client.get(base + "/export?format=txt").text
    assert booted.client.get(base + "/minutes?format=pdf").status_code == 400
    # waiting variant, and a session still being transcribed is refused
    booted.link.replies.append({**MINUTES, "summary": ["Otra."]})
    waited = booted.client.post(base + "/minutes", json={"regenerate": True, "wait": True}).json()
    assert waited["result"] == "ready" and waited["minutes"]["summary"] == "Otra."
    busy = seed(booted, status="processing")
    assert booted.client.post(f"/audio/api/sessions/{busy}/minutes", json={}).status_code == 409
    assert booted.client.get("/audio/api/sessions/nope/minutes").status_code == 404
    assert booted.client.get(base + "/minutes?format=md").status_code == 200
    assert booted.client.get(f"/audio/api/sessions/{busy}/minutes?format=md").status_code == 404


def test_scribe_minutes_stays_reachable_on_the_bridge_surface(booted):
    sid = seed(booted)
    token = open(booted.audio.config.token_path, encoding="utf-8").read().strip()
    names = {t["name"] for t in booted.client.get("/audio/api/agent/tools").json()["tools"]}
    assert {"scribe_minutes", "scribe_import_file"} <= names
    response = booted.client.post("/audio/api/agent/call", json={"name": "scribe_minutes", "arguments": {"session_id": sid}}, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200 and response.json()["status"] == "ready"
    assert "Acta" in booted.client.post("/audio/api/agent/call", json={"name": "scribe_export", "arguments": {"session_id": sid}}, headers={"Authorization": f"Bearer {token}"}).json()["text"]


def make_audio(path, seconds=24.0):
    write_wav(path, synth_speechlike(seconds))
    return path


def test_import_by_path_keeps_the_original_and_returns_the_transcript(booted):
    source = make_audio(booted.tmp / "clase de cocina.wav")
    result = call(booted, "scribe_import_file", {"path": str(source), "title": "Clase de cocina", "kind": "note", "wait_s": 30})
    assert result["status"] == "done" and result["session"]["title"] == "Clase de cocina"
    assert result["transcript_text"].startswith("[00:00] ") and result["segments"] >= 1 and result["next_from_s"] is None
    assert "S1:" not in result["transcript_text"]  # a single track has no speaker to show
    assert source.is_file()  # the caller's file is never moved or deleted
    stored = booted.audio.sessions.get(result["session"]["id"])
    assert stored["origin"] == "import" and stored["kind"] == "note"


def test_import_by_path_without_waiting_returns_the_session_at_once(booted):
    source = make_audio(booted.tmp / "nota.wav", 6.0)
    result = call(booted, "scribe_import_file", {"path": str(source)})
    assert result["status"] in ("processing", "done") and "transcript_text" not in result
    assert "scribe_transcript" in result["message"]
    wait_for(lambda: booted.audio.sessions.get(result["session"]["id"])["status"] == "done")


def test_import_by_path_rejects_bad_paths_with_a_reason(booted):
    folder = booted.tmp / "carpeta"
    folder.mkdir()
    text = booted.tmp / "notas.txt"
    text.write_text("hola")
    empty = booted.tmp / "vacio.wav"
    empty.write_bytes(b"")
    for path, reason in [("relative/file.wav", "absolute"), (str(booted.tmp / "missing.wav"), "No such file"), (str(folder), "Not a file"),
                         (str(text), "Unsupported file type"), (str(empty), "empty")]:
        error = call(booted, "scribe_import_file", {"path": path}, expect=400)
        assert reason in error["error"], error
    assert booted.audio.sessions.count() == 0


def test_a_long_transcript_is_cut_with_a_hint_to_continue(booted, monkeypatch):
    from funes_hoard.audio_memory import agent_tools

    monkeypatch.setattr(agent_tools, "TRANSCRIPT_TEXT_MAX_CHARS", 420)
    source = make_audio(booted.tmp / "larga.wav", 30.0)
    result = call(booted, "scribe_import_file", {"path": str(source), "wait_s": 30})
    assert result["next_from_s"] is not None and "from_s=" in result["message"]
    assert len(result["transcript_text"]) <= 420


def test_a_finished_meeting_gets_minutes_by_itself(booted):
    source = make_audio(booted.tmp / "reunion.wav", 30.0)
    booted.link.replies[:] = [{**MINUTES, "action_items": [{"owner": "yo", "action": "Revisar los minutos", "due_text": "la semana que viene",
                                                              "quote": "revisar los minutos la semana que viene"}]}]
    result = call(booted, "scribe_import_file", {"path": str(source), "title": "Reunión semanal", "kind": "meeting", "wait_s": 30})
    sid = result["session"]["id"]
    minutes = wait_for(lambda: booted.audio.minutes.get(sid))
    assert minutes["action_items"][0]["action"] == "Revisar los minutos"
    assert any(k == "funes.minutes.ready" and d["session_id"] == sid for k, d in booted.sent)
    # a plain note is left alone, and so is a meeting when the setting is off
    booted.audio.settings.update({"auto_minutes": False})
    other = call(booted, "scribe_import_file", {"path": str(source), "kind": "meeting", "wait_s": 30})["session"]["id"]
    time.sleep(0.3)
    assert booted.audio.minutes.get(other) is None and len(booted.link.calls) == 1
    # re-transcribing replaces the text, so the old minutes are dropped (and rewritten when the setting is on)
    booted.audio.settings.update({"auto_minutes": True})
    booted.link.replies.append({**MINUTES, "action_items": []})
    assert booted.client.post(f"/audio/api/sessions/{sid}/retranscribe").status_code == 200
    wait_for(lambda: booted.audio.minutes.get(sid) and booted.audio.minutes.get(sid)["action_items"] == [])
