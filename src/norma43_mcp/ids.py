"""Spanish banking identifiers: CCC, IBAN, NIF/NIE/CIF, SEPA creditor identifier, Norma 43 reference 1.

Every function is pure and deterministic. Sources:
- CCC control digits: Spanish banking norm (weights 1,2,4,8,5,10,9,7,3,6; 11 -> 0, 10 -> 1).
- IBAN: ISO 13616 (mod 97-10).
- SEPA creditor identifier (AT-02): EPC262-08 (check digits over national id + country + "00", mod 97-10;
  the 3-character business code is excluded from the check).
- Norma 43 "Referencia 1" control digit: Cuaderno 43 (junio 2012), Anexo 3.
"""

from __future__ import annotations

import re
from typing import Any

CCC_WEIGHTS = (1, 2, 4, 8, 5, 10, 9, 7, 3, 6)
DNI_LETTERS = "TRWAGMYFPDXBNJZSQVHLCKE"
CIF_CONTROL_LETTERS = "JABCDEFGHI"
REF1_WEIGHTS = (2, 3, 4, 5, 6, 7, 8, 9, 2, 3, 4)  # applied from the rightmost digit

# Lengths for the IBAN countries most seen in Spanish treasury files (ISO 13616 registry).
IBAN_LENGTHS = {
    "AD": 24, "AT": 20, "BE": 16, "BG": 22, "CH": 21, "CY": 28, "CZ": 24, "DE": 22, "DK": 18, "EE": 20,
    "ES": 24, "FI": 18, "FR": 27, "GB": 22, "GR": 27, "HR": 21, "HU": 28, "IE": 22, "IS": 26, "IT": 27,
    "LI": 21, "LT": 20, "LU": 20, "LV": 21, "MC": 27, "MT": 31, "NL": 18, "NO": 15, "PL": 28, "PT": 25,
    "RO": 24, "SE": 24, "SI": 19, "SK": 24, "SM": 27,
}

# Bank codes (NRBE) of the largest Spanish entities. Informative only: mergers change brands over time.
BANK_NAMES = {
    "0019": "Deutsche Bank", "0049": "Banco Santander", "0061": "Banca March", "0073": "Openbank",
    "0075": "Banco Popular (hoy Banco Santander)", "0081": "Banco Sabadell", "0128": "Bankinter",
    "0182": "BBVA", "0239": "EVO Banco", "1465": "ING", "1491": "Triodos Bank", "2080": "Abanca",
    "2085": "Ibercaja", "2095": "Kutxabank", "2100": "CaixaBank", "2103": "Unicaja", "3058": "Cajamar",
}


def compact(value: str) -> str:
    """Uppercase and drop spaces, dots, dashes and slashes."""
    return re.sub(r"[\s.\-/]", "", str(value or "")).upper()


def _mod97(text: str) -> int:
    """mod 97 of an alphanumeric string with letters mapped A=10 ... Z=35."""
    rem = 0
    for ch in text:
        for d in str(int(ch, 36)):
            rem = (rem * 10 + int(d)) % 97
    return rem


def ccc_control_digit(ten_digits: str) -> str:
    total = sum(int(d) * w for d, w in zip(ten_digits, CCC_WEIGHTS))
    r = 11 - total % 11
    return "0" if r == 11 else "1" if r == 10 else str(r)


def ccc_check_digits(bank: str, branch: str, account: str) -> str:
    return ccc_control_digit("00" + bank + branch) + ccc_control_digit(account)


def iban_check_digits(country: str, bban: str) -> str:
    return f"{98 - _mod97(bban + country + '00'):02d}"


def ccc_from_parts(bank: str, branch: str, account: str) -> str:
    """Full 20-digit CCC from the bank, branch and 10-digit account carried by Norma 43 (which omits the DC)."""
    return bank + branch + ccc_check_digits(bank, branch, account) + account


def iban_from_ccc(ccc: str) -> str:
    return "ES" + iban_check_digits("ES", ccc) + ccc


def format_iban(iban: str) -> str:
    return " ".join(iban[i:i + 4] for i in range(0, len(iban), 4))


def bank_name(code: str) -> str | None:
    return BANK_NAMES.get(code)


def validate_ccc(value: str) -> dict[str, Any]:
    v = compact(value)
    if not re.fullmatch(r"\d{20}", v):
        return {"kind": "ccc", "valid": False, "input": value, "error": "A CCC has exactly 20 digits."}
    bank, branch, dc, account = v[:4], v[4:8], v[8:10], v[10:]
    expected = ccc_check_digits(bank, branch, account)
    out: dict[str, Any] = {"kind": "ccc", "valid": dc == expected, "input": value, "normalized": v,
                           "bank_code": bank, "branch_code": branch, "control_digits": dc,
                           "account_number": account, "bank_name": bank_name(bank)}
    if dc == expected:
        iban = iban_from_ccc(v)
        out.update(iban=iban, iban_formatted=format_iban(iban))
    else:
        out["error"] = f"Control digits are {dc}; the correct ones are {expected}."
        out["expected_control_digits"] = expected
    return out


def validate_iban(value: str) -> dict[str, Any]:
    v = compact(value)
    out: dict[str, Any] = {"kind": "iban", "input": value, "normalized": v}
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{8,30}", v):
        return {**out, "valid": False, "error": "Not an IBAN: 2 letters, 2 check digits, then the account."}
    country = v[:2]
    want = IBAN_LENGTHS.get(country)
    if want and len(v) != want:
        return {**out, "valid": False, "country": country,
                "error": f"A {country} IBAN has {want} characters, this one has {len(v)}."}
    ok = _mod97(v[4:] + v[:4]) == 1
    out.update(valid=ok, country=country, check_digits=v[2:4], bban=v[4:], iban_formatted=format_iban(v))
    if not want:
        out["note"] = "Country length not in the built-in table; only the mod-97 checksum was verified."
    if not ok:
        out["error"] = f"Checksum fails; for this BBAN the check digits would be {iban_check_digits(country, v[4:])}."
        return out
    if country == "ES":
        ccc = validate_ccc(v[4:])
        out.update(bank_code=ccc["bank_code"], branch_code=ccc["branch_code"], account_number=ccc["account_number"],
                   bank_name=ccc["bank_name"])
        if not ccc["valid"]:
            out["valid"] = False
            out["error"] = "IBAN checksum is right but the inner CCC control digits are wrong: " + ccc["error"]
    return out


def validate_nif(value: str) -> dict[str, Any]:
    v = compact(value)
    if v.startswith("ES") and len(v) == 11:
        v = v[2:]  # intra-EU VAT number: ES + NIF
    out: dict[str, Any] = {"kind": "nif", "input": value, "normalized": v}
    if re.fullmatch(r"\d{8}[A-Z]", v):
        want = DNI_LETTERS[int(v[:8]) % 23]
        return {**out, "valid": v[8] == want, "type": "DNI", "expected_control": want}
    if re.fullmatch(r"[XYZ]\d{7}[A-Z]", v):
        want = DNI_LETTERS[int(str("XYZ".index(v[0])) + v[1:8]) % 23]
        return {**out, "valid": v[8] == want, "type": "NIE", "expected_control": want}
    if re.fullmatch(r"[KLM]\d{7}[A-Z]", v):
        want = DNI_LETTERS[int(v[1:8]) % 23]
        return {**out, "valid": v[8] == want, "type": "NIF especial (K/L/M)", "expected_control": want}
    if re.fullmatch(r"[ABCDEFGHJNPQRSUVW]\d{7}[0-9A-J]", v):
        digits = v[1:8]
        even = sum(int(digits[i]) for i in (1, 3, 5))
        odd = sum(sum(divmod(int(digits[i]) * 2, 10)) for i in (0, 2, 4, 6))
        d = (10 - (even + odd) % 10) % 10
        as_digit, as_letter = str(d), CIF_CONTROL_LETTERS[d]
        first, ctrl = v[0], v[8]
        if first in "ABEH":
            allowed = {as_digit}
        elif first in "NPQRSW":
            allowed = {as_letter}
        else:
            allowed = {as_digit, as_letter}
        return {**out, "valid": ctrl in allowed, "type": "CIF (persona jurídica)",
                "expected_control": " or ".join(sorted(allowed))}
    return {**out, "valid": False, "error": "Not a DNI, NIE or CIF pattern."}


def creditor_identifier(nif: str, business_code: str = "000", country: str = "ES") -> dict[str, Any]:
    """SEPA creditor identifier (AT-02) for a Spanish NIF: ES + check digits + business code + NIF."""
    nid = re.sub(r"[^A-Z0-9]", "", str(nif or "").upper())
    bc = re.sub(r"[^A-Z0-9]", "", str(business_code or "").upper())
    if not nid or len(nid) > 28:
        raise ValueError("Pass the creditor's NIF (national identifier).")
    if len(bc) != 3:
        raise ValueError("business_code must be exactly 3 letters or digits (use '000' if your bank gave none).")
    country = country.upper()
    if not re.fullmatch(r"[A-Z]{2}", country):
        raise ValueError("country must be a 2-letter ISO code.")
    check = f"{98 - _mod97(nid + country + '00'):02d}"
    out: dict[str, Any] = {"creditor_id": f"{country}{check}{bc}{nid}", "country": country, "check_digits": check,
                           "business_code": bc, "national_id": nid}
    if country == "ES":
        nif_check = validate_nif(nid)
        out["nif_valid"] = nif_check["valid"]
        if not nif_check["valid"]:
            out["warning"] = "The NIF itself does not pass its control check; verify it before using this identifier."
    return out


def validate_creditor_id(value: str) -> dict[str, Any]:
    v = re.sub(r"[^A-Z0-9]", "", str(value or "").upper())
    out: dict[str, Any] = {"kind": "creditor_id", "input": value, "normalized": v}
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{3}[A-Z0-9]{1,28}", v):
        return {**out, "valid": False, "error": "Not a SEPA creditor identifier (CC + 2 digits + 3 chars + id)."}
    country, check, bc, nid = v[:2], v[2:4], v[4:7], v[7:]
    want = f"{98 - _mod97(nid + country + '00'):02d}"
    out.update(valid=check == want, country=country, check_digits=check, business_code=bc, national_id=nid)
    if check != want:
        out["error"] = f"Check digits are {check}; for this national id they must be {want}."
    elif country == "ES":
        out["nif_valid"] = validate_nif(nid)["valid"]
    return out


def reference1_check_digit(eleven_digits: str) -> str:
    """Control digit of Norma 43 'Referencia 1' (Anexo 3): weights 2..9,2,3,4 from the right, remainder of /11."""
    total = sum(int(d) * w for d, w in zip(reversed(eleven_digits), REF1_WEIGHTS))
    r = total % 11
    return "0" if r == 10 else str(r)


def validate_reference1(value: str) -> dict[str, Any]:
    v = compact(value)
    if not re.fullmatch(r"\d{12}", v):
        return {"kind": "reference1", "valid": False, "input": value,
                "error": "Norma 43 Referencia 1 is 12 digits (11 + control digit)."}
    want = reference1_check_digit(v[:11])
    return {"kind": "reference1", "valid": v[11] == want, "input": value, "normalized": v,
            "expected_control": want}


def detect_and_validate(value: str, kind: str = "auto") -> dict[str, Any]:
    kinds = {"iban": validate_iban, "ccc": validate_ccc, "nif": validate_nif,
             "creditor_id": validate_creditor_id, "reference1": validate_reference1}
    kind = (kind or "auto").lower()
    if kind != "auto":
        if kind not in kinds:
            raise ValueError(f"kind must be one of: auto, {', '.join(kinds)}.")
        return kinds[kind](value)
    v = compact(value)
    if re.fullmatch(r"\d{20}", v):
        return validate_ccc(v)
    if re.fullmatch(r"\d{12}", v):
        return validate_reference1(v)
    if re.fullmatch(r"(ES)?[0-9XYZKLMABCDEFGHJNPQRSUVW]\d{7}[0-9A-Z]", v):
        return validate_nif(v)
    if re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]+", v):
        iban = validate_iban(v)
        if iban["valid"] or IBAN_LENGTHS.get(v[:2]) == len(v):
            return iban
        cid = validate_creditor_id(v)
        return cid if cid["valid"] else iban
    return {"kind": "unknown", "valid": False, "input": value,
            "error": "Could not recognise an IBAN, CCC, NIF/NIE/CIF, SEPA creditor id or Norma 43 reference."}
