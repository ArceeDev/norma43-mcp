"""Deterministic bank reconciliation: bank movements vs expected items (invoices, receipts, payroll, ledger).

Design rules, in order of priority:
1. Never guess. Equal-score alternatives are reported as `ambiguous`, never silently picked.
2. Money is integer cents. Amount must match (within an optional tolerance) and share the sign.
3. Every match carries the evidence that produced it (`reasons`) and a 0-100 score.
4. Group matches (one transfer paying several invoices, or several partial payments of one invoice)
   need a unique exact-sum combination AND corroborating reference/name evidence.
"""

from __future__ import annotations

import bisect
import heapq
import itertools
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from .n43 import ParseResult, fmt_cents

MAX_ITEMS = 5000
MAX_GROUP_CANDIDATES = 20
MAX_PAIRS_PER_MOVEMENT = 25
MAX_CANDIDATE_PAIRS = 2_000_000  # look-alike amount pairs scanned by the 1:1 pass
COMBINATION_BUDGET = 1_500_000
GROUP_MIN_SCORE = 60
_STOP = {
    "SL", "SA", "SLU", "SLL", "SAU", "SC", "CB", "SCOOP", "COOP", "SLNE", "SRL", "SAS", "LTD", "LLC", "INC", "GMBH",
    "BV", "DE", "DEL", "LA", "LAS", "LOS", "EL", "Y", "THE", "AND", "CIA", "SOCIEDAD", "LIMITADA", "ANONIMA",
}


class ReconcileError(ValueError):
    """Bad input (nothing is charged)."""


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    return text.upper()


def _compact(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", _plain(text))


def _token_list(text: str) -> list[str]:
    out: list[str] = []
    for t in re.split(r"[^A-Z0-9]+", _plain(text)):
        if len(t) >= 3 and t not in _STOP and t not in out:
            out.append(t)
    return out


def _tokens(text: str) -> set[str]:
    return set(_token_list(text))


def parse_amount(value: Any, field: str) -> int:
    if isinstance(value, bool) or value is None or value == "":
        raise ReconcileError(f"{field}: amount is required.")
    s = str(value).strip().replace(" ", "").replace("€", "")
    if isinstance(value, str) and "," in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    try:
        d = Decimal(s)
    except InvalidOperation as exc:
        raise ReconcileError(f"{field}: {value!r} is not an amount.") from exc
    if not d.is_finite():
        raise ReconcileError(f"{field}: {value!r} is not an amount.")
    cents = (d * 100).quantize(Decimal("1"))
    if abs(d * 100 - cents) > Decimal("0.0001"):
        raise ReconcileError(f"{field}: {value!r} has more than 2 decimals.")
    return int(cents)


def parse_date(value: Any, field: str) -> date:
    s = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y%m%d"):
        try:
            return datetime.strptime(s[:10] if fmt == "%Y-%m-%d" else s, fmt).date()
        except ValueError:
            continue
    raise ReconcileError(f"{field}: {value!r} is not a date (use YYYY-MM-DD or DD/MM/YYYY).")


@dataclass
class Mov:
    id: str
    order: int
    day: date
    cents: int
    text: str
    compact: str
    tokens: set[str]

    def brief(self) -> dict[str, Any]:
        return {"id": self.id, "date": self.day.isoformat(), "amount": fmt_cents(self.cents), "text": self.text[:160]}


@dataclass
class Exp:
    id: str
    order: int
    day: date
    cents: int
    reference: str
    ref_compact: str
    counterparty: str
    name_tokens: list[str]

    def brief(self) -> dict[str, Any]:
        d = {"id": self.id, "date": self.day.isoformat(), "amount": fmt_cents(self.cents)}
        if self.reference:
            d["reference"] = self.reference
        if self.counterparty:
            d["counterparty"] = self.counterparty
        return d


def _ids(items: list[dict[str, Any]], prefix: str, label: str) -> list[str]:
    out, seen = [], set()
    for i, it in enumerate(items, 1):
        if not isinstance(it, dict):
            raise ReconcileError(f"{label}[{i}] must be an object.")
        ident = str(it.get("id") or f"{prefix}{i}").strip()
        if ident in seen:
            raise ReconcileError(f"{label}: duplicate id {ident!r}.")
        seen.add(ident)
        out.append(ident)
    return out


def build_movements(items: list[dict[str, Any]]) -> list[Mov]:
    if len(items) > MAX_ITEMS:
        raise ReconcileError(f"At most {MAX_ITEMS} movements per call.")
    out = []
    for i, (ident, it) in enumerate(zip(_ids(items, "m", "movements"), items)):
        text = " ".join(str(it.get(k) or "") for k in ("text", "description", "concept", "reference")).strip()
        out.append(Mov(ident, i, parse_date(it.get("date"), f"movements[{ident}].date"),
                       parse_amount(it.get("amount"), f"movements[{ident}].amount"), text, _compact(text), _tokens(text)))
    return out


def movements_from_n43(res: ParseResult) -> list[Mov]:
    out: list[Mov] = []
    for acc in res.accounts:
        for m in acc.movements:
            day = m.operation_date or m.value_date
            if day is None:
                raise ReconcileError(f"Movement {m.id} has no valid date; fix the Norma 43 file first.")
            text = m.search_text()
            out.append(Mov(m.id, len(out), date.fromisoformat(day), m.cents, text, _compact(text), _tokens(text)))
    if len(out) > MAX_ITEMS:
        raise ReconcileError(f"At most {MAX_ITEMS} movements per call.")
    return out


def build_expected(items: list[dict[str, Any]]) -> list[Exp]:
    if not items:
        raise ReconcileError("Pass at least one expected item (invoice, receipt, payment...).")
    if len(items) > MAX_ITEMS:
        raise ReconcileError(f"At most {MAX_ITEMS} expected items per call.")
    out = []
    for i, (ident, it) in enumerate(zip(_ids(items, "e", "expected"), items)):
        ref = str(it.get("reference") or "").strip()
        name = str(it.get("counterparty") or it.get("name") or "").strip()
        when = it.get("date") or it.get("due_date")
        out.append(Exp(ident, i, parse_date(when, f"expected[{ident}].date"),
                       parse_amount(it.get("amount"), f"expected[{ident}].amount"), ref, _compact(ref), name,
                       _token_list(name)))
    return out


def _ref_found(e: Exp, m: Mov) -> bool:
    ref = e.ref_compact
    if len(ref) < 4 or (ref.isdigit() and len(ref) < 6):
        return False  # too short to be distinctive
    start = m.compact.find(ref)
    while start != -1:
        end = start + len(ref)
        if not ref.isdigit():
            return True
        before = m.compact[start - 1] if start else ""
        after = m.compact[end] if end < len(m.compact) else ""
        if not before.isdigit() and not after.isdigit():
            return True
        start = m.compact.find(ref, start + 1)
    return False


def _evidence(e: Exp, m: Mov) -> tuple[int, list[str]]:
    """Reference (35) + counterparty (up to 15) points, with reasons."""
    pts, why = 0, []
    if _ref_found(e, m):
        pts += 35
        why.append(f"referencia '{e.reference}' en el concepto")
    if e.name_tokens:
        hit = [t for t in e.name_tokens if t in m.tokens]
        if hit:
            pts += round(15 * len(hit) / len(e.name_tokens))
            why.append(f"contraparte: {' '.join(hit)}")
    return pts, why


@dataclass
class Pair:
    m: Mov
    e: Exp
    score: int
    days: int
    reasons: list[str]


def _confidence(score: int) -> str:
    return "alta" if score >= 80 else "media" if score >= 55 else "baja"


class Reconciler:
    def __init__(self, movs: list[Mov], exps: list[Exp], window: int, tolerance: int, max_group: int,
                 min_score: int) -> None:
        self.movs, self.exps = movs, exps
        self.window, self.tol, self.max_group, self.min_score = window, tolerance, max_group, min_score
        self.used_m: set[str] = set()
        self.used_e: set[str] = set()
        self.matches: list[dict[str, Any]] = []
        self.ambiguous: list[dict[str, Any]] = []
        self.suggestions: list[dict[str, Any]] = []
        self.budget = COMBINATION_BUDGET
        self.truncated = False
        self._pending_suggestions: list[Pair] = []

    # ---- 1:1 -------------------------------------------------------------------------------------
    def _candidates(self, sorted_e: list[Exp], keys: list[int]) -> Iterable[tuple[Mov, Exp, int]]:
        for m in self.movs:
            lo, hi = bisect.bisect_left(keys, m.cents - self.tol), bisect.bisect_right(keys, m.cents + self.tol)
            for e in sorted_e[lo:hi]:
                days = abs((m.day - e.day).days)
                if (e.cents > 0) == (m.cents > 0) and e.cents != 0 and days <= self.window:
                    yield m, e, days

    def _pairs(self) -> list[Pair]:
        """Scored 1:1 candidates. Memory stays bounded: each movement keeps its best MAX_PAIRS_PER_MOVEMENT."""
        sorted_e = sorted(self.exps, key=lambda e: e.cents)
        keys = [e.cents for e in sorted_e]
        n_m: dict[str, int] = {}
        n_e: dict[str, int] = {}
        for m, e, _ in self._candidates(sorted_e, keys):
            n_m[m.id] = n_m.get(m.id, 0) + 1
            n_e[e.id] = n_e.get(e.id, 0) + 1
        pairs: list[Pair] = []
        per_m: list[Pair] = []
        current: Mov | None = None

        def flush() -> None:
            if len(per_m) > MAX_PAIRS_PER_MOVEMENT:
                per_m.sort(key=lambda p: (-p.score, p.days, p.e.order))
                del per_m[MAX_PAIRS_PER_MOVEMENT:]
            pairs.extend(per_m)
            per_m.clear()

        for m, e, days in self._candidates(sorted_e, keys):
            if m is not current:
                flush()
                current = m
            diff = abs(m.cents - e.cents)
            score, why = (40, ["importe exacto"]) if diff == 0 else (30, [f"importe con diferencia de {fmt_cents(diff)}"])
            pts, ev = _evidence(e, m)
            score += pts
            why += ev
            score += round(10 * (1 - days / self.window)) if self.window else 10
            why.append("misma fecha" if days == 0 else f"{days} días de diferencia")
            if n_m[m.id] == 1 and n_e[e.id] == 1:
                score += 10
                why.append("único candidato posible")
            per_m.append(Pair(m, e, min(score, 100), days, why))
        flush()
        pairs.sort(key=lambda p: (-p.score, p.days, p.m.order, p.e.order))
        return pairs

    def _free(self, p: Pair) -> bool:
        return p.m.id not in self.used_m and p.e.id not in self.used_e

    def one_to_one(self) -> None:
        pairs = self._pairs()
        by_m: dict[str, list[Pair]] = {}
        by_e: dict[str, list[Pair]] = {}
        for p in pairs:
            by_m.setdefault(p.m.id, []).append(p)
            by_e.setdefault(p.e.id, []).append(p)
        for p in pairs:
            if not self._free(p) or p.score < self.min_score:
                continue
            rivals = [q for q in by_m[p.m.id] + by_e[p.e.id]
                      if q is not p and q.score == p.score and self._free(q)]
            if rivals:
                group = [p, *rivals]
                m_ids = sorted({q.m.id for q in group}, key=lambda i: next(q.m.order for q in group if q.m.id == i))
                e_ids = sorted({q.e.id for q in group}, key=lambda i: next(q.e.order for q in group if q.e.id == i))
                self.ambiguous.append({"movement_ids": m_ids, "expected_ids": e_ids, "score": p.score,
                                       "reason": "Varias parejas con la misma evidencia; decide una persona."})
                self.used_m.update(m_ids)
                self.used_e.update(e_ids)
                continue
            self._match("1:1", [p.m], [p.e], p.score, p.reasons)
        self._pending_suggestions = [p for p in pairs if p.score < self.min_score]

    # ---- groups ----------------------------------------------------------------------------------
    def _combos(self, target: Any, pool: list[Any], cents_of: Any) -> list[tuple[Any, ...]]:
        """Subsets of 2..max_group items summing to target (within tolerance), by pruned depth-first search.

        Items share the target's sign and are smaller than it, so sorting by size lets a branch stop as soon
        as its sum overshoots. Every visited node spends one unit of the shared search budget.
        """
        items = sorted(pool, key=lambda x: (abs(cents_of(x)), pool.index(x)))
        size = [abs(cents_of(x)) for x in items]
        goal, found, chosen = abs(target), [], []
        order = {id(x): i for i, x in enumerate(pool)}

        def dfs(start: int, partial: int) -> bool:
            for i in range(start, len(items)):
                total = partial + size[i]
                if total > goal + self.tol:
                    return False  # sorted: every later item overshoots too
                self.budget -= 1
                if self.budget <= 0:
                    self.truncated = True
                    return True
                chosen.append(items[i])
                if len(chosen) >= 2 and abs(total - goal) <= self.tol:
                    found.append(tuple(sorted(chosen, key=lambda x: order[id(x)])))
                    if len(found) > 5:
                        return True
                if len(chosen) < self.max_group and dfs(i + 1, total):
                    return True
                chosen.pop()
            return False

        dfs(0, 0)
        return found

    def _group_score(self, ev: list[tuple[int, list[str]]], days: int, n: int) -> tuple[int, list[str]]:
        score, why = 45, [f"suma exacta de {n} partidas"]
        refs = sum(1 for pts, _ in ev if pts >= 35)
        if refs:
            score += round(35 * refs / len(ev))
            why.append(f"{refs}/{len(ev)} referencias en el concepto")
        names = [pts - 35 if pts >= 35 else pts for pts, _ in ev]
        if any(names):
            score += round(sum(names) / len(names))
            why.append("contraparte coincide")
        score += round(5 * (1 - days / self.window)) if self.window else 5
        why.append(f"hasta {days} días de diferencia")
        return min(score, 100), why

    def one_to_many(self) -> None:
        """One movement settles several expected items (e.g. a transfer paying 3 invoices)."""
        for m in self.movs:
            if m.id in self.used_m or self.truncated:
                continue
            pool = [e for e in self.exps if e.id not in self.used_e and (e.cents > 0) == (m.cents > 0)
                    and abs(e.cents) < abs(m.cents) and abs((m.day - e.day).days) <= self.window]
            pool = heapq.nsmallest(MAX_GROUP_CANDIDATES, pool,
                                   key=lambda e: (-_evidence(e, m)[0], abs((m.day - e.day).days), e.order))
            sols = self._combos(m.cents, pool, lambda e: e.cents)
            self._decide(sols, lambda combo: ([m], list(combo)), "1:N")

    def many_to_one(self) -> None:
        """Several movements settle one expected item (partial payments)."""
        for e in self.exps:
            if e.id in self.used_e or self.truncated:
                continue
            pool = [m for m in self.movs if m.id not in self.used_m and (e.cents > 0) == (m.cents > 0)
                    and abs(m.cents) < abs(e.cents) and abs((m.day - e.day).days) <= self.window]
            pool = heapq.nsmallest(MAX_GROUP_CANDIDATES, pool,
                                   key=lambda m: (-_evidence(e, m)[0], abs((m.day - e.day).days), m.order))
            sols = self._combos(e.cents, pool, lambda m: m.cents)
            self._decide(sols, lambda combo: (list(combo), [e]), "N:1")

    def _decide(self, sols: list[tuple[Any, ...]], split: Any, kind: str) -> None:
        # No solution, more than 5 (the set is not unique enough to trust) or an interrupted search: no match.
        if not sols or len(sols) > 5 or self.truncated:
            return
        scored = []
        for combo in sols:
            ms, es = split(combo)
            ev = [_evidence(e, m) for m in ms for e in es]
            days = max(abs((m.day - e.day).days) for m in ms for e in es)
            score, why = self._group_score(ev, days, len(combo))
            scored.append((score, ms, es, why))
        scored.sort(key=lambda s: -s[0])
        best = scored[0]
        tie = len(scored) > 1 and scored[1][0] == best[0]
        if best[0] >= max(self.min_score, GROUP_MIN_SCORE) and not tie:
            self._match(kind, best[1], best[2], best[0], best[3])
        elif tie and best[0] >= max(self.min_score, GROUP_MIN_SCORE):
            m_ids = sorted({m.id for s in scored if s[0] == best[0] for m in s[1]})
            e_ids = sorted({e.id for s in scored if s[0] == best[0] for e in s[2]})
            self.ambiguous.append({"movement_ids": m_ids, "expected_ids": e_ids, "score": best[0],
                                   "reason": f"Varias combinaciones {kind} suman el mismo importe."})
            self.used_m.update(m_ids)
            self.used_e.update(e_ids)
        else:
            self.suggestions.append({"type": kind, "movement_ids": [m.id for m in best[1]],
                                     "expected_ids": [e.id for e in best[2]], "score": best[0],
                                     "reasons": best[3] + ["sin evidencia suficiente para confirmarla"]})

    # ---- output ----------------------------------------------------------------------------------
    def _match(self, kind: str, ms: list[Mov], es: list[Exp], score: int, reasons: list[str]) -> None:
        self.used_m.update(m.id for m in ms)
        self.used_e.update(e.id for e in es)
        self.matches.append({"type": kind, "movement_ids": [m.id for m in ms], "expected_ids": [e.id for e in es],
                             "amount": fmt_cents(sum(m.cents for m in ms)), "score": score,
                             "confidence": _confidence(score), "reasons": reasons})

    def run(self) -> dict[str, Any]:
        self.one_to_one()
        if self.max_group >= 2:
            self.one_to_many()
            self.many_to_one()
        seen = set()
        for p in self._pending_suggestions:
            if self._free(p) and p.m.id not in seen:
                seen.add(p.m.id)
                self.suggestions.append({"type": "1:1", "movement_ids": [p.m.id], "expected_ids": [p.e.id],
                                         "score": p.score, "reasons": p.reasons + ["por debajo de min_score"]})
        amb_m = {i for a in self.ambiguous for i in a["movement_ids"]}
        amb_e = {i for a in self.ambiguous for i in a["expected_ids"]}
        matched_m = self.used_m - amb_m
        matched_e = self.used_e - amb_e
        un_m = [m for m in self.movs if m.id not in self.used_m]
        un_e = [e for e in self.exps if e.id not in self.used_e]

        def split(items: Iterable[Any]) -> dict[str, str]:
            cents = [x.cents for x in items]
            return {"in": fmt_cents(sum(c for c in cents if c > 0)), "out": fmt_cents(-sum(c for c in cents if c < 0))}

        return {
            "summary": {
                "movements": len(self.movs), "expected": len(self.exps),
                "matched_movements": len(matched_m), "matched_expected": len(matched_e),
                "matched_amount": split(m for m in self.movs if m.id in matched_m),
                "ambiguous_groups": len(self.ambiguous),
                "unmatched_movements": len(un_m), "unmatched_movements_amount": split(un_m),
                "unmatched_expected": len(un_e), "unmatched_expected_amount": split(un_e),
                "search_truncated": self.truncated,
            },
            "matches": self.matches,
            "ambiguous": self.ambiguous,
            "suggestions": self.suggestions[:200],
            "unmatched_movements": [m.brief() for m in un_m[:500]],
            "unmatched_expected": [e.brief() for e in un_e[:500]],
        }


def check_options(date_window_days: int = 10, amount_tolerance: Any = 0, max_group_size: int = 4,
                  min_score: int = 50) -> tuple[int, int, int, int]:
    """Validate options up front so bad input is rejected before any debit."""
    window = int(date_window_days)
    if not 0 <= window <= 180:
        raise ReconcileError("date_window_days must be between 0 and 180.")
    tol = parse_amount(amount_tolerance or 0, "amount_tolerance")
    if not 0 <= tol <= 1000:
        raise ReconcileError("amount_tolerance must be between 0 and 10.00.")
    group = int(max_group_size)
    if not 1 <= group <= 5:
        raise ReconcileError("max_group_size must be between 1 (no grouping) and 5.")
    ms = int(min_score)
    if not 0 <= ms <= 100:
        raise ReconcileError("min_score must be between 0 and 100.")
    return window, tol, group, ms


def check_size(movs: list[Mov], exps: list[Exp], amount_tolerance: Any = 0) -> None:
    """Refuse (before any debit) inputs whose look-alike amounts would make the 1:1 pass quadratic."""
    tol = parse_amount(amount_tolerance or 0, "amount_tolerance")
    keys = sorted(e.cents for e in exps)
    n = sum(bisect.bisect_right(keys, m.cents + tol) - bisect.bisect_left(keys, m.cents - tol) for m in movs)
    if n > MAX_CANDIDATE_PAIRS:
        raise ReconcileError(f"Too many look-alike amounts: {n} movement/item pairs to compare (max "
                             f"{MAX_CANDIDATE_PAIRS}). Split the input by month or account, or lower "
                             "amount_tolerance; nothing was charged.")


def reconcile(movs: list[Mov], exps: list[Exp], date_window_days: int = 10, amount_tolerance: Any = 0,
              max_group_size: int = 4, min_score: int = 50) -> dict[str, Any]:
    if not movs:
        raise ReconcileError("No bank movements to reconcile.")
    check_size(movs, exps, amount_tolerance)
    return Reconciler(movs, exps, *check_options(date_window_days, amount_tolerance, max_group_size, min_score)).run()
