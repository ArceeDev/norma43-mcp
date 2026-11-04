"""Parser, identifiers and reconciliation engine (pure functions, no MCP, no billing)."""

from __future__ import annotations

import base64

import pytest

from n43_builder import Mov, account, file, r23_fields, r24, r33, r88, sample_statement
from norma43_mcp import ids, n43
from norma43_mcp.reconcile import ReconcileError, build_expected, build_movements, movements_from_n43, reconcile


# ---- identifiers (checked against published examples) -----------------------------------------------

def test_ccc_and_iban_known_example():
    v = ids.validate_ccc("2100 0418 45 0200051332")
    assert v["valid"] and v["iban"] == "ES9121000418450200051332" and v["bank_name"] == "CaixaBank"
    bad = ids.validate_ccc("21000418460200051332")
    assert not bad["valid"] and bad["expected_control_digits"] == "45"


def test_iban_validation():
    assert ids.validate_iban("ES91 2100 0418 4502 0005 1332")["valid"]
    assert ids.validate_iban("DE89 3704 0044 0532 0130 00")["valid"]
    assert ids.validate_iban("GB82 WEST 1234 5698 7654 32")["valid"]
    assert not ids.validate_iban("ES92 2100 0418 4502 0005 1332")["valid"]
    assert "24" in ids.validate_iban("ES91 2100 0418 4502 0005 133")["error"]


def test_nif_nie_cif():
    assert ids.validate_nif("12345678Z")["valid"] and not ids.validate_nif("12345678A")["valid"]
    assert ids.validate_nif("X1234567L")["valid"] and ids.validate_nif("ESA58818501")["valid"]
    assert ids.validate_nif("Q2826000H")["valid"] and not ids.validate_nif("Q28260008")["valid"]
    assert ids.validate_nif("B12345674")["valid"]


def test_sepa_creditor_id_epc_example():
    # EPC262-08 worked example: DE98ZZZ09999999999
    assert ids.creditor_identifier("09999999999", "ZZZ", "DE")["creditor_id"] == "DE98ZZZ09999999999"
    es = ids.creditor_identifier("B12345674")
    assert es["creditor_id"].startswith("ES") and es["creditor_id"].endswith("000B12345674") and es["nif_valid"]
    assert ids.validate_creditor_id(es["creditor_id"])["valid"]
    with pytest.raises(ValueError):
        ids.creditor_identifier("B12345674", "00")


def test_reference1_annex3_example():
    assert ids.reference1_check_digit("82546789013") == "8"
    assert ids.validate_reference1("825467890138")["valid"]


def test_auto_detection():
    assert ids.detect_and_validate("ES91 2100 0418 4502 0005 1332")["kind"] == "iban"
    assert ids.detect_and_validate("21000418450200051332")["kind"] == "ccc"
    assert ids.detect_and_validate("X1234567L")["kind"] == "nif"
    assert ids.detect_and_validate("DE98ZZZ09999999999")["kind"] == "creditor_id"


# ---- parser ----------------------------------------------------------------------------------------

def test_parse_sample_statement():
    res = n43.parse(sample_statement())
    out = n43.result_dict(res)
    assert out["valid"] and out["issues"] == []
    acc = out["accounts"][0]
    assert acc["iban"] == "ES9121000418450200051332" and acc["holder"] == "ACME SOLUCIONES SL"
    assert acc["opening_balance"] == "1500.00" and acc["closing_balance"] == "3922.20"
    assert acc["debit_total"] == "2207.80" and acc["credit_total"] == "4630.00" and acc["currency"] == "EUR"
    first, debit = acc["movements"][0], acc["movements"][1]
    assert first["amount"] == "1210.00" and first["direction"] == "abono" and first["balance_after"] == "2710.00"
    assert first["sepa"]["ordenante_nombre"] == "CLIENTE UNO SA" and first["sepa"]["concepto"] == "PAGO FACTURA F2026-0101"
    assert debit["amount"] == "-45.30" and debit["sepa"]["mandato_referencia"] == "MAND-778"
    assert debit["sepa"]["esquema"] == "CORE" and debit["description"] == "ELECTRICA DEMO SA RECIBO LUZ SEPT"
    concepts = {c["code"]: c for c in acc["by_concept"]}
    assert concepts["04"]["count"] == 5 and concepts["17"]["debits"] == "12.50"


def test_detects_tampered_balance_and_totals():
    lines = sample_statement().split("\r\n")
    i = next(k for k, ln in enumerate(lines) if ln.startswith("22"))
    lines[i] = lines[i][:28] + "00000000121001" + lines[i][42:]  # 1210.00 -> 1210.01
    out = n43.result_dict(n43.parse("\r\n".join(lines)))
    codes = {x["code"] for x in out["issues"]}
    assert not out["valid"] and {"TOTALS_MISMATCH", "BALANCE_MISMATCH"} <= codes
    assert any(x["line"] for x in out["issues"])


def test_detects_record_count_and_missing_end():
    text = sample_statement()
    lines = text.rstrip("\r\n").split("\r\n")
    lines[-1] = r88(99)
    assert "RECORD_COUNT_MISMATCH" in {x["code"] for x in n43.parse("\r\n".join(lines)).issues}
    truncated = n43.parse("\r\n".join(lines[:-1]))
    assert "MISSING_END_RECORD" in {x["code"] for x in truncated.issues} and not truncated.valid


def test_multi_account_negative_balance_and_currency_equivalence():
    usd = Mov("261003", "13", -10000, [r24("840", 10850)])
    a1 = account([usd], opening=-5000)  # overdrawn account (saldo deudor)
    a2 = account([Mov("261004", "02", 2500)], bank="0049", branch="1500", acct="2710000001", mode="1", name="ACME B")
    out = n43.result_dict(n43.parse(file(a1, a2, eol="\n")))
    assert out["valid"], out["issues"]
    x, y = out["accounts"]
    assert x["opening_balance"] == "-50.00" and x["closing_balance"] == "-150.00"
    assert x["movements"][0]["original_currency"] == {"currency_code": "840", "currency": "USD", "amount": "108.50"}
    assert y["bank_name"] == "Banco Santander" and ids.validate_iban(y["iban"])["valid"]


def test_single_line_blob_and_cp850_bytes():
    text = sample_statement().replace("ACME SOLUCIONES SL", "ACME ESPAÑA SL    ")
    blob = text.replace("\r\n", "")
    assert n43.parse(blob).valid
    raw = text.encode("cp850")
    decoded, enc = n43.read_input(content_base64=base64.b64encode(raw).decode())
    assert enc == "cp850" and "ESPAÑA" in decoded
    assert n43.result_dict(n43.parse(decoded))["accounts"][0]["holder"] == "ACME ESPAÑA SL"


def test_not_a_norma43_file():
    with pytest.raises(n43.N43Error):
        n43.parse("fecha;importe\n2026-10-01;12,00\n")
    with pytest.raises(n43.N43Error):
        n43.read_input("", "")


def test_csv_export_is_excel_safe():
    lines = account([Mov("261002", "99", 1000, [r23_fields("01", "=HYPERLINK(1)")])])
    csv_text = n43.to_csv(n43.parse(file(lines)))
    row = csv_text.splitlines()[1].split(";")
    assert row[8] == "10,00" and row[9] == "1510,00" and row[-1] == "'=HYPERLINK(1)"


def test_footer_mismatch_account():
    lines = account([Mov("261002", "02", 1000)])
    lines[-1] = r33("2100", "0418", "9999999999", 0, 0, 1, 1000, 151000, "978")
    assert "ACCOUNT_MISMATCH" in {x["code"] for x in n43.parse(file(lines)).issues}


# ---- reconciliation --------------------------------------------------------------------------------

EXPECTED = [
    {"id": "F2026-0101", "date": "2026-09-30", "amount": "1.210,00", "reference": "F2026-0101",
     "counterparty": "Cliente Uno, S.A."},
    {"id": "F2026-0102", "date": "2026-10-01", "amount": 1000, "reference": "F2026-0102", "counterparty": "Cliente Dos SL"},
    {"id": "F2026-0103", "date": "2026-10-01", "amount": 1420, "reference": "F2026-0103", "counterparty": "Cliente Dos SL"},
    {"id": "F2026-0104", "date": "2026-10-12", "amount": 1000, "reference": "F2026-0104", "counterparty": "Cliente Tres SL"},
    {"id": "F2026-0105", "date": "2026-10-20", "amount": 750, "reference": "F2026-0105", "counterparty": "Cliente Cuatro"},
    {"id": "LUZ-09", "date": "2026-10-05", "amount": -45.30, "counterparty": "Eléctrica Demo"},
    {"id": "NOM-10-JP", "date": "25/10/2026", "amount": "-1850.00", "counterparty": "Juan Pérez García"},
]


def _run(**kw):
    return reconcile(movements_from_n43(n43.parse(sample_statement())), build_expected(EXPECTED), **kw)


def test_reconcile_sample_statement():
    out = _run()
    got = {(m["type"], tuple(m["movement_ids"]), tuple(m["expected_ids"])) for m in out["matches"]}
    assert got == {
        ("1:1", ("a1-m1",), ("F2026-0101",)),
        ("1:1", ("a1-m2",), ("LUZ-09",)),
        ("1:1", ("a1-m7",), ("NOM-10-JP",)),
        # 2420 = 1000 + 1420 could also be F0104 + F0103; the references pick the right pair.
        ("1:N", ("a1-m3",), ("F2026-0102", "F2026-0103")),
        ("N:1", ("a1-m5", "a1-m6"), ("F2026-0104",)),
    }
    s = out["summary"]
    assert s["unmatched_movements"] == 2 and s["unmatched_expected"] == 1 and s["ambiguous_groups"] == 0
    assert s["unmatched_expected_amount"] == {"in": "750.00", "out": "0.00"}
    best = next(m for m in out["matches"] if m["expected_ids"] == ["F2026-0101"])
    assert best["confidence"] == "alta" and any("F2026-0101" in r for r in best["reasons"])


def test_reconcile_never_guesses_ties():
    movs = build_movements([{"id": "b1", "date": "2026-10-03", "amount": 100, "text": "TRANSFERENCIA"},
                            {"id": "b2", "date": "2026-10-03", "amount": 100, "text": "TRANSFERENCIA"}])
    exps = build_expected([{"id": "x", "date": "2026-10-03", "amount": 100},
                           {"id": "y", "date": "2026-10-03", "amount": 100}])
    out = reconcile(movs, exps)
    assert out["matches"] == [] and len(out["ambiguous"]) == 1
    assert out["ambiguous"][0]["movement_ids"] == ["b1", "b2"] and out["ambiguous"][0]["expected_ids"] == ["x", "y"]


def test_reference_breaks_the_tie():
    movs = build_movements([{"id": "b1", "date": "2026-10-03", "amount": 100, "text": "PAGO FRA 2026/0077"},
                            {"id": "b2", "date": "2026-10-03", "amount": 100, "text": "PAGO FRA 2026/0078"}])
    exps = build_expected([{"id": "x", "date": "2026-10-01", "amount": 100, "reference": "2026-0078"},
                           {"id": "y", "date": "2026-10-01", "amount": 100, "reference": "2026-0077"}])
    pairs = {(m["movement_ids"][0], m["expected_ids"][0]) for m in reconcile(movs, exps)["matches"]}
    assert pairs == {("b1", "y"), ("b2", "x")}


def test_group_without_evidence_is_only_a_suggestion():
    movs = build_movements([{"id": "b", "date": "2026-10-03", "amount": 300, "text": "INGRESO"}])
    exps = build_expected([{"id": "x", "date": "2026-10-03", "amount": 100},
                           {"id": "y", "date": "2026-10-03", "amount": 200}])
    out = reconcile(movs, exps)
    assert out["matches"] == [] and out["suggestions"][0]["expected_ids"] == ["x", "y"]


def test_numeric_reference_needs_digit_boundaries():
    movs = build_movements([{"id": "b", "date": "2026-10-03", "amount": 50, "text": "REF 91234567"}])
    exps = build_expected([{"id": "x", "date": "2026-10-03", "amount": 50, "reference": "123456"}])
    reasons = reconcile(movs, exps)["matches"][0]["reasons"]
    assert not any("referencia" in r for r in reasons)


def test_tolerance_window_and_sign():
    movs = build_movements([{"id": "b", "date": "2026-10-20", "amount": "-99.50", "text": "PAGO PROVEEDOR"}])
    exps = build_expected([{"id": "p", "date": "2026-10-01", "amount": "-100.00", "counterparty": "Proveedor"},
                           {"id": "c", "date": "2026-10-20", "amount": "99.50"}])
    assert reconcile(movs, exps)["matches"] == []  # sign differs for c, p is outside the window and tolerance
    out = reconcile(movs, exps, date_window_days=30, amount_tolerance="0.50")
    assert out["matches"][0]["expected_ids"] == ["p"]


def test_bad_inputs_are_rejected():
    with pytest.raises(ReconcileError):
        build_expected([{"id": "x", "date": "2026-13-01", "amount": 1}])
    with pytest.raises(ReconcileError):
        build_expected([{"id": "x", "date": "2026-10-01", "amount": "1.234"}])
    with pytest.raises(ReconcileError):
        build_expected([{"id": "x", "date": "2026-10-01", "amount": 1}, {"id": "x", "date": "2026-10-01", "amount": 2}])
    with pytest.raises(ReconcileError):
        _run(max_group_size=9)


def test_real_world_quirks_latin1_and_repeated_or_empty_23():
    """Banks export Latin-1/Windows-1252 and repeat or blank record 23: still valid, text kept."""
    from n43_builder import Mov, account, file, r23, r23_fields
    raw = file(account([
        Mov("261002", "04", 10000, [r23_fields("01", "PEÑA IBÁÑEZ"), r23_fields("01", "PEÑA IBÁÑEZ"), r23("02", "")]),
    ], mode="2")).encode("latin-1")
    text, enc = n43.decode_bytes(raw)
    assert enc == "cp1252" and "PEÑA IBÁÑEZ" in text
    res = n43.parse(text)
    assert res.valid
    assert {i["code"] for i in res.issues} <= {"DUPLICATE_COMPLEMENT"}
    assert "PEÑA IBÁÑEZ" in res.accounts[0].movements[0].description()
