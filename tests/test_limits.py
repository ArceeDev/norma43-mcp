"""Reconciliation at scale: bounded memory, refused oversized inputs, groups found late in big files."""
from __future__ import annotations

import pytest

try:
    import resource
except ImportError:  # Windows: no ru_maxrss
    resource = None

from norma43_mcp import reconcile as rc


def _movs(n, cents=5000):
    return rc.build_movements([{"id": f"m{i}", "date": "2026-10-05", "amount": f"{cents / 100:.2f}",
                                "text": f"CUOTA SOCIO {i:05d}"} for i in range(n)])


def _exps(n, cents=5000, refs=True):
    return rc.build_expected([{"id": f"e{i}", "date": "2026-10-05", "amount": f"{cents / 100:.2f}",
                               "reference": f"SOCIO {i:05d}" if refs else ""} for i in range(n)])


@pytest.mark.skipif(resource is None, reason="peak RSS needs the POSIX resource module")
def test_identical_amounts_use_bounded_memory():
    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    out = rc.reconcile(_movs(1000), _exps(1000, refs=False), 10, "0", 1, 50)
    grown_mb = (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - before) / 1024
    assert grown_mb < 150, grown_mb  # was ~600 MB (one Pair per movement x item)
    assert not out["matches"] and out["ambiguous"]


def test_identical_amounts_with_references_all_match():
    out = rc.reconcile(_movs(1000), _exps(1000), 10, "0", 1, 50)
    assert out["summary"]["matched_movements"] == 1000


def test_quadratic_input_is_refused_before_work():
    with pytest.raises(rc.ReconcileError, match="look-alike amounts"):
        rc.check_size(_movs(1500), _exps(1500), "0")


def test_group_search_is_not_exhausted_by_early_movements():
    # 300 transfers each paying two invoices: the old exhaustive search ran out of budget after ~70.
    movs = rc.build_movements([{"id": f"m{i}", "date": "2026-10-05", "amount": f"{(1000 + i) * 3 / 100:.2f}",
                                "text": f"PAGO F{i:04d}A F{i:04d}B"} for i in range(300)])
    exps = rc.build_expected(
        [{"id": f"a{i}", "date": "2026-10-05", "amount": f"{(1000 + i) / 100:.2f}", "reference": f"F{i:04d}A"}
         for i in range(300)]
        + [{"id": f"b{i}", "date": "2026-10-05", "amount": f"{(1000 + i) * 2 / 100:.2f}", "reference": f"F{i:04d}B"}
           for i in range(300)])
    out = rc.reconcile(movs, exps, 10, "0", 4, 50)
    assert not out["summary"]["search_truncated"]
    assert sum(1 for m in out["matches"] if m["type"] == "1:N") == 300
