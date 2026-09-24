"""Proof that the MCP adapter works: spawn `mcp_server.py` as a real
subprocess over stdio (as Faustus would) against a real running app, and
drive it through the MCP client protocol -- not by importing the adapter's
functions directly.
"""
import json
import socket
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from funes_hoard.api import create_app

REPO_ROOT = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def live_app(demo_data_dir, monkeypatch):
    # Hermetic: on the developer's machine the sibling apps ARE running (and
    # their token files exist), so point the federated sources at a closed port.
    import json as _json
    monkeypatch.setenv("FUNES_SOURCES", _json.dumps([{"id": i, "base_url": "http://127.0.0.1:1"} for i in ("argus", "echo", "scribe")]))
    monkeypatch.setenv("HOARD_HUB_URL", "http://127.0.0.1:1")  # and no hub proxy to fall back to
    port = _free_port()
    app = create_app(demo_data_dir, None, demo=True, port=port)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    import httpx

    while time.time() < deadline:
        try:
            r = httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=1)
            if r.status_code == 200:
                break
        except Exception:
            time.sleep(0.1)
    else:
        raise RuntimeError("app did not become healthy in time")
    yield port
    server.should_exit = True
    thread.join(timeout=10)


@pytest.mark.asyncio
async def test_mcp_adapter_lists_and_calls_tools_over_stdio(live_app):
    port = live_app
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(REPO_ROOT / "funes_hoard" / "mcp_server.py")],
        env={"FUNES_URL": f"http://127.0.0.1:{port}"},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {t.name for t in tools.tools}
            assert names == {
                "activity_now", "activity_where_was_i", "activity_timeline",
                "activity_summary", "activity_search", "activity_recent_files",
                "activity_projects", "activity_pause",
                "recall", "recall_search", "sources_status",
            }
            pause_tool = next(t for t in tools.tools if t.name == "activity_pause")
            assert pause_tool.annotations.readOnlyHint is False
            now_tool = next(t for t in tools.tools if t.name == "activity_now")
            assert now_tool.annotations.readOnlyHint is True

            result = await session.call_tool("activity_summary", {"day": "today"})
            assert result.isError is not True
            text = result.content[0].text
            assert "active_s" in text or "by_category" in text

            result2 = await session.call_tool("activity_where_was_i", {"contexts": 2, "before": "ayer"})
            assert result2.isError is not True
            payload = json.loads(result2.content[0].text)
            assert 1 <= len(payload["contexts"]) <= 2
            assert payload["contexts"][0]["title"]
            assert "+" in payload["before"] or "-" in payload["before"][19:]  # local ISO with offset

            for tool in tools.tools:
                assert "Keywords:" in (tool.description or ""), tool.name
                assert tool.annotations is not None and tool.annotations.openWorldHint is False
                assert tool.annotations.destructiveHint is False

            hits = await session.call_tool("activity_search", {"query": "funes-hoard OR \"", "limit": 3})
            assert hits.isError is not True

            # A8: a hit's ts chains straight into the timeline around it.
            atlas = json.loads((await session.call_tool("activity_search", {"query": "Atlas", "limit": 1})).content[0].text)
            around = await session.call_tool("activity_timeline", {"around": atlas["items"][0]["ts"]})
            assert around.isError is not True
            assert json.loads(around.content[0].text)["items"]

            bad = await session.call_tool("activity_timeline", {"start": "next blursday"})
            assert bad.isError is True
            assert "bad_time: cannot parse time" in bad.content[0].text
            assert "yesterday/ayer" in bad.content[0].text

            paused = await session.call_tool("activity_pause", {"minutes": 30})
            assert json.loads(paused.content[0].text)["paused"] is True
            again = await session.call_tool("activity_pause", {"minutes": 5})
            assert "already paused" in json.loads(again.content[0].text)["note"]

            # Federated tools: never fail even though no sibling app is running.
            merged = await session.call_tool("recall", {"window_minutes": 30})
            assert merged.isError is not True
            merged_payload = json.loads(merged.content[0].text)
            assert "items" in merged_payload and "summary" in merged_payload
            assert merged_payload["summary"]["unavailable"]

            status = await session.call_tool("sources_status", {})
            assert status.isError is not True
            assert {s["id"] for s in json.loads(status.content[0].text)["sources"]} == {"argus", "echo", "scribe"}

    import httpx

    calls = httpx.get(f"http://127.0.0.1:{port}/api/agent-calls").json()["items"]
    assert {"activity_summary", "activity_where_was_i", "activity_pause"} <= {c["tool"] for c in calls}


@pytest.mark.asyncio
async def test_mcp_adapter_does_not_log_every_call_to_stderr(live_app, tmp_path):
    # C7: the mcp SDK logs "Processing request of type ..." at INFO for
    # every call by default -- the host only needs real problems there.
    port = live_app
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(REPO_ROOT / "funes_hoard" / "mcp_server.py")],
        env={"FUNES_URL": f"http://127.0.0.1:{port}"},
    )
    errlog_path = tmp_path / "stderr.log"
    with open(errlog_path, "w", encoding="utf-8") as errlog:
        async with stdio_client(params, errlog=errlog) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                await session.list_tools()
                await session.call_tool("activity_now", {})
                await session.call_tool("activity_summary", {"day": "today"})
    stderr_text = errlog_path.read_text(encoding="utf-8")
    assert "Processing request of type" not in stderr_text


@pytest.mark.asyncio
async def test_mcp_adapter_raises_tool_error_when_app_not_running():
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(REPO_ROOT / "funes_hoard" / "mcp_server.py")],
        env={"FUNES_URL": "http://127.0.0.1:18839"},  # nothing listening here
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("activity_now", {})
            assert result.isError is True
            assert "not running" in result.content[0].text
