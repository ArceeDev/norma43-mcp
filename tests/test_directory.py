"""Claude Connectors Directory: every tool declares a title and readOnlyHint or destructiveHint (policy 5E)."""
from __future__ import annotations

from norma43_mcp import server


async def test_every_tool_has_title_and_hint():
    tools = await server.mcp.list_tools()
    assert tools
    for t in tools:
        assert t.title and len(t.title) <= 60, t.name
        assert t.annotations and (t.annotations.read_only_hint is not None
                                  or t.annotations.destructive_hint is not None), t.name
    assert len({t.title for t in tools}) == len(tools)
