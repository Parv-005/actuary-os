"""Validation rule primitives (§6.4): pure functions over aggregates — no DB,
no storage, no LLM. The validation agent aggregates DataFrames then calls
these; unit tests target this module directly."""
from __future__ import annotations

import math
import statistics

COVERAGE_BLOCK_PCT = 90.0  # <90% of dates in-period -> BLOCKER (stale file)
LR_SHIFT_WARN_PP = 3.0  # portfolio LR move >= 3pp vs prior -> warning
VOLUME_SHIFT_WARN_PCT = 30.0  # policy volume move >= 30% vs prior -> warning
CONCENTRATION_WARN_SHARE = 0.50  # top-region incurred share >= 50% -> warning
SMALL_SAMPLE_CLAIMS = 20  # claims < 20 per cell -> small-sample caveat
SMALL_SAMPLE_POLICIES = 10  # policies < 10 per cell -> small-sample caveat
MIN_OUTLIER_N = 10  # fewer rows -> outlier check skipped (insufficient data)
OUTLIER_SAMPLE_CAP = 5


def classify_recon(diff_pct: float, pass_pct: float, warn_pct: float) -> tuple[str, str]:
    """(severity, status) by absolute reconciliation difference.

    Thresholds [ID] §6.4: <=pass PASS, <=warn WARNING, else BLOCKER.
    """
    a = abs(diff_pct)
    if a <= pass_pct:
        return "INFO", "PASS"
    if a <= warn_pct:
        return "WARNING", "WARNING"
    return "BLOCKER", "BLOCKER"


def recon_diff_pct(transformed: float, source: float) -> float | None:
    if not source:
        return None
    return (transformed - source) / source * 100.0


def coverage_pct(dated_values: list[str], period: str) -> dict:
    """Share of non-empty YYYY-MM-DD / YYYY-MM values falling inside period."""
    vals = [str(v).strip() for v in dated_values if str(v).strip()]
    inside = sum(1 for v in vals if v[:7] == period)
    pct = (inside / len(vals) * 100.0) if vals else 0.0
    return {"total": len(vals), "in_period": inside, "pct": pct,
            "blocked": pct < COVERAGE_BLOCK_PCT}


def lr_shift_pp(current_lr: float, prior_lr: float) -> float:
    return (current_lr - prior_lr) * 100.0


def volume_change_pct(current: float, prior: float) -> float | None:
    if not prior:
        return None
    return (current - prior) / prior * 100.0


def outlier_flags(amounts: list[float], ids: list[str]) -> dict:
    """Flag claims above max(p99, mean + 3*sd) — 'unusual but not proven
    invalid' (§13.3). Never proof of error; always WARNING, never BLOCKER."""
    n = len(amounts)
    if n < MIN_OUTLIER_N:
        return {"threshold": None, "flagged": [],
                "note": f"insufficient data (n={n})"}
    mean = statistics.fmean(amounts)
    sd = statistics.pstdev(amounts)
    ordered = sorted(amounts)
    p99 = ordered[min(n - 1, math.ceil(0.99 * n) - 1)]
    threshold = max(p99, mean + 3 * sd)
    flagged = [{"claim_id": cid, "incurred_amount": amt}
               for cid, amt in zip(ids, amounts, strict=True) if amt > threshold]
    return {"threshold": threshold, "mean": mean, "std": sd, "p99": p99,
            "flagged": flagged[:OUTLIER_SAMPLE_CAP],
            "flagged_total": len(flagged)}


def small_sample_cells(claim_counts: dict[tuple, int],
                       policy_counts: dict[tuple, int]) -> list[dict]:
    """Cells with claims<20 or policies<10 (§13.3) — caveat, not a finding."""
    cells = set(claim_counts) | set(policy_counts)
    out = []
    for cell in sorted(cells):
        nc, npol = claim_counts.get(cell, 0), policy_counts.get(cell, 0)
        if nc < SMALL_SAMPLE_CLAIMS or npol < SMALL_SAMPLE_POLICIES:
            out.append({"cell": list(cell), "claims": nc, "policies": npol})
    return out


def rename_candidates(prior_labels: set[str],
                      current_labels: set[str]) -> dict[str, list[str]]:
    """Labels that vanished vs prior (possible rename) + labels that appeared."""
    return {"vanished": sorted(prior_labels - current_labels),
            "appeared": sorted(current_labels - prior_labels)}


def concentration(shares: dict[str, float]) -> dict:
    """Top-share concentration of incurred by region."""
    if not shares:
        return {"top": None, "share": 0.0, "warn": False}
    top = max(shares, key=lambda k: shares[k])
    return {"top": top, "share": shares[top],
            "warn": shares[top] >= CONCENTRATION_WARN_SHARE}
