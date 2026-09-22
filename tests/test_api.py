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
    # "yesterday" always holds a full synthetic day, whatever time the suite runs
    r = client.post("/api/agent/activity_summary", json={"day": "yesterday"})
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
    assert r.json()["error"] == "bad_time"


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


# --- agent output shape (what a small local model actually reads) --------
import re as _re

ISO_RE = _re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$")


def test_agent_times_are_local_iso_with_offset(client):
    body = client.post("/api/agent/activity_timeline", json={"start": "-3d", "limit": 3}).json()
    assert ISO_RE.match(body["start"]) and ISO_RE.match(body["items"][0]["start"])
    assert "human" in body["items"][0]
    # the UI keeps epoch seconds
    ui = client.get("/api/timeline", params={"day": "today", "limit": 3}).json()
    assert isinstance(ui["start"], float)


def test_agent_summary_group_by_is_honoured_and_validated(client):
    by_project = client.post("/api/agent/activity_summary", json={"day": "ayer", "group_by": "project"}).json()
    assert "by_project" in by_project and "by_category" not in by_project and "by_app" not in by_project
    assert by_project["active_human"].endswith("min")
    bad = client.post("/api/agent/activity_summary", json={"group_by": "colour"})
    assert bad.status_code == 400 and bad.json()["error"] == "bad_group_by"
    ui = client.get("/api/summary", params={"day": "today"}).json()
    assert {"by_category", "by_app", "by_project"} <= set(ui)


def test_agent_timeline_offset_paginates(client):
    first = client.post("/api/agent/activity_timeline", json={"start": "-3d", "limit": 2, "min_minutes": 0}).json()
    assert first["has_more"] and first["next_offset"] == 2
    second = client.post("/api/agent/activity_timeline", json={"start": "-3d", "limit": 2, "min_minutes": 0, "offset": 2}).json()
    assert {i["id"] for i in first["items"]}.isdisjoint({i["id"] for i in second["items"]})


def test_ui_timeline_for_yesterday_is_exactly_yesterday(client):
    from datetime import datetime, timedelta

    body = client.get("/api/timeline", params={"day": "yesterday", "min_minutes": 0}).json()
    start = datetime.fromtimestamp(body["start"])
    assert start.time().hour == 0 and body["end"] - body["start"] in (82800, 86400, 90000)
    assert start.date() == datetime.now().date() - timedelta(days=1)
    assert all(body["start"] <= i["start"] <= i["end"] <= body["end"] for i in body["items"])


def test_agent_where_was_i_has_titles_and_distinct_contexts(client):
    body = client.post("/api/agent/activity_where_was_i", json={"before": "yesterday", "contexts": 5}).json()
    keys = [c["project"] or c["app"] for c in body["contexts"]]
    assert len(keys) == len(set(keys)) and keys
    assert all(c["title"] for c in body["contexts"])


def test_agent_search_since_a_day_word_means_from_its_midnight(client):
    body = client.post("/api/agent/activity_search", json={"query": "Visual Studio Code", "since": "yesterday", "limit": 50}).json()
    assert body["items"], "since=yesterday must mean from yesterday 00:00, not from 24 h ago this second"
    assert all(i["when"].startswith(("today", "yesterday")) for i in body["items"])
    assert any(i["when"].startswith("yesterday 09:") for i in body["items"])


def test_activity_now_reports_nothing_as_current_while_paused(client):
    client.post("/api/privacy/pause", json={"minutes": 30})
    body = client.post("/api/agent/activity_now", json={}).json()
    assert body["paused"] is True and body["app"] is None and ISO_RE.match(body["paused_until"])


def test_commit_authors_round_trip(client):
    r = client.put("/api/commit-authors", json={"authors": ["Luissalet", " alex@example.com "]})
    assert r.json()["authors"] == ["Luissalet", "alex@example.com"]
    assert client.get("/api/commit-authors").json()["authors"] == ["Luissalet", "alex@example.com"]


def test_ui_search_uses_markers_that_cannot_collide_with_title_brackets(client):
    items = client.get("/api/search", params={"query": "DuckDB"}).json()["items"]
    assert items and "\u0002DuckDB\u0003" in items[0]["text"]
    agent = client.post("/api/agent/activity_search", json={"query": "DuckDB"}).json()["items"]
    assert "[DuckDB]" in agent[0]["text"]


def test_ui_recent_commits(client):
    items = client.get("/api/commits", params={"since": "-7d"}).json()["items"]
    assert items and {"repo", "sha", "subject"} <= set(items[0])
