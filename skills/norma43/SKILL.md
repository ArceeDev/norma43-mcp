---
name: norma43-bank-reconciliation
description: Read Spanish bank statements in Norma 43 / Cuaderno 43 / AEB43 / CSB43 format (.n43, .aeb, .txt from Santander, BBVA, CaixaBank, Sabadell...) and reconcile them against invoices, receipts, payroll or ledger entries. Use for "extracto bancario", "conciliación bancaria", "punteo", IBAN/NIF/CIF validation or SEPA creditor id.
---

# Norma 43 and bank reconciliation (Spain)

Needs the `norma43-mcp` server. Paid tools use KeyFlint credits; unreadable input is never charged and failures are refunded.

## Setup

```json
{
  "mcpServers": {
    "norma43": {
      "command": "uvx",
      "args": ["norma43-mcp"],
      "env": { "KEYFLINT_LICENSE": "LIC-..." }
    }
  }
}
```

Get a license with credits: see the `pricing` tool (`buy_credits`).

## When to use which tool

| The user has / wants | Tool | Credits |
|---|---|---|
| A Norma 43 file to read, check or summarise | `parse_norma43` | 1 |
| The statement in Excel | `norma43_to_csv` | 1 |
| To match bank movements with invoices or ledger entries | `reconcile_movements` | 3 |
| To check an IBAN, CCC, NIF/NIE/CIF, SEPA creditor id | `validate_identifier` | 0 |
| A remesa de recibos (fees, cuotas) for the bank | `sepa_direct_debits` | 2 |
| A batch of supplier or payroll transfers | `sepa_credit_transfers` | 2 |
| A SEPA creditor id (AT-02) from a NIF | `sepa_creditor_id` | 0 |
| Prices or remaining credits | `pricing`, `credits_balance` | 0 |

How to recognise Norma 43: fixed 80-character lines that start with `11`, `22`, `23`, `33`, `88`.

## Workflow

1. **Pass the file.** If it has Ñ or accents and was exported in CP850/Latin-1, send the raw bytes as `content_base64`; otherwise `content` is fine. Do not retype or "clean" the file.
2. **Check `valid` first.** If false, show the `issues` (each names its line) before drawing conclusions. `BALANCE_MISMATCH` or `TOTALS_MISMATCH` usually means a truncated or edited file.
3. **Reconcile.** Build `expected` from the user's invoices: `{id, date, amount, reference, counterparty}`.
   - Sign convention: **+ = money in** (customer invoices, cobros), **- = money out** (supplier invoices, payroll, pagos).
   - Pass the Norma 43 file directly as `content`; no need to parse it first.
   - Add `amount_tolerance: "0.50"` only if the user says the bank deducts fees.
4. **Report** in this order:
   - `matches` with confidence `alta`: reconciled.
   - `ambiguous`: **ask the user**. Never pick one yourself; the tool returns ties on purpose.
   - `suggestions`: weak candidates for the user to confirm.
   - `unmatched_movements` / `unmatched_expected`: pending items.

## Rules

- Amounts are strings with 2 decimals. Never round or convert to float.
- Do not invent matches the tool did not return.
- On error `-32062` (no credits), show the user the purchase link from the message.
- Bank names are informative; the IBAN is computed from the CCC and is authoritative.
