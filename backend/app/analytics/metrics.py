"""Metric catalog math (§6.5): pure pandas over processed frames — no DB, no LLM.

Levels: portfolio, product, region, product/segment, product/segment/region.
Keys: loss_ratio, claim_frequency, claim_severity (+ ave_variance and
deterioration_contribution derived rows). Undefined values are NEVER
infinity/NaN — value None + undefined_reason (spec §11).
"""
from __future__ import annotations

import json

import pandas as pd

from app.analytics.decomposition import oat_contributions

MODULE_VERSION = "m1"

LEVELS: list[tuple[str, ...]] = [
    (),
    ("product",),
    ("region",),
    ("product", "segment"),
    ("product", "segment", "region"),
]

DIM_COLS = ("product", "segment", "region")
FINEST = ("product", "segment", "region")

SMALL_SAMPLE_CLAIMS = 20
SMALL_SAMPLE_POLICIES = 10

FORMULAS = {
    "loss_ratio": "loss_ratio = Σincurred/Σearned_premium",
    "claim_frequency": "claim_frequency = claim_count/earned_exposure_units",
    "claim_severity": "claim_severity = Σincurred/claim_count",
    "ave_variance": "ave_variance = (actual_loss_ratio - expected_loss_ratio) * 100 (pp)",
    "deterioration_contribution": (
        "contribution_i = (LR_i,cur − LR_i,prev) × premium_weight_i,prev,"
        " normalized to % of total movement"
    ),
}

UNITS = {
    "loss_ratio": "ratio",
    "claim_frequency": "per_unit",
    "claim_severity": "inr",
    "ave_variance": "pp",
    "deterioration_contribution": "pct",
}

UNDEFINED_REASONS = {
    "loss_ratio": "premium base is zero",
    "claim_frequency": "exposure base is zero",
    "claim_severity": "no claims in cell",
}


def _clean_dim(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for c in DIM_COLS:
        if c in df.columns:
            df[c] = df[c].fillna("").astype(str).str.strip()
        else:
            df[c] = ""
    return df


def aggregate_frames(
    claims: pd.DataFrame, premium: pd.DataFrame, exposure: pd.DataFrame
) -> dict[tuple, dict[tuple, dict]]:
    """Roll processed frames into per-cell aggregates at every level.

    Returns {level: {cell_tuple: {incurred, earned, exp_units, claims, policies}}}.
    """
    cl = _clean_dim(claims)
    pr = _clean_dim(premium)
    ex = _clean_dim(exposure)
    cl["_inc"] = pd.to_numeric(cl["incurred_amount"], errors="coerce")
    pr["_ern"] = pd.to_numeric(pr["earned_premium"], errors="coerce")
    ex["_exp"] = pd.to_numeric(ex["earned_exposure_units"], errors="coerce")
    ex["_pol"] = pd.to_numeric(ex["active_policies"], errors="coerce")

    dims = list(DIM_COLS)
    c_map = cl.groupby(dims).agg(
        incurred=("_inc", "sum"), claims=("_inc", "size")).to_dict("index")
    p_map = pr.groupby(dims).agg(earned=("_ern", "sum")).to_dict("index")
    e_map = ex.groupby(dims).agg(
        exp_units=("_exp", "sum"), policies=("_pol", "sum")).to_dict("index")

    finest: dict[tuple, dict] = {}
    for key in set(c_map) | set(p_map) | set(e_map):
        key = tuple(key)
        c, p, e = c_map.get(key, {}), p_map.get(key, {}), e_map.get(key, {})
        finest[key] = {
            "incurred": float(c.get("incurred", 0.0)),
            "claims": int(c.get("claims", 0)),
            "earned": float(p.get("earned", 0.0)),
            "exp_units": float(e.get("exp_units", 0.0)),
            "policies": float(e.get("policies", 0.0)),
        }

    out: dict[tuple, dict[tuple, dict]] = {}
    for level in LEVELS:
        if not level:
            tot = {"incurred": 0.0, "claims": 0, "earned": 0.0,
                   "exp_units": 0.0, "policies": 0.0}
            for a in finest.values():
                for k in tot:
                    tot[k] += a[k]
            out[level] = {(): tot}
            continue
        idx = [dims.index(d) for d in level]
        rolled: dict[tuple, dict] = {}
        for key, a in finest.items():
            cell = tuple(key[i] for i in idx)
            t = rolled.setdefault(cell, {"incurred": 0.0, "claims": 0,
                                         "earned": 0.0, "exp_units": 0.0,
                                         "policies": 0.0})
            for k in t:
                t[k] += a[k]
        out[level] = rolled
    return out


def _lr(a: dict) -> float | None:
    return None if a["earned"] == 0 else a["incurred"] / a["earned"]


def _freq(a: dict) -> float | None:
    return None if a["exp_units"] == 0 else a["claims"] / a["exp_units"]


def _sev(a: dict) -> float | None:
    return None if a["claims"] == 0 else a["incurred"] / a["claims"]


_VALUE_FN = {"loss_ratio": _lr, "claim_frequency": _freq, "claim_severity": _sev}


def _delta(key: str, val: float | None, prev: float | None) -> float | None:
    if val is None or prev is None:
        return None
    if key == "loss_ratio":
        return (val - prev) * 100.0  # percentage points
    if prev == 0:
        return None
    return (val - prev) / prev * 100.0  # relative % change


def _cell_flagged(level: tuple[str, ...], cell: tuple,
                  outlier_finest: set[tuple]) -> bool:
    if not outlier_finest:
        return False
    if level == FINEST:
        return cell in outlier_finest
    pos = [FINEST.index(d) for d in level]
    return any(all(o[i] == v for i, v in zip(pos, cell, strict=True))
               for o in outlier_finest)


def _dims_json(dims: dict) -> str:
    return json.dumps(dims, sort_keys=True)


def build_rows(
    cur: dict,
    *,
    period: str,
    dv_ids: list[str],
    prior: dict | None = None,
    prior_metrics: dict[tuple[str, str], float] | None = None,
    expected_lr: float | None = None,
    outlier_cells: set[tuple] | None = None,
    prior_ref: str | None = None,
) -> list[dict]:
    """Build metric-row dicts (matching the metrics table) for every
    level/cell. Pure — upsert lives in the analysis agent."""
    rows: list[dict] = []
    out_cells = outlier_cells or set()
    prior_metrics = prior_metrics or {}

    def _prev(key: str, dims: dict, level: tuple, cell: tuple) -> float | None:
        if prior is not None and cell in prior.get(level, {}):
            v = _VALUE_FN[key](prior[level][cell])
            if v is not None:
                return v
        return prior_metrics.get((key, _dims_json(dims)))

    def _flags(level: tuple, cell: tuple, a: dict) -> dict:
        return {
            "small_sample": (a["claims"] < SMALL_SAMPLE_CLAIMS
                             or a["policies"] < SMALL_SAMPLE_POLICIES),
            "claim_count": a["claims"],
            "policy_count": a["policies"],
            "outlier": _cell_flagged(level, cell, out_cells),
        }

    def _base_inputs(level: tuple) -> dict:
        return {"dataset_version_ids": list(dv_ids),
                "groupby": list(level) or ["portfolio"],
                "prior_ref": prior_ref}

    for level in LEVELS:
        for cell, a in cur[level].items():
            dims = dict(zip(level, cell, strict=True))
            flags = _flags(level, cell, a)
            for key in ("loss_ratio", "claim_frequency", "claim_severity"):
                val = _VALUE_FN[key](a)
                prev = _prev(key, dims, level, cell)
                rows.append({
                    "metric_key": key, "dimensions": dims, "period": period,
                    "value": val, "prev_value": prev,
                    "expected_value": (expected_lr if key == "loss_ratio"
                                       and not dims else None),
                    "delta_pp": _delta(key, val, prev),
                    "unit": UNITS[key],
                    "undefined_reason": (None if val is not None
                                         else UNDEFINED_REASONS[key]),
                    "flags": flags, "formula": FORMULAS[key],
                    "inputs": _base_inputs(level),
                    "module_version": MODULE_VERSION,
                })

    # AvE variance (portfolio LR actual vs expected)
    port_lr = _lr(cur[()][()])
    if expected_lr is not None and port_lr is not None:
        ave_val, ave_reason = (port_lr - expected_lr) * 100.0, None
    elif expected_lr is None:
        ave_val, ave_reason = None, f"no expected baseline for loss_ratio ({period})"
    else:
        ave_val, ave_reason = None, "actual loss_ratio undefined (premium base is zero)"
    rows.append({
        "metric_key": "ave_variance", "dimensions": {}, "period": period,
        "value": ave_val, "prev_value": None, "expected_value": expected_lr,
        "delta_pp": None, "unit": UNITS["ave_variance"],
        "undefined_reason": ave_reason,
        "flags": _flags((), (), cur[()][()]),
        "formula": FORMULAS["ave_variance"],
        "inputs": _base_inputs(()), "module_version": MODULE_VERSION,
    })

    # Deterioration contribution (OAT over finest cells)
    cur_lr = {c: _lr(a) for c, a in cur[FINEST].items()}
    prev_lr, weights, basis = {}, None, "none"
    if prior is not None:
        prev_lr = {c: _lr(prior[FINEST][c]) if c in prior.get(FINEST, {}) else None
                   for c in cur_lr}
        prior_earned_total = sum(a["earned"] for a in prior[FINEST].values())
        if prior_earned_total > 0:
            weights = {c: prior[FINEST][c]["earned"] / prior_earned_total
                       if c in prior.get(FINEST, {}) else None
                       for c in cur_lr}
            basis = "prior"
    port_prev = _prev("loss_ratio", {}, (), ())
    total = (port_lr - port_prev) if port_lr is not None and port_prev is not None else None
    pcts = oat_contributions(cur_lr, prev_lr, weights, total)
    if pcts:
        for cell, pct in pcts.items():
            dims = dict(zip(FINEST, cell, strict=True))
            rows.append({
                "metric_key": "deterioration_contribution", "dimensions": dims,
                "period": period, "value": pct, "prev_value": None,
                "expected_value": None, "delta_pp": None,
                "unit": UNITS["deterioration_contribution"],
                "undefined_reason": None,
                "flags": _flags(FINEST, cell, cur[FINEST][cell]),
                "formula": FORMULAS["deterioration_contribution"],
                "inputs": {**_base_inputs(FINEST), "weights_basis": basis},
                "module_version": MODULE_VERSION,
            })
    else:
        if total is None:
            reason = "no prior baseline for contribution decomposition"
        elif weights is None:
            reason = "no prior premium baseline for weights"
        else:
            reason = "no portfolio movement to decompose"
        rows.append({
            "metric_key": "deterioration_contribution", "dimensions": {},
            "period": period, "value": None, "prev_value": None,
            "expected_value": None, "delta_pp": None,
            "unit": UNITS["deterioration_contribution"],
            "undefined_reason": reason,
            "flags": _flags((), (), cur[()][()]),
            "formula": FORMULAS["deterioration_contribution"],
            "inputs": {**_base_inputs(()), "weights_basis": basis},
            "module_version": MODULE_VERSION,
        })
    return rows
