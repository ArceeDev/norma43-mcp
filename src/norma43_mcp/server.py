"""Norma 43 MCP: Spanish bank statements (Cuaderno 43) and bank reconciliation for AI agents.

Paid tools debit KeyFlint credits only after the input has been read successfully, and refund them
if the tool body fails. Identifier tools are free.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from contextlib import contextmanager
from typing import Any, Iterator

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import __version__, ids, n43
from . import apps
from .billing import Billing
from .reconcile import (ReconcileError, build_expected, build_movements, check_options, check_size,
                        movements_from_n43, reconcile)

DEFAULT_PRICES = {"parse_norma43": 1, "norma43_to_csv": 1, "reconcile_movements": 3}
MAX_BYTES = int(os.environ.get("NORMA43_MAX_BYTES", 5_000_000))


def _prices() -> dict[str, int]:
    prices = dict(DEFAULT_PRICES)
    for item in filter(None, (p.strip() for p in os.environ.get("NORMA43_PRICES", "").split(","))):
        name, _, n = item.partition("=")
        if name.strip() in prices and n.strip().isdigit() and int(n) > 0:
            prices[name.strip()] = int(n)
    return prices


PRICES = _prices()
billing = Billing(hosted=os.environ.get("NORMA43_TRANSPORT", "stdio") != "stdio")
LOCAL = ToolAnnotations(readOnlyHint=True, openWorldHint=False, idempotentHint=True)

mcp = MCPServer(
    "norma43-mcp",
    title="Norma 43 y conciliación bancaria",
    version=__version__,
    website_url="https://github.com/ArceeDev/norma43-mcp",
    instructions=(
        "Spanish bank statements (Norma 43 / Cuaderno 43 / AEB43 / CSB43) and bank reconciliation. "
        "Use `parse_norma43` to read a .n43/.txt/.aeb file into accounts, IBANs, balances and movements with "
        "integrity checks (record 33 totals, opening + movements = closing, record 88 count). "
        "Use `reconcile` to match bank movements against invoices, receipts or ledger entries: it never guesses, "
        "ties are returned as `ambiguous` for a human. Amounts are strings with 2 decimals; negative = cargo "
        "(money out), positive = abono (money in). Free tools: `validate_identifier` (IBAN, CCC, NIF/NIE/CIF, "
        "SEPA creditor id), `sepa_creditor_id`, `pricing`, `credits_balance`. Paid tools cost KeyFlint credits "
        "(see `pricing`); unreadable input is never charged and failures are refunded."
    ),
)


UI = apps.ui_meta("norma43")
apps.register(mcp, slug="norma43", name="Norma 43", icon="landmark", version=__version__, lang="es",
              buy_url=lambda: billing.checkout_url,
              labels={"parse_norma43": "Extracto Norma 43", "norma43_to_csv": "Norma 43 a CSV", "reconcile_movements": "Conciliación bancaria", "credits_balance": "Saldo de créditos"})

@contextmanager
def _user_errors() -> Iterator[None]:
    """Input problems reach the agent with their message (and, raised before a debit, cost nothing)."""
    try:
        yield
    except (n43.N43Error, ReconcileError, ValueError) as exc:
        raise ToolError(str(exc)) from exc


def _headers(ctx: Context) -> Any:
    try:
        return ctx.headers
    except ValueError:
        return None


def _note(receipt: dict, credits: int) -> dict[str, Any]:
    return {"credits_charged": credits, "credits_remaining": receipt.get("remaining_balance")}


async def _read_n43(ctx: Context, content: str, content_base64: str) -> tuple[n43.ParseResult, str]:
    """Decode + parse before any debit; the license is checked first so unlicensed callers cost no CPU."""
    with _user_errors():
        text, encoding = n43.read_input(content, content_base64, MAX_BYTES)
    billing.client(_headers(ctx)).check()
    with _user_errors():
        res = await asyncio.to_thread(n43.parse, text)
    return res, encoding


@mcp.tool(annotations=LOCAL, meta=UI)
async def parse_norma43(ctx: Context, content: str = "", content_base64: str = "", include_movements: bool = True,
                        max_movements: int = 2000) -> dict[str, Any]:
    """Read a Spanish Norma 43 (Cuaderno 43 / AEB43 / CSB43) bank statement into structured JSON.

    Returns every account (CCC, computed IBAN, bank, holder, period, opening/closing balance, debit/credit
    totals, breakdown by AEB common concept) and its movements (dates, signed amount, running balance,
    concept, references, record-23 texts, decoded SEPA fields in modalidad 3). Checks the file the way a
    bank does: record 33 counts and totals, opening + movements = closing, record 88 record count, dates,
    signs and field formats. `valid` is false when any error is found; each issue names its line.

    Args:
        content: The file as text (paste the .n43/.txt content).
        content_base64: The raw file bytes as base64 (use for CP850/Latin-1 files with Ñ and accents).
        include_movements: False returns only accounts, totals and checks.
        max_movements: Cap on returned movements across all accounts (default 2000, max 20000).
    """
    res, encoding = await _read_n43(ctx, content, content_base64)
    cap = max(0, min(int(max_movements), 20000))
    async with billing.charge(PRICES["parse_norma43"], "parse_norma43", _headers(ctx)) as receipt:
        out = n43.result_dict(res, include_movements, cap)
    return {**out, "encoding": encoding, "billing": _note(receipt, PRICES["parse_norma43"])}


@mcp.tool(annotations=LOCAL, meta=UI)
async def norma43_to_csv(ctx: Context, content: str = "", content_base64: str = "", delimiter: str = ";",
                         decimal_comma: bool = True) -> dict[str, Any]:
    """Convert a Norma 43 statement to CSV (one row per movement, with IBAN, running balance and concept names).

    Defaults suit Spanish Excel (';' separator, decimal comma). Free-text cells are protected against
    spreadsheet formula injection. Integrity issues are returned alongside the CSV.
    """
    res, encoding = await _read_n43(ctx, content, content_base64)
    async with billing.charge(PRICES["norma43_to_csv"], "norma43_to_csv", _headers(ctx)) as receipt:
        with _user_errors():
            csv_text = n43.to_csv(res, delimiter, decimal_comma)
    errors = [i for i in res.issues if i["severity"] == "error"]
    return {"csv": csv_text, "rows": sum(len(a.movements) for a in res.accounts), "valid": res.valid,
            "errors": errors[:50], "encoding": encoding, "billing": _note(receipt, PRICES["norma43_to_csv"])}


@mcp.tool(annotations=LOCAL, meta=UI)
async def reconcile_movements(ctx: Context, expected: list[dict[str, Any]], content: str = "",
                              content_base64: str = "", movements: list[dict[str, Any]] | None = None,
                              date_window_days: int = 10, amount_tolerance: str = "0", max_group_size: int = 4,
                              min_score: int = 50) -> dict[str, Any]:
    """Bank reconciliation (conciliación bancaria): match bank movements to expected items, deterministically.

    Bank side: a Norma 43 file (`content` / `content_base64`) or a `movements` list of
    {id, date, amount, text}. Expected side: `expected` list of {id, date, amount, reference?, counterparty?}
    such as issued invoices (+, cobros), supplier invoices or payroll (-, pagos), or ledger entries.
    Signs: + is money in, - is money out, as on the statement.

    Finds 1:1 matches, one transfer paying several invoices (1:N) and partial payments (N:1). Each match
    has a 0-100 score, a confidence (alta/media/baja) and the evidence used (amount, invoice reference found
    in the bank concept, counterparty name, date distance, uniqueness). Ties are never guessed: they are
    returned under `ambiguous`. Weak candidates go to `suggestions`. Group matches require a unique exact
    sum plus reference or name evidence.

    Args:
        expected: Items to find in the bank (max 5000).
        content: Norma 43 file as text (alternative to `movements`).
        content_base64: Norma 43 raw bytes as base64.
        movements: Bank movements if you do not have a Norma 43 file (max 5000).
        date_window_days: Max days between bank date and item date (default 10, max 180).
        amount_tolerance: Max amount difference accepted, e.g. "0.50" for bank fees (default "0", max "10").
        max_group_size: Max items combined in 1:N / N:1 matches (1 disables grouping, max 5).
        min_score: Minimum score to confirm a match (default 50).
    """
    with _user_errors():
        check_options(date_window_days, amount_tolerance, max_group_size, min_score)
        exps = build_expected(expected)
        if movements and (content or content_base64):
            raise ReconcileError("Pass the bank side once: a Norma 43 file or a movements list, not both.")
        if movements:
            billing.client(_headers(ctx)).check()
            movs, source = build_movements(movements), "movements"
        else:
            res, _ = await _read_n43(ctx, content, content_base64)
            movs, source = movements_from_n43(res), "norma43"
            if not res.valid:
                source = "norma43 (with integrity errors: run parse_norma43 to see them)"
        if not movs:
            raise ReconcileError("The bank side has no movements; nothing was charged.")
        check_size(movs, exps, amount_tolerance)
    async with billing.charge(PRICES["reconcile_movements"], "reconcile_movements", _headers(ctx)) as receipt:
        out = await asyncio.to_thread(reconcile, movs, exps, date_window_days, amount_tolerance, max_group_size,
                                      min_score)
    return {"bank_source": source, **out, "billing": _note(receipt, PRICES["reconcile_movements"])}


@mcp.tool(annotations=LOCAL)
def validate_identifier(value: str, kind: str = "auto") -> dict[str, Any]:
    """Validate a Spanish/EU banking identifier. Free.

    Recognises IBAN (any SEPA country, plus the inner CCC check for ES), CCC (20 digits, returns the IBAN),
    NIF/NIE/CIF (also ES-prefixed VAT numbers), SEPA creditor identifier (AT-02) and Norma 43 Referencia 1.
    Returns `valid`, the detected `kind`, normalized form and, when wrong, the expected control digits.

    Args:
        value: The identifier, spaces and dashes allowed.
        kind: auto | iban | ccc | nif | creditor_id | reference1.
    """
    with _user_errors():
        return ids.detect_and_validate(value, kind)


@mcp.tool(annotations=LOCAL)
def sepa_creditor_id(nif: str, business_code: str = "000") -> dict[str, Any]:
    """Compute the Spanish SEPA creditor identifier (AT-02, 'identificador de acreedor') from a NIF. Free.

    Format: ES + 2 check digits + 3-character business code (default '000') + NIF, per EPC262-08.
    Needed to issue SEPA direct debits (adeudos / remesas de recibos, Cuaderno 19.14).
    """
    with _user_errors():
        return ids.creditor_identifier(nif, business_code)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def pricing() -> dict[str, Any]:
    """Credit price of each tool and where to buy credits. Free."""
    return {"credits_per_call": {**PRICES, "validate_identifier": 0, "sepa_creditor_id": 0},
            "refunds": "Unreadable input is never charged; a tool that fails after charging is refunded.",
            "buy_credits": billing.checkout_url or "Set KEYFLINT_CHECKOUT_URL"}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False), meta=UI)
async def credits_balance(ctx: Context) -> dict[str, Any]:
    """Remaining credits on your KeyFlint license. Free."""
    return await billing.balance(_headers(ctx))


def main() -> None:
    p = argparse.ArgumentParser(prog="norma43-mcp", description=__doc__)
    p.add_argument("--http", action="store_true", help="Serve Streamable HTTP (hosted, license per request).")
    p.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    p.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8080)))
    args = p.parse_args()
    if args.http:
        billing.hosted = True
        mcp.run("streamable-http", host=args.host, port=args.port, stateless_http=True, json_response=True)
    else:
        mcp.run("stdio")


if __name__ == "__main__":
    main()
