"""MCP Apps card: paid tools and credits_balance point to a self-contained HTML resource."""

from mcp.client import Client

from norma43_mcp import apps, server


async def test_card_resource_and_tool_meta():
    async with Client(server.mcp) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
        uri = apps.uri("norma43")
        ui = {n for n, t in tools.items() if (t.meta or {}).get("ui", {}).get("resourceUri") == uri}
        assert (set(server.PRICES) & set(tools)) | {"credits_balance"} <= ui
        assert "pricing" not in ui
        res = await c.read_resource(uri)
    page = res.contents[0]
    assert page.mime_type == apps.MIME
    assert "ui/initialize" in page.text and "__CFG__" not in page.text
    assert "<script src" not in page.text and "https://fonts" not in page.text


def test_config_cannot_break_out_of_script():
    page = apps.html(slug="x", name="</script><b>", icon="nope", version="0", labels={"a": "</script>"})
    assert page.count("</script>") == 1
