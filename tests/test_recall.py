"""recall/recall_search: merging Funes's own spans with fake Argus/Echo/Scribe
sources over an in-process httpx.MockTransport (grant/deny/timeout), citation
format, unavailable-source reporting and the REST/agent surface."""
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from funes_hoard.api import create_app
from funes_hoard.db import Database
from funes_hoard.errors import BadInput
from funes_hoard.recall import recall, recall_search, sources_status
from funes_hoard.sources import SourceRegistry

NOW = 1_790_000_000.0  # fixed instant, well clear of any date-word ambiguity


def _insert_span(db: Database, start_ts: float, end_ts: float, app: str, title: str, project=None) -> int:
    return db.execute(
        "INSERT INTO spans(start_ts, end_ts, kind, app, exe, title, category, project) VALUES (?, ?, 'active', ?, ?, ?, 'Coding', ?)",
        (start_ts, end_ts, app, app.lower(), title, project),
    )


@pytest.fixture()
def db(tmp_path):
    database = Database(tmp_path / "data")
    _insert_span(database, NOW - 300, NOW - 60, "Code", "funes: recall.py - Visual Studio Code", project="funes")
    yield database
    database.close()


@pytest.fixture()
def registry(tmp_path):
    reg = SourceRegistry(tmp_path / "data")
    token_dir = tmp_path / "tokens"
    token_dir.mkdir()
    for sid in ("argus", "echo", "scribe"):
        token_file = token_dir / f"{sid}-token"
        token_file.write_text(f"{sid}-token-value", encoding="utf-8")
        reg.save_patch(sid, {"base_url": f"http://{sid}.test", "token_path": str(token_file)})
    return reg


def _handler_for(argus_frames=None, echo_clips=None, scribe_sessions=None, scribe_transcript=None,
                  argus_hits=None, echo_hits=None, scribe_search_sessions=None, deny=(), timeout=()):
    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host in timeout:
            raise httpx.TimeoutException("slow", request=request)
        if request.url.path == "/api/health":
            if host in deny:
                return httpx.Response(200, json={"service": host, "status": "ok"})
            return httpx.Response(200, json={"service": host.split(".")[0], "status": "ok"})
        assert request.url.path == "/api/agent/call"
        if host in deny:
            return httpx.Response(401, json={"error": "unauthorized"})
        import json as _json

        body = _json.loads(request.content)
        name = body["name"]
        if name == "screen_timeline":
            return httpx.Response(200, json={"frames": argus_frames or []})
        if name == "screen_search":
            return httpx.Response(200, json={"hits": argus_hits or []})
        if name == "clip_recent":
            return httpx.Response(200, json={"clips": echo_clips or []})
        if name == "clip_search":
            return httpx.Response(200, json={"hits": echo_hits or []})
        if name == "scribe_sessions":
            return httpx.Response(200, json={"sessions": scribe_sessions or []})
        if name == "scribe_transcript":
            return httpx.Response(200, json=scribe_transcript or {"segments": []})
        if name == "scribe_search":
            return httpx.Response(200, json={"sessions": scribe_search_sessions or []})
        return httpx.Response(404, json={"error": "unknown tool"})

    return handler


# -------------------------------------------------------------------- at --
def test_recall_includes_funes_own_episodes(db, registry):
    handler = _handler_for(deny=("argus.test", "echo.test", "scribe.test"))
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = _run(recall(db, registry, at="", window_minutes=15, now=NOW, http_client=client))
    funes_items = [i for i in result["items"] if i["source"] == "funes"]
    assert len(funes_items) == 1
    assert funes_items[0]["citation"].startswith("[funes:episode ")
    assert "recall.py" in funes_items[0]["text"]


def test_recall_merges_and_time_sorts_across_sources(db, registry):
    from funes_hoard.timeparse import iso_local

    argus_time = iso_local(NOW - 300)
    argus_hhmm = argus_time[11:16]
    session_start_ts = NOW - 600  # started 10 min ago, still running: overlaps the window
    handler = _handler_for(
        argus_frames=[{"id": 88, "time": argus_time, "app": "chrome", "window_title": "Weather", "excerpt": "22C"}],
        echo_clips=[{"id": 512, "kind": "text", "preview": "copied address", "last_seen_at": NOW - 120}],
        scribe_sessions=[{"id": "s1", "title": "Standup", "started_at": iso_local(session_start_ts), "duration_s": 1800, "status": "done"}],
        scribe_transcript={"segments": [{"start_s": 600, "end_s": 605, "speaker": "yo", "text": "hello there"}]},
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = _run(recall(db, registry, at=None, window_minutes=60, now=NOW, http_client=client))
    sources_seen = {i["source"] for i in result["items"]}
    assert sources_seen == {"funes", "argus", "echo", "scribe"}
    times = [i["time"] for i in result["items"]]
    assert times == sorted(times, reverse=True)
    citations = {i["source"]: i["citation"] for i in result["items"]}
    assert citations["argus"] == f"[argus:moment 88 {argus_hhmm}]"
    assert citations["echo"] == "[echo:clip 512]"
    assert citations["scribe"].startswith("[scribe:seg 1 ")
    assert result["summary"]["counts"] == {"funes": 1, "argus": 1, "echo": 1, "scribe": 1}
    assert result["summary"]["unavailable"] == []


def test_recall_reports_unavailable_sources_without_raising(db, registry):
    handler = _handler_for(deny=("echo.test",), timeout=("scribe.test",))
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = _run(recall(db, registry, at=None, now=NOW, http_client=client))
    reasons = {u["id"]: u["reason"] for u in result["summary"]["unavailable"]}
    assert reasons["echo"] == "unauthorized"
    assert reasons["scribe"] == "timeout"
    assert "argus" not in reasons


def test_recall_disabled_source_is_reported_not_called(db, registry, tmp_path):
    registry.save_patch("argus", {"enabled": False})
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        return httpx.Response(200, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = _run(recall(db, registry, at=None, now=NOW, http_client=client))
    assert "argus.test" not in calls
    reasons = {u["id"]: u["reason"] for u in result["summary"]["unavailable"]}
    assert reasons["argus"] == "disabled"


def test_recall_respects_an_explicit_source_subset(db, registry):
    handler = _handler_for(echo_clips=[{"id": 1, "kind": "text", "preview": "x", "last_seen_at": NOW}])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = _run(recall(db, registry, at=None, sources=["echo"], now=NOW, http_client=client))
    assert set(result["summary"]["counts"]) == {"funes", "echo"}


def test_recall_unknown_source_id_is_reported(db, registry):
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    result = _run(recall(db, registry, at=None, sources=["nope"], now=NOW, http_client=client))
    reasons = {u["id"]: u["reason"] for u in result["summary"]["unavailable"]}
    assert reasons["nope"] == "unknown_source"


def test_recall_parses_time_words(db, registry):
    handler = _handler_for(deny=("argus.test", "echo.test", "scribe.test"))
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = _run(recall(db, registry, at="-2m", window_minutes=5, now=NOW, http_client=client))
    assert result["at"] is not None
    with pytest.raises(BadInput):
        _run(recall(db, registry, at="not a real time!!", now=NOW, http_client=client))


# -------------------------------------------------------------- search --
def test_recall_search_requires_a_query(db, registry):
    with pytest.raises(BadInput):
        _run(recall_search(db, registry, "   ", now=NOW))


def test_recall_search_merges_funes_and_sources(db, registry):
    handler = _handler_for(
        argus_hits=[{"id": 3, "time": "2026-09-24T09:00:00+02:00", "app": "chrome", "window_title": "Invoice", "snippet": "total due"}],
        echo_hits=[{"id": 7, "kind": "text", "preview": "invoice #1", "last_seen_at": NOW - 500}],
        scribe_search_sessions=[{
            "session": {"id": "s2", "title": "Client call", "started_at": "2026-09-24T09:30:00+02:00"},
            "hits": [{"segment_id": 17, "start_s": 34, "speaker": "otros", "snippet": "about the invoice"}],
        }],
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = _run(recall_search(db, registry, "invoice", now=NOW, http_client=client))
    citations = {i["source"]: i["citation"] for i in result["items"]}
    assert citations["argus"] == "[argus:moment 3 09:00]"
    assert citations["echo"] == "[echo:clip 7]"
    assert citations["scribe"].startswith("[scribe:seg 17 ")


# -------------------------------------------------------- sources_status --
def test_sources_status_reports_each_source(registry):
    handler = _handler_for(deny=("scribe.test",))
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = _run(sources_status(registry, http_client=client))
    by_id = {s["id"]: s for s in result["sources"]}
    assert by_id["argus"]["ok"] is True
    assert by_id["scribe"]["ok"] is True  # health endpoint itself is 200 even when calls are denied


def _run(coro):
    import asyncio

    return asyncio.run(coro)


# ------------------------------------------------------------- REST/API --
@pytest.fixture()
def api_client(demo_data_dir):
    app = create_app(demo_data_dir, None, demo=True, port=18844)
    with TestClient(app, base_url="http://127.0.0.1:18844") as c:
        yield c


def test_api_recall_endpoint_never_fails_when_sources_are_down(api_client, monkeypatch):
    # Closed ports, whatever is running on the developer's machine.
    monkeypatch.setenv("FUNES_SOURCES", json.dumps([{"id": i, "base_url": "http://127.0.0.1:1"} for i in ("argus", "echo", "scribe")]))
    r = api_client.get("/api/recall", params={"window": 30})
    assert r.status_code == 200
    body = r.json()
    assert "items" in body and "summary" in body
    assert body["summary"]["unavailable"]  # nothing is really running on those ports


def test_api_recall_search_endpoint(api_client):
    r = api_client.get("/api/recall/search", params={"query": "Episodic"})
    assert r.status_code == 200
    assert any(i["source"] == "funes" for i in r.json()["items"])


def test_api_sources_list_and_patch(api_client):
    r = api_client.get("/api/sources")
    assert r.status_code == 200
    assert {s["id"] for s in r.json()["items"]} == {"argus", "echo", "scribe"}

    r = api_client.put("/api/sources/argus", json={"enabled": False})
    assert r.status_code == 200
    assert r.json()["enabled"] is False
    assert api_client.get("/api/sources").json()["items"][0]["enabled"] is False or any(
        s["id"] == "argus" and s["enabled"] is False for s in api_client.get("/api/sources").json()["items"]
    )


def test_api_sources_health(api_client):
    r = api_client.get("/api/sources/health")
    assert r.status_code == 200
    assert {s["id"] for s in r.json()["sources"]} == {"argus", "echo", "scribe"}


def test_agent_recall_tool_is_audited(api_client):
    r = api_client.post("/api/agent/recall", json={"window_minutes": 10})
    assert r.status_code == 200
    calls = api_client.get("/api/agent-calls").json()["items"]
    assert calls[0]["tool"] == "recall"


def test_agent_sources_status_tool(api_client):
    r = api_client.post("/api/agent/sources_status", json={})
    assert r.status_code == 200
    assert "sources" in r.json()


def test_agent_recall_bad_time_is_a_clear_400(api_client):
    r = api_client.post("/api/agent/recall", json={"at": "not a real time!!"})
    assert r.status_code == 400
    assert r.json()["error"] == "bad_time"
