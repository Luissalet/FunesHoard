"""Browser-attack guard, static-file containment and agent-surface limits."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from funes_hoard.api import create_app

PORT = 18832


@pytest.fixture()
def static_client(tmp_path, demo_data_dir):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>index</html>", encoding="utf-8")
    (dist / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET", encoding="utf-8")
    app = create_app(demo_data_dir, dist, demo=True, port=PORT)
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as c:
        yield c, secret


@pytest.mark.parametrize(
    "path_template",
    [
        "/%2F{abs}",  # decodes to an absolute path
        "/..%2Fsecret.txt",
        "/%2e%2e/secret.txt",
        "/assets/..%2F..%2Fsecret.txt",
    ],
)
def test_spa_fallback_never_serves_files_outside_dist(static_client, path_template):
    client, secret = static_client
    path = path_template.format(abs=str(secret).lstrip("/"))
    r = client.get(path)
    assert "TOP-SECRET" not in r.text


def test_spa_serves_real_files_and_falls_back_to_index(static_client):
    client, _ = static_client
    assert client.get("/favicon.svg").text == "<svg/>"
    assert "index" in client.get("/today").text


def test_unknown_api_path_is_json_404_not_the_spa(static_client):
    client, _ = static_client
    r = client.get("/api/does-not-exist")
    assert r.status_code == 404


@pytest.fixture()
def client(demo_data_dir):
    app = create_app(demo_data_dir, None, demo=True, port=PORT)
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as c:
        yield c


def test_guard_rejects_loopback_host_on_another_port(client):
    r = client.get("/api/health", headers={"Host": f"127.0.0.1:{PORT + 1}"})
    assert r.status_code == 403


def test_guard_accepts_localhost_on_own_port(client):
    r = client.get("/api/health", headers={"Host": f"localhost:{PORT}"})
    assert r.status_code == 200


def test_guard_rejects_origin_of_another_local_port(client):
    # Another web app on this machine (another port) must not be able to
    # drive this app's write endpoints.
    r = client.post("/api/privacy/resume", headers={"Origin": f"http://127.0.0.1:{PORT + 5}"})
    assert r.status_code == 403


def test_guard_accepts_own_origin_on_write(client):
    r = client.post("/api/privacy/resume", headers={"Origin": f"http://localhost:{PORT}"})
    assert r.status_code == 200


def test_guard_rejects_null_origin_on_write(client):
    r = client.post("/api/privacy/resume", headers={"Origin": "null"})
    assert r.status_code == 403


def test_agent_surface_is_exactly_the_eleven_tools(client):
    agent_paths = sorted(
        {getattr(r, "path", "") for r in client.app.routes if getattr(r, "path", "").startswith("/api/agent/")}
    )
    assert agent_paths == sorted(
        f"/api/agent/{t}" for t in (
            "activity_now", "activity_where_was_i", "activity_timeline", "activity_summary",
            "activity_search", "activity_recent_files", "activity_projects", "activity_pause",
            "recall", "recall_search", "sources_status",
        )
    )


def test_agent_pause_can_never_shorten_a_human_pause(client):
    client.post("/api/privacy/pause-until-resumed")
    r = client.post("/api/agent/activity_pause", json={"minutes": 1})
    assert r.status_code == 200
    status = client.get("/api/status").json()
    assert status["paused"] is True
    assert status["paused_until"] is None  # still "until resumed"


def test_agent_pause_only_extends_a_timed_pause(client):
    long = client.post("/api/privacy/pause", json={"minutes": 60}).json()["until"]
    short = client.post("/api/agent/activity_pause", json={"minutes": 5}).json()
    assert client.get("/api/status").json()["paused_until"] == pytest.approx(long)
    assert "already paused" in short["note"]


def test_agent_errors_are_top_level_error_and_message(client):
    r = client.post("/api/agent/activity_timeline", json={"start": "not-a-real-date!!"})
    assert r.status_code == 400
    body = r.json()
    assert body["error"] == "bad_time"
    assert "today" in body["message"]  # tells the model what it can pass instead


def test_agent_validation_error_is_actionable_400_and_audited(client):
    r = client.post("/api/agent/activity_pause", json={"minutes": 99999})
    assert r.status_code == 400
    body = r.json()
    assert body["error"] == "bad_arguments"
    assert "minutes" in body["message"]
    calls = client.get("/api/agent-calls").json()["items"]
    assert calls[0]["tool"] == "activity_pause" and calls[0]["ok"] == 0


def test_invalid_privacy_rule_is_rejected_not_silently_ignored(client):
    r = client.post("/api/privacy/rules", json={"kind": "exclude", "match_type": "title", "pattern": "x"})
    assert r.status_code == 400
    r = client.post("/api/privacy/rules", json={"kind": "exclude", "match_type": "title_regex", "pattern": "(unclosed"})
    assert r.status_code == 400
    assert r.json()["error"] == "bad_pattern"


def test_invalid_classify_rule_is_rejected(client):
    r = client.post("/api/classify/rules", json={"match_type": "app", "pattern": "x", "category": "Nonsense"})
    assert r.status_code == 400
    r = client.post("/api/classify/rules", json={"match_type": "title_regex", "pattern": "[", "category": "Coding"})
    assert r.status_code == 400


def test_search_survives_fts_syntax_characters(client):
    for q in ('funes-hoard', 'C++', '"unbalanced', 'AND', 'a OR', 'NEAR(', 'title:x', "DuckDB?", "contraseña'--"):
        r = client.post("/api/agent/activity_search", json={"query": q})
        assert r.status_code == 200, (q, r.text)
    # punctuation only: a clear 400, never a 500
    assert client.post("/api/agent/activity_search", json={"query": "*"}).status_code == 400


def test_search_hyphenated_query_finds_the_title(client):
    body = client.post("/api/agent/activity_search", json={"query": "Episodic memory"}).json()
    assert any("Episodic" in i["text"] for i in body["items"])


def test_search_with_empty_query_is_a_clear_400(client):
    r = client.post("/api/agent/activity_search", json={"query": "   "})
    assert r.status_code == 400
    assert r.json()["error"] == "empty_query"


def test_deleted_range_is_no_longer_searchable(client):
    before = client.post("/api/agent/activity_search", json={"query": "DuckDB", "limit": 50}).json()
    assert before["items"], "demo data should contain a DuckDB page"
    client.post("/api/privacy/delete-range", json={"start": 0, "end": 4_000_000_000})
    after = client.post("/api/agent/activity_search", json={"query": "DuckDB", "limit": 50}).json()
    assert after["items"] == []
