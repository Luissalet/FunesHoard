"""MCP tool descriptions stay short: hosts pay for every token of them on every turn.

750 characters is roughly 250 tokens with the conservative chars/3 estimate hosts budget with.
Argument details belong in the parameter descriptions of the input schema instead.
"""
import asyncio

import pytest

from funes_hoard import mcp_server

MAX_DESCRIPTION_CHARS = 750

TOOLS = asyncio.run(mcp_server.mcp.list_tools())

# Tools whose long details moved into parameter descriptions: same arguments as before.
EXPECTED_PROPERTIES = {
    "recall": {"at", "window_minutes", "sources", "limit_per_source", "include_dropped"},
    "recall_search": {"query", "since", "until", "sources", "limit_per_source", "include_dropped"},
    "activity_timeline": {"start", "end", "min_minutes", "limit", "offset", "around"},
    "activity_where_was_i": {"before", "contexts", "all_categories"},
    "activity_search": {"query", "since", "until", "limit"},
    "activity_summary": {"day", "start", "end", "group_by"},
    "scribe_minutes": {"session_id", "regenerate"},
}


@pytest.mark.parametrize("tool", TOOLS, ids=lambda t: t.name)
def test_description_is_capped(tool):
    assert tool.description, tool.name
    assert len(tool.description) <= MAX_DESCRIPTION_CHARS, (tool.name, len(tool.description))


@pytest.mark.parametrize("name,expected", sorted(EXPECTED_PROPERTIES.items()))
def test_trimmed_tools_keep_their_arguments(name, expected):
    tool = next(t for t in TOOLS if t.name == name)
    props = tool.inputSchema.get("properties", {})
    assert set(props) == expected
    described = [p for p, spec in props.items() if spec.get("description")]
    assert described, f"{name}: argument details should live in the parameter descriptions"
