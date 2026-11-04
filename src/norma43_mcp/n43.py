"""Norma 43 (AEB/CECA/UNACC Cuaderno 43, edición junio 2012) parser with integrity checks.

Records (80 positions each): 11 account header, 22 movement, 23 complementary concept (up to 5),
24 currency equivalence, 33 account footer, 88 end of file. Amounts carry 2 implied decimals and
are handled as integer cents end to end, so no float rounding ever touches a balance.
"""

from __future__ import annotations

import base64
import binascii
import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from . import ids

RECORD_LEN = 80

CONCEPTOS_COMUNES = {
    "01": "TALONES - REINTEGROS",
    "02": "ABONARÉS - ENTREGAS - INGRESOS",
    "03": "DOMICILIADOS - RECIBOS - LETRAS - PAGOS POR SU CTA.",
    "04": "GIROS - TRANSFERENCIAS - TRASPASOS - CHEQUES",
    "05": "AMORTIZACIONES PRÉSTAMOS, CRÉDITOS, ETC.",
    "06": "REMESAS EFECTOS",
    "07": "SUSCRIPCIONES - DIV. PASIVOS - CANJES",
    "08": "DIV. CUPONES - PRIMA JUNTA - AMORTIZACIONES",
    "09": "OPERACIONES DE BOLSA Y/O COMPRA/VENTA VALORES",
    "10": "CHEQUES GASOLINA",
    "11": "CAJERO AUTOMÁTICO",
    "12": "TARJETAS DE CRÉDITO - TARJETAS DÉBITO",
    "13": "OPERACIONES EXTRANJERO",
    "14": "DEVOLUCIONES E IMPAGADOS",
    "15": "NÓMINAS - SEGUROS SOCIALES",
    "16": "TIMBRES - CORRETAJE - PÓLIZA",
    "17": "INTERESES - COMISIONES - CUSTODIA - GASTOS E IMPUESTOS",
    "98": "ANULACIONES - CORRECCIONES ASIENTO",
    "99": "VARIOS",
}

# ISO 4217 numeric -> alpha (Anexo 2 lists the first eleven; the rest are frequent in Spanish treasury).
CURRENCIES = {
    "036": "AUD", "124": "CAD", "208": "DKK", "392": "JPY", "554": "NZD", "578": "NOK", "752": "SEK",
    "756": "CHF", "826": "GBP", "840": "USD", "978": "EUR", "985": "PLN", "203": "CZK", "348": "HUF",
    "946": "RON", "156": "CNY", "484": "MXN", "986": "BRL", "032": "ARS", "152": "CLP", "170": "COP",
    "604": "PEN", "949": "TRY", "356": "INR",
}

# Code pages: Cuaderno 43 ASCII files use CP850 (Ñ = 0xA5); most current bank exports are Latin-1/CP1252.
_CP850_UPPER = set(b"\xa5\x90\xb5\xd6\xe0\xe9\x80")
_LATIN_UPPER = set(b"\xd1\xc9\xc1\xcd\xd3\xda\xc7")


class N43Error(ValueError):
    """Input that cannot be read as a Norma 43 file at all (nothing is charged)."""


def fmt_cents(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    a = abs(cents)
    return f"{sign}{a // 100}.{a % 100:02d}"


def decode_bytes(raw: bytes) -> tuple[str, str]:
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    cp850 = sum(1 for b in raw if b in _CP850_UPPER)
    latin = sum(1 for b in raw if b in _LATIN_UPPER)
    if cp850 > latin:
        return raw.decode("cp850"), "cp850"
    return raw.decode("cp1252", errors="replace"), "cp1252"


def read_input(content: str = "", content_base64: str = "", max_bytes: int = 5_000_000) -> tuple[str, str]:
    """Return (text, encoding). Raises N43Error for empty, oversized or undecodable input."""
    if content and content_base64:
        raise N43Error("Pass the file either as content (text) or as content_base64, not both.")
    if content_base64:
        try:
            raw = base64.b64decode("".join(content_base64.split()), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise N43Error("content_base64 is not valid base64.") from exc
        if len(raw) > max_bytes:
            raise N43Error(f"File is {len(raw)} bytes; the limit is {max_bytes}.")
        return decode_bytes(raw)
    if not content or not content.strip():
        raise N43Error("Empty input: pass the Norma 43 file as content (text) or content_base64 (raw bytes).")
    if len(content) > max_bytes:
        raise N43Error(f"File is {len(content)} characters; the limit is {max_bytes}.")
    return content, "text"


@dataclass
class Movement:
    id: str
    line: int
    office: str
    operation_date: str | None
    value_date: str | None
    common_concept: str
    own_concept: str
    cents: int
    document_number: str
    reference1: str
    reference2: str
    complementary: list[dict[str, str]] = field(default_factory=list)
    original_currency: dict[str, str] | None = None
    balance_after: int = 0
    is_debit: bool = False
    sepa: dict[str, str] | None = None

    @property
    def concept_text(self) -> str:
        parts = [c["text"] for c in self.complementary if c["text"]]
        return " ".join(parts)

    def description(self) -> str:
        if self.sepa:
            keys = ("ordenante_nombre", "acreedor_nombre", "concepto", "concepto_continuacion")
            text = " ".join(self.sepa[k] for k in keys if self.sepa.get(k))
            if text:
                return text
        return self.concept_text or CONCEPTOS_COMUNES.get(self.common_concept, "")

    def search_text(self) -> str:
        """Everything a reconciler may match against: concept lines, SEPA fields, references, document number."""
        sepa = " ".join(v for k, v in (self.sepa or {}).items() if k not in ("tipo", "esquema"))
        return " ".join(x for x in (sepa or self.concept_text, self.reference2, self.reference1,
                                    self.document_number) if x)


@dataclass
class Account:
    index: int
    line: int
    bank: str
    branch: str
    number: str
    start: str | None
    end: str | None
    opening: int
    currency_code: str
    mode: str
    holder: str
    movements: list[Movement] = field(default_factory=list)
    footer: dict[str, Any] | None = None
    closing: int | None = None

    @property
    def ccc(self) -> str | None:
        if re.fullmatch(r"\d{4}", self.bank) and re.fullmatch(r"\d{4}", self.branch) and re.fullmatch(r"\d{10}", self.number):
            return ids.ccc_from_parts(self.bank, self.branch, self.number)
        return None


@dataclass
class ParseResult:
    accounts: list[Account]
    issues: list[dict[str, Any]]
    record_count: int
    has_end_record: bool

    @property
    def valid(self) -> bool:
        return not any(i["severity"] == "error" for i in self.issues)


class _Parser:
    def __init__(self) -> None:
        self.issues: list[dict[str, Any]] = []

    def issue(self, severity: str, line: int | None, code: str, message: str) -> None:
        self.issues.append({"severity": severity, "line": line, "code": code, "message": message})

    def num(self, rec: str, a: int, b: int, name: str, line: int) -> int:
        raw = rec[a - 1:b]
        if not raw.isdigit():
            self.issue("error", line, "BAD_NUMBER", f"{name} (positions {a}-{b}) must be digits, found {raw!r}.")
            return 0
        return int(raw)

    def ymd(self, rec: str, a: int, b: int, name: str, line: int) -> str | None:
        raw = rec[a - 1:b]
        if raw.isdigit():
            yy, mm, dd = int(raw[:2]), int(raw[2:4]), int(raw[4:6])
            try:
                return date(2000 + yy if yy < 80 else 1900 + yy, mm, dd).isoformat()
            except ValueError:
                pass
        self.issue("error", line, "BAD_DATE", f"{name} (positions {a}-{b}) is not a valid YYMMDD date: {raw!r}.")
        return None

    def sign(self, rec: str, pos: int, name: str, line: int, debit_negative: bool = True) -> int:
        raw = rec[pos - 1]
        if raw == "1":
            return -1 if debit_negative else 1
        if raw == "2":
            return 1 if debit_negative else -1
        self.issue("error", line, "BAD_SIGN", f"{name} (position {pos}) must be 1 (debe) or 2 (haber), found {raw!r}.")
        return 1


def _blank_zeros(raw: str) -> str:
    """Zero-filled numeric fields mean 'not informed'."""
    v = raw.strip()
    return "" if not v.strip("0") else v


def _records(text: str) -> list[tuple[int, str]]:
    text = text.replace("\x1a", "")
    lines = [(i + 1, ln) for i, ln in enumerate(re.split(r"\r\n|\n|\r", text)) if ln.strip()]
    if len(lines) == 1 and len(lines[0][1]) > RECORD_LEN:
        blob = lines[0][1]
        return [(i // RECORD_LEN + 1, blob[i:i + RECORD_LEN]) for i in range(0, len(blob), RECORD_LEN)]
    return lines


def parse(text: str) -> ParseResult:
    records = _records(text)
    if not any(rec.startswith("11") for _, rec in records):
        raise N43Error("No account header record (code 11) found: this is not a Norma 43 / Cuaderno 43 file.")
    p = _Parser()
    accounts: list[Account] = []
    cur: Account | None = None
    mov: Movement | None = None
    counted = 0
    has_end = False

    def close_missing(line: int | None) -> None:
        p.issue("error", line, "MISSING_ACCOUNT_FOOTER",
                f"Account {cur.bank}-{cur.branch}-{cur.number} has no final record 33.")

    for line, rec in records:
        if len(rec) > RECORD_LEN:
            if rec[RECORD_LEN:].strip():
                p.issue("error", line, "LINE_TOO_LONG", f"Record has {len(rec)} characters; Norma 43 records have 80.")
            rec = rec[:RECORD_LEN]
        rec = rec.ljust(RECORD_LEN)
        code = rec[:2]
        if has_end:
            p.issue("warning", line, "AFTER_END", "Records after the end-of-file record 88 were ignored.")
            break
        if code != "88":
            counted += 1
        if code == "11":
            if cur is not None and cur.footer is None:
                close_missing(line)
            cur = Account(index=len(accounts) + 1, line=line, bank=rec[2:6], branch=rec[6:10], number=rec[10:20],
                          start=p.ymd(rec, 21, 26, "Fecha inicial", line), end=p.ymd(rec, 27, 32, "Fecha final", line),
                          opening=p.sign(rec, 33, "Clave debe/haber del saldo inicial", line, debit_negative=True)
                          * p.num(rec, 34, 47, "Saldo inicial", line),
                          currency_code=rec[47:50], mode=rec[50], holder=rec[51:77].strip())
            if cur.mode not in ("1", "2", "3"):
                p.issue("warning", line, "BAD_MODE", f"Modalidad de información should be 1, 2 or 3, found {cur.mode!r}.")
            for name, val in (("Clave de entidad", cur.bank), ("Clave de oficina", cur.branch), ("Nº de cuenta", cur.number)):
                if not val.isdigit():
                    p.issue("error", line, "BAD_NUMBER", f"{name} must be digits, found {val!r}.")
            accounts.append(cur)
            mov = None
        elif code == "22":
            if cur is None or cur.footer is not None:
                p.issue("error", line, "ORPHAN_MOVEMENT", "Movement record 22 outside an account (no open record 11).")
                mov = None
                continue
            sgn = p.sign(rec, 28, "Clave debe/haber", line)
            mov = Movement(id=f"a{cur.index}-m{len(cur.movements) + 1}", line=line, office=rec[6:10].strip(),
                           operation_date=p.ymd(rec, 11, 16, "Fecha operación", line),
                           value_date=p.ymd(rec, 17, 22, "Fecha valor", line),
                           common_concept=rec[22:24], own_concept=rec[24:27].strip(),
                           cents=sgn * p.num(rec, 29, 42, "Importe", line),
                           document_number=_blank_zeros(rec[42:52]), reference1=_blank_zeros(rec[52:64]),
                           reference2=rec[64:80].strip(), is_debit=sgn < 0)
            if mov.common_concept not in CONCEPTOS_COMUNES:
                p.issue("warning", line, "UNKNOWN_CONCEPT", f"Concepto común {mov.common_concept!r} is not in Anexo 2.")
            if mov.operation_date and cur.start and cur.end and not cur.start <= mov.operation_date <= cur.end:
                p.issue("warning", line, "DATE_OUT_OF_PERIOD",
                        f"Operation date {mov.operation_date} is outside the statement period {cur.start}..{cur.end}.")
            cur.movements.append(mov)
        elif code == "23":
            if mov is None:
                p.issue("error", line, "ORPHAN_COMPLEMENT", "Complementary record 23 without a preceding movement 22.")
                continue
            dato = rec[2:4]
            if dato not in {"01", "02", "03", "04", "05"}:
                p.issue("warning", line, "BAD_DATA_CODE", f"Código dato of record 23 should be 01-05, found {dato!r}.")
            if any(c["code"] == dato for c in mov.complementary):
                p.issue("warning", line, "DUPLICATE_COMPLEMENT", f"Record 23 with código dato {dato} repeated.")
            body = rec[4:80]
            if cur.mode != "3" and rec[41] != " " and rec[42] != " ":
                body = rec[4:42] + " " + rec[42:80]  # two full 38-char concept fields: keep the words apart
            # Modalidad 3 SEPA layouts (Anexo 4) cross the 38/38 split, so the record is read as one text.
            mov.complementary.append({"code": dato, "raw": rec[4:80], "text": " ".join(body.split())})
        elif code == "24":
            if mov is None:
                p.issue("error", line, "ORPHAN_COMPLEMENT", "Equivalence record 24 without a preceding movement 22.")
                continue
            cur_code = rec[4:7]
            mov.original_currency = {"currency_code": cur_code, "currency": CURRENCIES.get(cur_code, cur_code),
                                     "amount": fmt_cents(p.num(rec, 8, 21, "Importe en divisa origen", line))}
        elif code == "33":
            if cur is None or cur.footer is not None:
                p.issue("error", line, "ORPHAN_FOOTER", "Final record 33 without an open account (record 11).")
                continue
            foot = {"bank": rec[2:6], "branch": rec[6:10], "number": rec[10:20],
                    "debit_count": p.num(rec, 21, 25, "Nº apuntes debe", line),
                    "debit_cents": p.num(rec, 26, 39, "Total importes debe", line),
                    "credit_count": p.num(rec, 40, 44, "Nº apuntes haber", line),
                    "credit_cents": p.num(rec, 45, 58, "Total importes haber", line),
                    "closing": p.sign(rec, 59, "Código saldo final", line) * p.num(rec, 60, 73, "Saldo final", line),
                    "currency_code": rec[73:76]}
            cur.footer, cur.closing = foot, foot["closing"]
            _check_account(p, cur, line)
            mov = None
        elif code == "88":
            has_end = True
            if cur is not None and cur.footer is None:
                close_missing(line)
            if rec[2:20] != "9" * 18:
                p.issue("warning", line, "BAD_END_RECORD", "Positions 3-20 of record 88 should be eighteen 9s.")
            declared = p.num(rec, 21, 26, "Nº de registros", line)
            if declared != counted:
                p.issue("error", line, "RECORD_COUNT_MISMATCH",
                        f"Record 88 declares {declared} records but the file has {counted} (excluding record 88).")
        else:
            p.issue("error", line, "UNKNOWN_RECORD", f"Unknown record code {code!r} (valid: 11, 22, 23, 24, 33, 88).")
            mov = None

    if cur is not None and cur.footer is None and not has_end:
        close_missing(None)
    if not has_end:
        p.issue("error", None, "MISSING_END_RECORD", "The file has no end-of-file record 88 (it may be truncated).")
    for acc in accounts:
        running = acc.opening
        for m in acc.movements:
            running += m.cents
            m.balance_after = running
            m.sepa = sepa_fields(acc, m)
    return ParseResult(accounts=accounts, issues=p.issues, record_count=counted, has_end_record=has_end)


def _check_account(p: _Parser, acc: Account, line: int) -> None:
    f = acc.footer
    assert f is not None
    if (f["bank"], f["branch"], f["number"]) != (acc.bank, acc.branch, acc.number):
        p.issue("error", line, "ACCOUNT_MISMATCH",
                f"Record 33 is for {f['bank']}-{f['branch']}-{f['number']} but record 11 opened "
                f"{acc.bank}-{acc.branch}-{acc.number}.")
    debits = [m for m in acc.movements if m.is_debit]
    credits = [m for m in acc.movements if not m.is_debit]
    debit_sum = -sum(m.cents for m in debits)
    credit_sum = sum(m.cents for m in credits)
    checks = (("debit_count", len(debits), "Nº apuntes debe", str),
              ("debit_cents", debit_sum, "Total importes debe", fmt_cents),
              ("credit_count", len(credits), "Nº apuntes haber", str),
              ("credit_cents", credit_sum, "Total importes haber", fmt_cents))
    for key, actual, label, show in checks:
        if f[key] != actual:
            p.issue("error", line, "TOTALS_MISMATCH",
                    f"{label} in record 33 is {show(f[key])} but the movements add up to {show(actual)}.")
    expected_closing = acc.opening + credit_sum - debit_sum
    if f["closing"] != expected_closing:
        p.issue("error", line, "BALANCE_MISMATCH",
                f"Closing balance {fmt_cents(f['closing'])} != opening {fmt_cents(acc.opening)} + credits "
                f"{fmt_cents(credit_sum)} - debits {fmt_cents(debit_sum)} = {fmt_cents(expected_closing)}.")
    if f["currency_code"] != acc.currency_code:
        p.issue("error", line, "CURRENCY_MISMATCH",
                f"Currency {f['currency_code']} in record 33 differs from {acc.currency_code} in record 11.")


# SEPA layouts of record 23 in modalidad tercera (Anexo 4). Positions are 1-based over the 80-char record.
_SEPA_TRANSFER = {
    "01": (("ordenante_nombre", 5, 70), ("ordenante_codigo", 71, 80)),
    "02": (("referencia_ordenante", 5, 39), ("por_cuenta_de", 40, 80)),
    "03": (("proposito", 5, 8), ("categoria_proposito", 9, 12), ("concepto", 13, 80)),
    "04": (("concepto_continuacion", 5, 76),),
    "05": (("informacion_beneficiario", 5, 80),),
}
_SEPA_DEBIT = {
    "01": (("esquema", 5, 8), ("acreedor_nombre", 9, 78)),
    "02": (("acreedor_identificador", 5, 39), ("mandato_referencia", 40, 74)),
    "03": (("proposito", 5, 8), ("categoria_proposito", 9, 12), ("concepto", 13, 80)),
    "04": (("concepto_continuacion", 5, 76),),
    "05": (("referencia_acreedor", 5, 39), ("deudor_nombre", 40, 80)),
}


def sepa_fields(acc: Account, m: Movement) -> dict[str, str] | None:
    """Anexo 4 view of the 23 records, only for modalidad 3 transfers (04) and direct debits (03)."""
    layout = {"04": _SEPA_TRANSFER, "03": _SEPA_DEBIT}.get(m.common_concept)
    if acc.mode != "3" or layout is None or not m.complementary:
        return None
    out: dict[str, str] = {"tipo": "transferencia SEPA" if m.common_concept == "04" else "adeudo SEPA"}
    for c in m.complementary:
        rec = "23" + c["code"] + c["raw"]
        for name, a, b in layout.get(c["code"], ()):
            val = rec[a - 1:b].strip()
            if val:
                out[name] = val
    return out if len(out) > 1 else None


def movement_dict(acc: Account, m: Movement) -> dict[str, Any]:
    d: dict[str, Any] = {
        "id": m.id, "operation_date": m.operation_date, "value_date": m.value_date,
        "amount": fmt_cents(m.cents), "direction": "cargo" if m.is_debit else "abono",
        "balance_after": fmt_cents(m.balance_after),
        "common_concept": {"code": m.common_concept, "name": CONCEPTOS_COMUNES.get(m.common_concept)},
        "own_concept": m.own_concept, "description": m.description(),
        "document_number": m.document_number, "reference1": m.reference1, "reference2": m.reference2,
    }
    if acc.mode in ("2", "3") and m.office:
        d["office"] = m.office
    if m.complementary:
        d["complementary"] = [{"code": c["code"], "text": c["text"]} for c in m.complementary]
    if m.sepa:
        d["sepa"] = m.sepa
    if m.original_currency:
        d["original_currency"] = m.original_currency
    return d


def account_dict(acc: Account, include_movements: bool, budget: list[int]) -> dict[str, Any]:
    ccc = acc.ccc
    iban = ids.iban_from_ccc(ccc) if ccc else None
    debits = [m.cents for m in acc.movements if m.is_debit]
    credits = [m.cents for m in acc.movements if not m.is_debit]
    by_concept: dict[str, dict[str, Any]] = {}
    for m in acc.movements:
        b = by_concept.setdefault(m.common_concept, {"code": m.common_concept,
                                                     "name": CONCEPTOS_COMUNES.get(m.common_concept),
                                                     "count": 0, "debits": 0, "credits": 0})
        b["count"] += 1
        b["debits" if m.is_debit else "credits"] += abs(m.cents)
    out: dict[str, Any] = {
        "index": acc.index, "bank_code": acc.bank, "branch_code": acc.branch, "account_number": acc.number,
        "ccc": ccc, "iban": iban, "iban_formatted": ids.format_iban(iban) if iban else None,
        "bank_name": ids.bank_name(acc.bank), "holder": acc.holder,
        "currency": CURRENCIES.get(acc.currency_code, acc.currency_code), "currency_code": acc.currency_code,
        "information_mode": acc.mode, "period": {"from": acc.start, "to": acc.end},
        "opening_balance": fmt_cents(acc.opening),
        "closing_balance": fmt_cents(acc.closing) if acc.closing is not None else None,
        "computed_closing_balance": fmt_cents(acc.opening + sum(credits) + sum(debits)),
        "debit_count": len(debits), "debit_total": fmt_cents(-sum(debits)),
        "credit_count": len(credits), "credit_total": fmt_cents(sum(credits)),
        "movement_count": len(acc.movements),
        "by_concept": [{**b, "debits": fmt_cents(b["debits"]), "credits": fmt_cents(b["credits"])}
                       for _, b in sorted(by_concept.items())],
    }
    if include_movements:
        take = acc.movements[:max(0, budget[0])]
        budget[0] -= len(take)
        out["movements"] = [movement_dict(acc, m) for m in take]
        if len(take) < len(acc.movements):
            out["movements_truncated"] = True
    return out


def result_dict(res: ParseResult, include_movements: bool = True, max_movements: int = 2000) -> dict[str, Any]:
    budget = [max_movements]
    errors = sum(1 for i in res.issues if i["severity"] == "error")
    return {
        "valid": res.valid,
        "summary": {"accounts": len(res.accounts), "movements": sum(len(a.movements) for a in res.accounts),
                    "records": res.record_count, "errors": errors, "warnings": len(res.issues) - errors},
        "issues": res.issues[:200],
        "accounts": [account_dict(a, include_movements, budget) for a in res.accounts],
    }


_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _safe_text(v: str) -> str:
    """Neutralise spreadsheet formula injection in free-text columns."""
    return "'" + v if v.startswith(_FORMULA_START) else v


def to_csv(res: ParseResult, delimiter: str = ";", decimal_comma: bool = True) -> str:
    if delimiter not in {";", ",", "\t", "|"}:
        raise N43Error("delimiter must be one of ; , | or a tab.")
    if decimal_comma and delimiter == ",":
        raise N43Error("decimal_comma=True needs a delimiter other than ','.")

    def money(c: int) -> str:
        s = fmt_cents(c)
        return s.replace(".", ",") if decimal_comma else s

    buf = io.StringIO()
    w = csv.writer(buf, delimiter=delimiter, lineterminator="\n")
    w.writerow(["iban", "titular", "id", "fecha_operacion", "fecha_valor", "concepto_comun", "concepto_comun_nombre",
                "concepto_propio", "importe", "saldo", "divisa", "documento", "referencia1", "referencia2",
                "descripcion"])
    for acc in res.accounts:
        ccc = acc.ccc
        iban = ids.iban_from_ccc(ccc) if ccc else f"{acc.bank}-{acc.branch}-{acc.number}"
        cur = CURRENCIES.get(acc.currency_code, acc.currency_code)
        for m in acc.movements:
            w.writerow([iban, _safe_text(acc.holder), m.id, m.operation_date or "", m.value_date or "",
                        m.common_concept, CONCEPTOS_COMUNES.get(m.common_concept, ""), m.own_concept,
                        money(m.cents), money(m.balance_after), cur, m.document_number, m.reference1,
                        _safe_text(m.reference2), _safe_text(m.description())])
    return buf.getvalue()
