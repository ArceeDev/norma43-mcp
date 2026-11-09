"""SEPA pain.008 (remesa de recibos) and pain.001 (transferencias): checks first, deterministic bytes."""
from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from norma43_mcp import ids, sepa
from test_e2e import balance, call, license_, server  # noqa: F401  (fixtures)

IBAN_A = ids.iban_from_ccc(ids.ccc_from_parts("2100", "0418", "4502000513"))
IBAN_B = ids.iban_from_ccc(ids.ccc_from_parts("0049", "1500", "0512345678"))
CREDITOR = {"name": "Asesoría Núñez & Cía SL", "iban": IBAN_A, "nif": "B12345674"}
DEBITS = [
    {"name": "Talleres Ruiz SL", "iban": IBAN_B, "amount": "60.50", "mandate_id": "CUOTA-0012",
     "mandate_date": "2024-01-15", "concept": "Cuota marzo"},
    {"name": "José Peña", "iban": IBAN_B, "amount": "1.234,56", "mandate_id": "CUOTA-0013",
     "mandate_date": "2026-03-01", "sequence_type": "FRST"},
]
NS = {"p": sepa.NS["pain.008"]}


def test_direct_debits_structure_and_totals():
    out = sepa.direct_debits(CREDITOR, "2026-03-05", DEBITS)
    root = ET.fromstring(out["content"])
    assert root.find("p:CstmrDrctDbtInitn/p:GrpHdr/p:CtrlSum", NS).text == "1295.06"
    assert root.find("p:CstmrDrctDbtInitn/p:GrpHdr/p:NbOfTxs", NS).text == "2"
    assert out["by_sequence"] == {"FRST": 1, "RCUR": 1}
    assert len(root.findall("p:CstmrDrctDbtInitn/p:PmtInf", NS)) == 2
    assert "Asesoria Nunez + Cia SL" in out["content"] and out["creditor_id"].startswith("ES")
    assert sepa.direct_debits(CREDITOR, "2026-03-05", DEBITS)["sha256"] == out["sha256"]


def test_one_bad_iban_refuses_whole_batch():
    bad = DEBITS + [{**DEBITS[0], "iban": "ES0000000000000000000000", "mandate_id": "X"}]
    with pytest.raises(sepa.SepaError, match="1 recibo"):
        sepa.direct_debits(CREDITOR, "2026-03-05", bad)


def test_mandate_after_collection_and_duplicate_e2e_refused():
    with pytest.raises(sepa.SepaError, match="posterior"):
        sepa.direct_debits(CREDITOR, "2023-03-05", DEBITS[:1])
    dup = [{**DEBITS[0], "end_to_end_id": "A"}, {**DEBITS[1], "end_to_end_id": "A"}]
    with pytest.raises(sepa.SepaError, match="repetido"):
        sepa.direct_debits(CREDITOR, "2026-03-05", dup)


def test_credit_transfers():
    out = sepa.credit_transfers({"name": "Cliente SL", "iban": IBAN_A}, "2026-03-10", [
        {"name": "Proveedor XYZ SL", "iban": IBAN_B, "amount": "300", "concept": "Fra 5521", "purpose": "SUPP"}])
    root = ET.fromstring(out["content"])
    ns = {"p": sepa.NS["pain.001"]}
    assert root.find("p:CstmrCdtTrfInitn/p:PmtInf/p:CtrlSum", ns).text == "300.00"
    assert root.find(".//p:CdtTrfTxInf/p:PmtTpInf/p:CtgyPurp/p:Cd", ns).text == "SUPP"


async def test_sepa_e2e_charges_and_bad_is_free(server):
    before = await balance(server)
    err, data, text = await call(server, "sepa_direct_debits", creditor=CREDITOR, collection_date="2026-03-05",
                                 debits=DEBITS)
    assert not err, text
    assert data["billing"]["credits_charged"] == 2 and await balance(server) == before - 2
    err, _, text = await call(server, "sepa_direct_debits", creditor={**CREDITOR, "iban": "ES00"},
                              collection_date="2026-03-05", debits=DEBITS)
    assert err and "IBAN" in text and await balance(server) == before - 2
