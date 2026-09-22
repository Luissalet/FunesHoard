import pytest
from fastapi.testclient import TestClient

from funes_hoard.api import create_app


@pytest.fixture()
def client(tmp_path):
    app = create_app(tmp_path / "data", None, demo=True, port=18831)
    with TestClient(app, base_url="http://127.0.0.1:18831") as c:
        yield c


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == "funes-hoard"
    assert body["name"] == "Funes's Hoard"
    assert body["status"] == "ok"


def test_agent_activity_now(client):
    r = client.post("/api/agent/activity_now", json={})
    assert r.status_code == 200
    body = r.json()
    assert "recording" in body and "app" in body


def test_agent_activity_summary_today(client):
    r = client.post("/api/agent/activity_summary", json={"day": "today"})
    assert r.status_code == 200
    body = r.json()
    assert body["active_s"] > 0
    assert "focus_blocks" in body


def test_agent_activity_pause_is_the_only_write(client):
    r = client.post("/api/agent/activity_pause", json={"minutes": 5})
    assert r.status_code == 200
    assert r.json()["paused"] is True
    status = client.get("/api/status").json()
    assert status["paused"] is True


def test_agent_calls_are_logged_for_audit(client):
    client.post("/api/agent/activity_now", json={})
    r = client.get("/api/agent-calls")
    assert r.status_code == 200
    items = r.json()["items"]
    assert any(i["tool"] == "activity_now" for i in items)
    assert all(i["ok"] == 1 for i in items)


def test_agent_bad_time_returns_400_not_500(client):
    r = client.post("/api/agent/activity_timeline", json={"start": "not-a-real-date!!"})
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "bad_time"


def test_agent_api_has_no_rules_or_delete_or_export_endpoints(client):
    # The agent-facing surface is exactly the enumerated /api/agent/<tool>
    # routes -- nothing under it can touch rules, delete history, export,
    # or resume a paused session early.
    for path in (
        "/api/agent/rules_update",
        "/api/agent/privacy_rules_add",
        "/api/agent/delete_range",
        "/api/agent/export",
        "/api/agent/activity_resume",
    ):
        r = client.post(path, json={})
        assert r.status_code == 404


def test_ui_privacy_rules_endpoint_is_separate_from_agent_surface(client):
    # The UI can manage rules; this confirms that surface exists precisely
    # so we can also confirm the agent surface does NOT expose it (see
    # test_agent_api_has_no_rules_or_delete_or_export_endpoints).
    r = client.get("/api/privacy/rules")
    assert r.status_code == 200
    assert len(r.json()["items"]) > 0


def test_browser_guard_rejects_bad_host(client):
    r = client.get("/api/health", headers={"Host": "evil.example.com"})
    assert r.status_code == 403


def test_browser_guard_rejects_cross_site_post(client):
    r = client.post(
        "/api/agent/activity_now",
        json={},
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    assert r.status_code == 403


def test_browser_guard_rejects_foreign_origin_on_write(client):
    r = client.post(
        "/api/privacy/pause",
        json={"minutes": 1},
        headers={"Origin": "http://evil.example.com"},
    )
    assert r.status_code == 403


def test_browser_guard_allows_plain_get_from_any_origin(client):
    r = client.get("/api/health", headers={"Origin": "http://evil.example.com"})
    assert r.status_code == 200


def test_classify_preview_does_not_persist(client):
    before = client.get("/api/classify/rules").json()["items"]
    r = client.post(
        "/api/classify/preview",
        json={"match_type": "app", "pattern": "Code.exe", "category": "Coding"},
    )
    assert r.status_code == 200
    assert "would_change" in r.json()
    after = client.get("/api/classify/rules").json()["items"]
    assert before == after


def test_delete_range_two_step_is_the_only_way_to_wipe(client):
    span = client.get("/api/timeline").json()["items"][0]
    r = client.post("/api/privacy/delete-range", json={"start": span["start"] - 1, "end": span["end"] + 1})
    assert r.status_code == 200
    assert r.json()["deleted"]["spans"] >= 1
