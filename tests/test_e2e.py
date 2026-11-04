"""MCP end to end: tools over the protocol, debits, no-charge on bad input, refunds, paywall, hosted licenses."""

from __future__ import annotations

import base64
import importlib
import json

import pytest
from mcp.client import Client
from mcp.shared.exceptions import MCPError

from conftest import PUB
from n43_builder import sample_statement
from test_core import EXPECTED


@pytest.fixture(scope="module")
def license_(gateway):
    return gateway["issue"]("buyer@acme.test")


def _env(monkeypatch, gateway, key):
    monkeypatch.setenv("KEYFLINT_LICENSE", key)
    monkeypatch.setenv("KEYFLINT_PUBLIC_KEYS", PUB)
    monkeypatch.setenv("KEYFLINT_URL", gateway["url"])
    monkeypatch.setenv("KEYFLINT_CHECKOUT_URL", "https://example.com/buy")
    monkeypatch.setenv("KEYFLINT_CLOCK_STATE", "off")
    monkeypatch.delenv("NORMA43_TRANSPORT", raising=False)
    monkeypatch.delenv("NORMA43_PRICES", raising=False)


@pytest.fixture()
def server(gateway, license_, monkeypatch):
    _env(monkeypatch, gateway, license_["license_key"])
    import norma43_mcp.server as srv

    return importlib.reload(srv)


async def call(srv, name, **args):
    async with Client(srv.mcp) as c:
        res = await c.call_tool(name, args)
    text = res.content[0].text if res.content else ""
    data = res.structured_content or (json.loads(text) if text.startswith("{") else None)
    if isinstance(data, dict) and set(data) == {"result"}:
        data = data["result"]
    return res.is_error, data, text


async def balance(srv):
    _, data, _ = await call(srv, "credits_balance")
    return data["balance"]


async def test_tools_are_listed_with_annotations(server):
    async with Client(server.mcp) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
    assert {"parse_norma43", "norma43_to_csv", "reconcile_movements", "validate_identifier", "sepa_creditor_id",
            "pricing", "credits_balance"} <= set(tools)
    ann = tools["parse_norma43"].annotations
    assert ann.read_only_hint and not ann.open_world_hint and ann.idempotent_hint


async def test_parse_debits_one_credit(server):
    before = await balance(server)
    err, data, _ = await call(server, "parse_norma43", content=sample_statement())
    assert not err, data
    assert data["valid"] and data["accounts"][0]["closing_balance"] == "3922.20"
    assert data["billing"]["credits_charged"] == 1 and await balance(server) == before - 1


async def test_parse_base64_bytes(server):
    raw = sample_statement().encode("cp850")
    err, data, _ = await call(server, "parse_norma43", content_base64=base64.b64encode(raw).decode(),
                              include_movements=False)
    assert not err and "movements" not in data["accounts"][0] and data["summary"]["movements"] == 8


async def test_garbage_is_never_charged(server):
    before = await balance(server)
    err, _, text = await call(server, "parse_norma43", content="hola, esto no es un extracto")
    assert err and "Norma 43" in text
    err, _, text = await call(server, "reconcile_movements", expected=[{"id": "x", "date": "mal", "amount": 1}],
                              content=sample_statement())
    assert err and "not a date" in text
    err, _, _ = await call(server, "reconcile_movements", expected=EXPECTED, content=sample_statement(),
                           max_group_size=9)
    assert err
    assert await balance(server) == before


async def test_csv_tool(server):
    err, data, _ = await call(server, "norma43_to_csv", content=sample_statement())
    assert not err and data["rows"] == 8 and data["csv"].startswith("iban;titular;id;")


async def test_reconcile_tool_costs_three(server):
    before = await balance(server)
    err, data, _ = await call(server, "reconcile_movements", expected=EXPECTED, content=sample_statement())
    assert not err, data
    assert data["summary"]["matched_expected"] == 6 and data["bank_source"] == "norma43"
    assert data["billing"]["credits_charged"] == 3 and await balance(server) == before - 3


async def test_reconcile_with_movement_list(server):
    err, data, _ = await call(server, "reconcile_movements",
                              movements=[{"id": "b", "date": "2026-10-02", "amount": "1210.00",
                                          "text": "TRANSF CLIENTE UNO F2026-0101"}],
                              expected=EXPECTED[:1])
    assert not err and data["matches"][0]["confidence"] == "alta"


async def test_free_tools_need_no_credits(server):
    before = await balance(server)
    err, data, _ = await call(server, "validate_identifier", value="ES91 2100 0418 4502 0005 1332")
    assert not err and data["valid"] and data["bank_name"] == "CaixaBank"
    err, data, _ = await call(server, "sepa_creditor_id", nif="B12345674")
    assert not err and data["creditor_id"].endswith("000B12345674")
    err, data, _ = await call(server, "pricing")
    assert data["credits_per_call"]["reconcile_movements"] == 3
    assert await balance(server) == before


async def test_refund_when_body_fails(server, monkeypatch):
    before = await balance(server)

    def boom(*a, **k):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(server.n43, "result_dict", boom)
    err, _, _ = await call(server, "parse_norma43", content=sample_statement())
    assert err and await balance(server) == before


async def test_paywall_when_credits_run_out(gateway, monkeypatch):
    lic = gateway["issue"]("broke@acme.test", tier="community")
    _env(monkeypatch, gateway, lic["license_key"])
    monkeypatch.setenv("NORMA43_PRICES", f"reconcile_movements={lic['quota_credits'] + 1}")
    import norma43_mcp.server as srv

    srv = importlib.reload(srv)
    async with Client(srv.mcp) as c:
        with pytest.raises(MCPError) as exc:
            await c.call_tool("reconcile_movements", {"expected": EXPECTED, "content": sample_statement()})
    assert exc.value.code == -32062 and "example.com/buy" in str(exc.value)


async def test_hosted_mode_requires_license_header(gateway, license_, monkeypatch):
    from keyflint_verify.mcp import LicenseError

    from norma43_mcp.billing import Billing, license_from_headers

    monkeypatch.setenv("KEYFLINT_PUBLIC_KEYS", PUB)
    monkeypatch.setenv("KEYFLINT_URL", gateway["url"])
    monkeypatch.delenv("KEYFLINT_LICENSE", raising=False)
    b = Billing(hosted=True)
    with pytest.raises(LicenseError):
        b.client(None)
    hdr = {"Authorization": f"Bearer {license_['license_key']}"}
    assert license_from_headers(hdr) == license_["license_key"]
    async with b.charge(1, "parse_norma43", hdr) as receipt:
        assert receipt["remaining_balance"] >= 0
    with pytest.raises(LicenseError):
        b.client({"X-License-Key": "LIC-forged"}).check()
