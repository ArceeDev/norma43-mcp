"""Writes Norma 43 files field by field from the Cuaderno 43 layout, independently of the parser under test."""

from __future__ import annotations

from dataclasses import dataclass, field


def _a(text: str, n: int) -> str:
    return f"{text:<{n}}"[:n]


def r11(bank: str, branch: str, acct: str, start: str, end: str, opening: int, cur: str, mode: str, name: str) -> str:
    sign = "1" if opening < 0 else "2"
    s = "11" + bank + branch + acct + start + end + sign + f"{abs(opening):014d}" + cur + mode + _a(name, 26) + "   "
    assert len(s) == 80, len(s)
    return s


def r22(op: str, val: str, comun: str, propio: str, cents: int, doc: str = "", ref1: str = "", ref2: str = "",
        office: str = "0418") -> str:
    dh = "1" if cents < 0 else "2"
    s = ("22" + "    " + office + op + val + comun + propio + dh + f"{abs(cents):014d}" + f"{doc:0>10}"
         + f"{ref1:0>12}" + _a(ref2, 16))
    assert len(s) == 80, len(s)
    return s


def r23(code: str, body: str) -> str:
    s = "23" + code + _a(body, 76)
    assert len(s) == 80
    return s


def r23_fields(code: str, t1: str, t2: str = "") -> str:
    return r23(code, _a(t1, 38) + _a(t2, 38))


def r24(cur: str, cents: int) -> str:
    s = "2401" + cur + f"{cents:014d}" + " " * 59
    assert len(s) == 80
    return s


def r33(bank: str, branch: str, acct: str, nd: int, td: int, nh: int, th: int, final: int, cur: str) -> str:
    sign = "1" if final < 0 else "2"
    s = ("33" + bank + branch + acct + f"{nd:05d}" + f"{td:014d}" + f"{nh:05d}" + f"{th:014d}" + sign
         + f"{abs(final):014d}" + cur + "    ")
    assert len(s) == 80, len(s)
    return s


def r88(n: int) -> str:
    return "88" + "9" * 18 + f"{n:06d}" + " " * 54


@dataclass
class Mov:
    op: str
    comun: str
    cents: int
    extra: list[str] = field(default_factory=list)  # ready-made 23/24 records
    propio: str = "000"
    val: str = ""
    doc: str = ""
    ref1: str = ""
    ref2: str = ""


def account(movs: list[Mov], bank: str = "2100", branch: str = "0418", acct: str = "0200051332",
            start: str = "261001", end: str = "261031", opening: int = 150000, cur: str = "978", mode: str = "3",
            name: str = "ACME SOLUCIONES SL") -> list[str]:
    lines = [r11(bank, branch, acct, start, end, opening, cur, mode, name)]
    for m in movs:
        lines.append(r22(m.op, m.val or m.op, m.comun, m.propio, m.cents, m.doc, m.ref1, m.ref2))
        lines.extend(m.extra)
    debits = [-m.cents for m in movs if m.cents < 0]
    credits = [m.cents for m in movs if m.cents >= 0]
    final = opening + sum(credits) - sum(debits)
    lines.append(r33(bank, branch, acct, len(debits), sum(debits), len(credits), sum(credits), final, cur))
    return lines


def file(*accounts: list[str], eol: str = "\r\n") -> str:
    lines = [ln for acc in accounts for ln in acc]
    return eol.join(lines + [r88(len(lines))]) + eol


def sepa_transfer(name: str, concept: str, purpose: str = "", category: str = "") -> list[str]:
    """Anexo 4 transfer layout: 01 = ordenante (66) + código (10); 03 = propósito, categoría, concepto (68)."""
    return [r23("01", _a(name, 66) + _a("", 10)), r23("03", _a(purpose, 4) + _a(category, 4) + _a(concept, 68))]


def sepa_debit(creditor: str, creditor_id: str, mandate: str, concept: str) -> list[str]:
    """Anexo 4 direct-debit layout: 01 = esquema + acreedor; 02 = id acreedor + mandato; 03 = concepto."""
    return [r23("01", "CORE" + _a(creditor, 70) + "  "), r23("02", _a(creditor_id, 35) + _a(mandate, 35) + " " * 6),
            r23("03", " " * 8 + _a(concept, 68))]


def sample_statement() -> str:
    """October 2026 statement used across the tests (opening 1,500.00 EUR)."""
    return file(account([
        Mov("261002", "04", 121000, sepa_transfer("CLIENTE UNO SA", "PAGO FACTURA F2026-0101")),
        Mov("261005", "03", -4530, sepa_debit("ELECTRICA DEMO SA", "ES15000B12345674", "MAND-778", "RECIBO LUZ SEPT")),
        Mov("261007", "04", 242000, sepa_transfer("CLIENTE DOS SL", "FRAS F2026-0102 F2026-0103")),
        Mov("261010", "17", -1250, [r23_fields("01", "COMISION MANTENIMIENTO")], ref2="CUOTA OCT"),
        Mov("261015", "04", 60000, sepa_transfer("CLIENTE TRES SL", "A CUENTA F2026-0104")),
        Mov("261020", "04", 40000, sepa_transfer("CLIENTE TRES SL", "RESTO F2026-0104")),
        Mov("261025", "15", -185000, [r23_fields("01", "NOMINA OCTUBRE", "JUAN PEREZ GARCIA")]),
        Mov("261028", "04", -30000, sepa_transfer("PROVEEDOR XYZ SL", "PEDIDO 5521")),
    ]))
