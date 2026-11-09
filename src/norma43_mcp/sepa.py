"""SEPA payment files for Spanish banks: direct debits pain.008.001.02 (remesa de recibos, Cuaderno 19.14 XML)
and credit transfers pain.001.001.03 (transferencias, Cuaderno 34.14 XML).

Deterministic: same input -> same bytes (MsgId is a hash of the content; CreDtTm defaults to the execution date
at 09:00). Every IBAN, the creditor identifier and the totals are checked before anything is written; a wrong
item is refused with its reason, never fixed silently.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
import xml.etree.ElementTree as ET
from datetime import date
from typing import Any

from . import ids
from .reconcile import parse_amount, parse_date


class SepaError(ValueError):
    pass


MAX_ITEMS = 10_000
SEQ = ("FRST", "RCUR", "OOFF", "FNAL")
_ALLOWED = re.compile(r"[^A-Za-z0-9/\-?:().,'+ ]")
NS = {"pain.008": "urn:iso:std:iso:20022:tech:xsd:pain.008.001.02",
      "pain.001": "urn:iso:std:iso:20022:tech:xsd:pain.001.001.03"}


def clean(text: Any, size: int) -> str:
    """EPC basic Latin character set: accents dropped, ñ -> n, other symbols -> space."""
    s = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    s = re.sub(r"\s+", " ", _ALLOWED.sub(" ", s.replace("&", "+"))).strip()
    return s[:size]


def _id(text: Any, size: int = 35) -> str:
    s = re.sub(r"[^A-Za-z0-9/\-?:().,'+]", "", clean(text, 200))
    return s[:size]


def _iban(value: Any, where: str) -> str:
    res = ids.validate_iban(str(value or ""))
    if not res.get("valid"):
        raise SepaError(f"{where}: IBAN no válido ({res.get('error', 'formato')})")
    return res["normalized"]


def _bic(value: Any, where: str) -> str:
    b = re.sub(r"\s", "", str(value or "")).upper()
    if b and not re.fullmatch(r"[A-Z]{6}[A-Z0-9]{2}([A-Z0-9]{3})?", b):
        raise SepaError(f"{where}: BIC no válido {value!r}")
    return b


def _amt(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


def _sub(parent: ET.Element, tag: str, text: str | None = None) -> ET.Element:
    el = ET.SubElement(parent, tag)
    if text is not None:
        el.text = text
    return el


def _agent(parent: ET.Element, tag: str, bic: str) -> None:
    fin = _sub(_sub(parent, tag), "FinInstnId")
    if bic:
        _sub(fin, "BIC", bic)
    else:
        _sub(_sub(fin, "Othr"), "Id", "NOTPROVIDED")


def _party(parent: ET.Element, tag: str, name: str) -> None:
    _sub(_sub(parent, tag), "Nm", name)


def _account(parent: ET.Element, tag: str, iban: str) -> None:
    _sub(_sub(_sub(parent, tag), "Id"), "IBAN", iban)


def _finish(root: ET.Element, kind: str) -> str:
    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def _msg_id(prefix: str, payload: list[Any]) -> str:
    return prefix + hashlib.sha256(repr(payload).encode()).hexdigest()[:29].upper()


def _header(doc: ET.Element, msg_id: str, created: str, n: int, total: int, name: str, party_id: str) -> None:
    grp = _sub(doc, "GrpHdr")
    _sub(grp, "MsgId", msg_id)
    _sub(grp, "CreDtTm", created)
    _sub(grp, "NbOfTxs", str(n))
    _sub(grp, "CtrlSum", _amt(total))
    ip = _sub(grp, "InitgPty")
    _sub(ip, "Nm", name)
    if party_id:
        _sub(_sub(_sub(_sub(ip, "Id"), "OrgId"), "Othr"), "Id", party_id)


def _created(created_at: str, when: date) -> str:
    if created_at:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", created_at.strip()):
            raise SepaError("created_at: usa AAAA-MM-DDTHH:MM:SS")
        return created_at.strip()
    return f"{when.isoformat()}T09:00:00"


def direct_debits(creditor: dict[str, Any], collection_date: str, debits: list[dict[str, Any]],
                  sequence_type: str = "RCUR", scheme: str = "CORE", created_at: str = "",
                  today: date | None = None) -> dict[str, Any]:
    """Remesa de recibos SEPA (pain.008.001.02)."""
    if not isinstance(debits, list) or not debits:
        raise SepaError("debits: lista vacía")
    if len(debits) > MAX_ITEMS:
        raise SepaError(f"máximo {MAX_ITEMS} recibos por fichero")
    name = clean(creditor.get("name"), 70)
    if not name:
        raise SepaError("creditor.name es obligatorio")
    iban = _iban(creditor.get("iban"), "creditor")
    bic = _bic(creditor.get("bic"), "creditor")
    cid = ids.compact(str(creditor.get("creditor_id", "") or ""))
    if not cid and creditor.get("nif"):
        cid = ids.creditor_identifier(str(creditor["nif"]), str(creditor.get("business_code", "000")))["creditor_id"]
    chk = ids.validate_creditor_id(cid) if cid else {"valid": False}
    if not chk.get("valid"):
        raise SepaError("creditor.creditor_id (AT-02) no válido: indícalo o pasa creditor.nif para calcularlo")
    when = parse_date(collection_date, "collection_date")
    if today and when < today:
        raise SepaError("collection_date no puede ser anterior a hoy")
    seq_default = sequence_type.strip().upper()
    if seq_default not in SEQ:
        raise SepaError(f"sequence_type debe ser {', '.join(SEQ)}")
    scheme = scheme.strip().upper()
    if scheme not in ("CORE", "B2B"):
        raise SepaError("scheme debe ser CORE o B2B")

    rows, errors, seen = [], [], set()
    for i, d in enumerate(debits):
        where = f"recibo {i + 1}"
        try:
            if not isinstance(d, dict):
                raise SepaError(f"{where}: no es un objeto")
            cents = parse_amount(d.get("amount"), f"{where}.amount")
            if cents <= 0 or cents > 99_999_999_999:
                raise SepaError(f"{where}: importe debe ser positivo")
            mandate = _id(d.get("mandate_id"))
            if not mandate:
                raise SepaError(f"{where}: falta mandate_id (referencia del mandato)")
            signed = parse_date(d.get("mandate_date"), f"{where}.mandate_date")
            if signed > when:
                raise SepaError(f"{where}: el mandato es posterior a la fecha de cobro")
            debtor = clean(d.get("name"), 70)
            if not debtor:
                raise SepaError(f"{where}: falta name del deudor")
            e2e = _id(d.get("end_to_end_id") or d.get("reference") or f"{mandate}-{i + 1}")
            if e2e in seen:
                raise SepaError(f"{where}: end_to_end_id repetido {e2e}")
            seen.add(e2e)
            seq = str(d.get("sequence_type") or seq_default).upper()
            if seq not in SEQ:
                raise SepaError(f"{where}: sequence_type {seq!r}")
            rows.append({"cents": cents, "mandate": mandate, "signed": signed.isoformat(), "name": debtor,
                         "iban": _iban(d.get("iban"), where), "bic": _bic(d.get("bic"), where),
                         "e2e": e2e, "seq": seq, "text": clean(d.get("concept") or d.get("reference"), 140)})
        except (SepaError, ValueError) as exc:
            errors.append({"index": i, "error": str(exc)})
    if errors:
        raise SepaError(f"{len(errors)} recibo(s) con errores; no se genera el fichero. Primero: {errors[0]['error']}")

    total = sum(r["cents"] for r in rows)
    msg_id = _msg_id("REM", [name, iban, cid, when.isoformat(), rows])
    root = ET.Element("Document", xmlns=NS["pain.008"])
    doc = _sub(root, "CstmrDrctDbtInitn")
    _header(doc, msg_id, _created(created_at, when), len(rows), total, name, cid)
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        groups.setdefault(r["seq"], []).append(r)
    for k, (seq, items) in enumerate(sorted(groups.items())):
        pmt = _sub(doc, "PmtInf")
        _sub(pmt, "PmtInfId", f"{msg_id[:30]}-{k + 1}")
        _sub(pmt, "PmtMtd", "DD")
        _sub(pmt, "NbOfTxs", str(len(items)))
        _sub(pmt, "CtrlSum", _amt(sum(r["cents"] for r in items)))
        tp = _sub(pmt, "PmtTpInf")
        _sub(_sub(tp, "SvcLvl"), "Cd", "SEPA")
        _sub(_sub(tp, "LclInstrm"), "Cd", scheme)
        _sub(tp, "SeqTp", seq)
        _sub(pmt, "ReqdColltnDt", when.isoformat())
        _party(pmt, "Cdtr", name)
        _account(pmt, "CdtrAcct", iban)
        _agent(pmt, "CdtrAgt", bic)
        _sub(pmt, "ChrgBr", "SLEV")
        sch = _sub(_sub(_sub(_sub(pmt, "CdtrSchmeId"), "Id"), "PrvtId"), "Othr")
        _sub(sch, "Id", cid)
        _sub(_sub(sch, "SchmeNm"), "Prtry", "SEPA")
        for r in items:
            tx = _sub(pmt, "DrctDbtTxInf")
            _sub(_sub(tx, "PmtId"), "EndToEndId", r["e2e"])
            _sub(tx, "InstdAmt", _amt(r["cents"])).set("Ccy", "EUR")
            mnd = _sub(_sub(tx, "DrctDbtTx"), "MndtRltdInf")
            _sub(mnd, "MndtId", r["mandate"])
            _sub(mnd, "DtOfSgntr", r["signed"])
            _agent(tx, "DbtrAgt", r["bic"])
            _party(tx, "Dbtr", r["name"])
            _account(tx, "DbtrAcct", r["iban"])
            if r["text"]:
                _sub(_sub(tx, "RmtInf"), "Ustrd", r["text"])
    xml = _finish(root, "pain.008")
    return {"format": "pain.008.001.02", "filename": f"remesa-{when.isoformat()}.xml", "content": xml,
            "sha256": hashlib.sha256(xml.encode()).hexdigest(), "message_id": msg_id,
            "creditor_id": cid, "count": len(rows), "total": _amt(total), "collection_date": when.isoformat(),
            "by_sequence": {s: len(v) for s, v in sorted(groups.items())}}


def credit_transfers(debtor: dict[str, Any], execution_date: str, payments: list[dict[str, Any]],
                     created_at: str = "", today: date | None = None) -> dict[str, Any]:
    """Transferencias SEPA (pain.001.001.03): pago a proveedores, nóminas."""
    if not isinstance(payments, list) or not payments:
        raise SepaError("payments: lista vacía")
    if len(payments) > MAX_ITEMS:
        raise SepaError(f"máximo {MAX_ITEMS} transferencias por fichero")
    name = clean(debtor.get("name"), 70)
    if not name:
        raise SepaError("debtor.name es obligatorio")
    iban = _iban(debtor.get("iban"), "debtor")
    bic = _bic(debtor.get("bic"), "debtor")
    nif = ids.compact(str(debtor.get("nif", "") or ""))
    when = parse_date(execution_date, "execution_date")
    if today and when < today:
        raise SepaError("execution_date no puede ser anterior a hoy")
    rows, errors, seen = [], [], set()
    for i, p in enumerate(payments):
        where = f"transferencia {i + 1}"
        try:
            if not isinstance(p, dict):
                raise SepaError(f"{where}: no es un objeto")
            cents = parse_amount(p.get("amount"), f"{where}.amount")
            if cents <= 0 or cents > 99_999_999_999:
                raise SepaError(f"{where}: importe debe ser positivo")
            creditor = clean(p.get("name"), 70)
            if not creditor:
                raise SepaError(f"{where}: falta name del beneficiario")
            e2e = _id(p.get("end_to_end_id") or p.get("reference") or f"TRF-{i + 1}")
            if e2e in seen:
                raise SepaError(f"{where}: end_to_end_id repetido {e2e}")
            seen.add(e2e)
            purpose = str(p.get("purpose", "") or "").strip().upper()
            if purpose and not re.fullmatch(r"[A-Z]{4}", purpose):
                raise SepaError(f"{where}: purpose es un código ISO de 4 letras (SALA nómina, SUPP proveedor)")
            rows.append({"cents": cents, "name": creditor, "iban": _iban(p.get("iban"), where),
                         "bic": _bic(p.get("bic"), where), "e2e": e2e, "purpose": purpose,
                         "text": clean(p.get("concept") or p.get("reference"), 140)})
        except (SepaError, ValueError) as exc:
            errors.append({"index": i, "error": str(exc)})
    if errors:
        raise SepaError(f"{len(errors)} transferencia(s) con errores; no se genera el fichero. Primero: "
                        f"{errors[0]['error']}")
    total = sum(r["cents"] for r in rows)
    msg_id = _msg_id("TRF", [name, iban, when.isoformat(), rows])
    root = ET.Element("Document", xmlns=NS["pain.001"])
    doc = _sub(root, "CstmrCdtTrfInitn")
    _header(doc, msg_id, _created(created_at, when), len(rows), total, name, nif)
    pmt = _sub(doc, "PmtInf")
    _sub(pmt, "PmtInfId", f"{msg_id[:30]}-1")
    _sub(pmt, "PmtMtd", "TRF")
    _sub(pmt, "NbOfTxs", str(len(rows)))
    _sub(pmt, "CtrlSum", _amt(total))
    _sub(_sub(_sub(pmt, "PmtTpInf"), "SvcLvl"), "Cd", "SEPA")
    _sub(pmt, "ReqdExctnDt", when.isoformat())
    _party(pmt, "Dbtr", name)
    _account(pmt, "DbtrAcct", iban)
    _agent(pmt, "DbtrAgt", bic)
    _sub(pmt, "ChrgBr", "SLEV")
    for r in rows:
        tx = _sub(pmt, "CdtTrfTxInf")
        _sub(_sub(tx, "PmtId"), "EndToEndId", r["e2e"])
        if r["purpose"]:
            _sub(_sub(_sub(tx, "PmtTpInf"), "CtgyPurp"), "Cd", r["purpose"])
        _sub(_sub(tx, "Amt"), "InstdAmt", _amt(r["cents"])).set("Ccy", "EUR")
        if r["bic"]:
            _agent(tx, "CdtrAgt", r["bic"])
        _party(tx, "Cdtr", r["name"])
        _account(tx, "CdtrAcct", r["iban"])
        if r["text"]:
            _sub(_sub(tx, "RmtInf"), "Ustrd", r["text"])
    xml = _finish(root, "pain.001")
    return {"format": "pain.001.001.03", "filename": f"transferencias-{when.isoformat()}.xml", "content": xml,
            "sha256": hashlib.sha256(xml.encode()).hexdigest(), "message_id": msg_id, "count": len(rows),
            "total": _amt(total), "execution_date": when.isoformat()}
