"""Proof that the MCP adapter works: spawn `mcp_server.py` as a real
subprocess over stdio (as Faustus would) against a real running app, and
drive it through the MCP client protocol -- not by importing the adapter's
functions directly.
"""
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
def live_app(tmp_path):
    port = _free_port()
    app = create_app(tmp_path / "data", None, demo=True, port=port)
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
            }
            pause_tool = next(t for t in tools.tools if t.name == "activity_pause")
            assert pause_tool.annotations.readOnlyHint is False
            now_tool = next(t for t in tools.tools if t.name == "activity_now")
            assert now_tool.annotations.readOnlyHint is True

            result = await session.call_tool("activity_summary", {"day": "today"})
            assert result.isError is not True
            text = result.content[0].text
            assert "active_s" in text or "by_category" in text

            result2 = await session.call_tool("activity_where_was_i", {"contexts": 2})
            assert result2.isError is not True


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
