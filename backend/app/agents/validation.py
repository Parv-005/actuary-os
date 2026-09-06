"""Validation Agent (§6.4, deterministic): "Can we trust this data?" — L1-L4.

Reads processed datasets + reference_values + prior-workflow metrics, upserts
one validation_results row per check (idempotent by check_id), raises a single
red CP-2 when any check BLOCKs. Returns BLOCKER and lets the engine own the
transition to BLOCKED (single-writer rule §7.2).
"""
from __future__ import annotations

import pandas as pd
from sqlalchemy import select

from app.agents.base import StageResult
from app.agents.context import WorkflowContext
from app.analytics import quality as q
from app.config import settings
from app.models import DatasetVersion, Metric, ReferenceValue, ValidationResult, Workflow
from app.services import checkpoints
from app.services.audit import record_event
from app.services.datasets import read_csv_bytes

KINDS = ("claims", "premium", "exposure")

LIKELY_CAUSES = [
    "actual portfolio deterioration (higher claims or lower premium)",
    "large-loss volatility in the period",
    "reporting or booking timing lag vs the system of record",
    "duplicate or scope change in the source extract",
    "wrong file selected at intake (check period coverage)",
]


def _num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _missing(series: pd.Series) -> pd.Series:
    return series.isna() | (series.astype(str).str.strip() == "")


def _inr(v: float) -> str:
    return f"₹{v:,.0f}"


def _prior_baseline(session, wf: Workflow) -> dict:
    """Prior portfolio LR, product labels, exposure rows from the most recent
    earlier workflow that has a portfolio loss_ratio metric (seeded Aug run)."""
    blank = {"workflow_ref": None, "period": None, "prior_lr": None,
             "prior_products": set(), "prior_exposure_rows": None}
    cands = session.execute(
        select(Workflow).where(
            Workflow.portfolio == wf.portfolio,
            Workflow.reporting_period < wf.reporting_period,
            Workflow.id != wf.id,
        ).order_by(Workflow.reporting_period.desc())
    ).scalars().all()
    for pw in cands:
        rows = session.execute(
            select(Metric).where(Metric.workflow_id == pw.id,
                                 Metric.metric_key == "loss_ratio")
        ).scalars().all()
        port = next((m for m in rows if not (m.dimensions or {})), None)
        if port is None or port.value is None:
            continue
        products = {(m.dimensions or {}).get("product") for m in rows}
        products.discard(None)
        exp_dv = session.execute(
            select(DatasetVersion).where(DatasetVersion.workflow_id == pw.id,
                                         DatasetVersion.kind == "exposure")
        ).scalar_one_or_none()
        return {"workflow_ref": pw.human_ref, "period": pw.reporting_period,
                "prior_lr": float(port.value), "prior_products": products,
                "prior_exposure_rows": exp_dv.row_count if exp_dv else None}
    return blank


def _upsert(session, workflow_id, check: dict) -> None:
    row = session.execute(
        select(ValidationResult).where(
            ValidationResult.workflow_id == workflow_id,
            ValidationResult.check_id == check["check_id"])
    ).scalar_one_or_none()
    if row is None:
        row = ValidationResult(workflow_id=workflow_id, check_id=check["check_id"])
        session.add(row)
    row.check_name = check["check_name"]
    row.category = check["category"]
    row.severity = check["severity"]
    row.status = check["status"]
    row.message = check["message"]
    row.details = check["details"]
    row.affected_row_count = check.get("affected_row_count", 0)
    row.resolved_by = None
    row.resolved_at = None
    row.resolution = None


def _l1_structural(frames: dict[str, pd.DataFrame], dvs: dict,
                   period: str) -> list[dict]:
    from app.services.datasets import CANONICAL_COLUMNS

    checks: list[dict] = []
    missing = {k: [c for c in CANONICAL_COLUMNS[k] if c not in frames[k].columns]
               for k in KINDS}
    any_missing = {k: v for k, v in missing.items() if v}
    checks.append({
        "check_id": "struct_schema", "check_name": "Schema present",
        "category": "structural",
        "severity": "BLOCKER" if any_missing else "INFO",
        "status": "BLOCKER" if any_missing else "PASS",
        "message": (f"Missing canonical columns: {any_missing}"
                    if any_missing else "All canonical columns present"),
        "details": {"missing": missing}, "affected_row_count": 0,
    })
    empty = [k for k in KINDS if len(frames[k]) == 0]
    checks.append({
        "check_id": "struct_row_counts", "check_name": "Row counts present",
        "category": "structural",
        "severity": "BLOCKER" if empty else "INFO",
        "status": "BLOCKER" if empty else "PASS",
        "message": (f"Datasets with no records: {empty}"
                    if empty else
                    "All datasets non-empty: " + ", ".join(
                        f"{k}={len(frames[k])}" for k in KINDS)),
        "details": {"row_counts": {k: len(frames[k]) for k in KINDS}},
        "affected_row_count": 0,
    })
    cov = q.coverage_pct(frames["claims"]["event_date"].tolist(), period)
    checks.append({
        "check_id": "cover_claims_events",
        "check_name": "Claim event dates inside reporting period",
        "category": "structural",
        "severity": "BLOCKER" if cov["blocked"] else "INFO",
        "status": "BLOCKER" if cov["blocked"] else "PASS",
        "message": (
            f"Only {cov['pct']:.1f}% of claim event dates fall inside {period} "
            f"({cov['in_period']}/{cov['total']}) — wrong file likely "
            f"(recovery: Reject Data → new workflow)"
            if cov["blocked"] else
            f"{cov['pct']:.1f}% of claim event dates inside {period} "
            f"({cov['in_period']}/{cov['total']})"),
        "details": {**cov, "period": period},
        "affected_row_count": cov["total"] - cov["in_period"],
    })
    for kind in ("premium", "exposure"):
        vals = frames[kind]["period"].tolist() if "period" in frames[kind] else []
        in_p = sum(1 for v in vals if str(v).strip() == period)
        total = len([v for v in vals if str(v).strip()])
        pct = (in_p / total * 100.0) if total else 0.0
        blocked = pct < q.COVERAGE_BLOCK_PCT
        checks.append({
            "check_id": f"cover_{kind}_period",
            "check_name": f"{kind.title()} period column matches reporting period",
            "category": "structural",
            "severity": "BLOCKER" if blocked else "INFO",
            "status": "BLOCKER" if blocked else "PASS",
            "message": (
                f"Only {pct:.1f}% of {kind} rows carry period {period} "
                f"— wrong file likely" if blocked else
                f"{pct:.1f}% of {kind} rows carry period {period} ({in_p}/{total})"),
            "details": {"total": total, "in_period": in_p, "pct": pct,
                        "period": period},
            "affected_row_count": total - in_p,
        })
    removed = {k: (dvs[k].transform_log or {}).get(
        "exact_duplicates_removed", {}).get("count", 0) for k in KINDS}
    checks.append({
        "check_id": "struct_dedup_rule", "check_name": "Dedup rule applied (INFO)",
        "category": "structural", "severity": "INFO", "status": "PASS",
        "message": (f"{sum(removed.values())} exact duplicates auto-removed per "
                    f"pre-approved validated rule: "
                    + ", ".join(f"{k}={v}" for k, v in removed.items())),
        "details": {"removed_by_kind": removed}, "affected_row_count": 0,
    })
    return checks


def _l2_record(frames: dict[str, pd.DataFrame], dvs: dict) -> list[dict]:
    cl, pr = frames["claims"], frames["premium"]
    checks: list[dict] = []
    n_miss_inc = int(_missing(cl["incurred_amount"]).sum()) if "incurred_amount" in cl else len(cl)
    checks.append({
        "check_id": "record_missing_incurred",
        "check_name": "Incurred amount present on every claim",
        "category": "record",
        "severity": "BLOCKER" if n_miss_inc else "INFO",
        "status": "BLOCKER" if n_miss_inc else "PASS",
        "message": (
            f"{n_miss_inc} claims missing incurred_amount — blocks loss_ratio, "
            f"claim_severity" if n_miss_inc else
            "Every claim carries an incurred_amount"),
        "details": {"missing": n_miss_inc}, "affected_row_count": n_miss_inc,
    })
    n_blank_region = int((cl["region"].astype(str).str.strip() == "").sum())
    filled = (dvs["claims"].transform_log or {}).get("region_fix", {}).get("filled", 0)
    checks.append({
        "check_id": "record_missing_region", "check_name": "Region present (auto-fix)",
        "category": "record",
        "severity": "WARNING" if n_blank_region else "INFO",
        "status": "WARNING" if n_blank_region else "PASS",
        "message": (
            f"{n_blank_region} claims still missing region after policy-join "
            f"auto-fix" if n_blank_region else
            f"Region complete ({filled} auto-fixed via policy join)"),
        "details": {"still_blank": n_blank_region, "auto_fixed": filled},
        "affected_row_count": n_blank_region,
    })
    bad_dates = cl[_missing(cl["event_date"]) | _missing(cl["report_date"])]
    samples = bad_dates["claim_id"].head(5).tolist()
    checks.append({
        "check_id": "record_invalid_dates", "check_name": "Dates parseable",
        "category": "record",
        "severity": "WARNING" if len(bad_dates) else "INFO",
        "status": "WARNING" if len(bad_dates) else "PASS",
        "message": (
            f"{len(bad_dates)} claims with unparseable event/report dates "
            f"(sample: {samples})" if len(bad_dates) else
            "All claim dates parseable"),
        "details": {"count": len(bad_dates), "sample_claim_ids": samples},
        "affected_row_count": len(bad_dates),
    })
    neg = pr[(_num(pr["written_premium"]) < 0) | (_num(pr["earned_premium"]) < 0)]
    samples = neg["policy_id"].head(5).tolist() if "policy_id" in pr else []
    checks.append({
        "check_id": "record_negative_premium", "check_name": "Premiums non-negative",
        "category": "record",
        "severity": "WARNING" if len(neg) else "INFO",
        "status": "WARNING" if len(neg) else "PASS",
        "message": (
            f"{len(neg)} premium rows with negative written/earned premium "
            f"(sample: {samples})" if len(neg) else
            "No negative premiums"),
        "details": {"count": len(neg), "sample_policy_ids": samples},
        "affected_row_count": len(neg),
    })
    coll_total = sum((dvs[k].transform_log or {}).get("key_collisions", {}).get(
        "count", 0) for k in KINDS)
    coll_samples = (dvs["claims"].transform_log or {}).get(
        "key_collisions", {}).get("samples", [])
    checks.append({
        "check_id": "record_key_collisions",
        "check_name": "Key-collision duplicates confirmed",
        "category": "record",
        "severity": "BLOCKER" if coll_total else "INFO",
        "status": "BLOCKER" if coll_total else "PASS",
        "message": (
            f"Key-collision duplicates: {coll_total} rows share a claim_id with "
            f"differing values — never auto-merged, manual review required "
            f"(sample: {coll_samples[:2]})" if coll_total else
            "No key-collision duplicates"),
        "details": {"count": coll_total, "samples": coll_samples[:3]},
        "affected_row_count": coll_total,
    })
    ev, rp = cl["event_date"].astype(str).str.strip(), cl["report_date"].astype(str).str.strip()
    impossible = cl[(ev != "") & (rp != "") & (ev > rp)]
    samples = impossible["claim_id"].head(5).tolist()
    checks.append({
        "check_id": "record_impossible_dates",
        "check_name": "Event date not after report date",
        "category": "record",
        "severity": "WARNING" if len(impossible) else "INFO",
        "status": "WARNING" if len(impossible) else "PASS",
        "message": (
            f"{len(impossible)} claims with event_date after report_date "
            f"(sample: {samples})" if len(impossible) else
            "No impossible event/report date pairs"),
        "details": {"count": len(impossible), "sample_claim_ids": samples},
        "affected_row_count": len(impossible),
    })
    remaining_dupes = sum(int(frames[k].duplicated(keep=False).sum()) for k in KINDS)
    checks.append({
        "check_id": "record_dedup_verified",
        "check_name": "Post-dedup duplicate verification",
        "category": "record",
        "severity": "WARNING" if remaining_dupes else "INFO",
        "status": "WARNING" if remaining_dupes else "PASS",
        "message": (
            f"{remaining_dupes} exact-duplicate rows remain in processed datasets"
            if remaining_dupes else
            "Post-dedup verification: no exact duplicates remain"),
        "details": {"remaining": remaining_dupes},
        "affected_row_count": remaining_dupes,
    })
    return checks


def _l3_reconciliation(frames: dict[str, pd.DataFrame], refs: dict[str, float],
                       period: str) -> list[dict]:
    earned = float(_num(frames["premium"]["earned_premium"]).sum())
    incurred = float(_num(frames["claims"]["incurred_amount"]).sum())
    specs = [("recon_premium", "Premium reconciliation (earned vs system of record)",
              earned, "recon_premium_total"),
             ("recon_claims", "Claims reconciliation (incurred vs system of record)",
              incurred, "recon_claims_total")]
    checks = []
    for check_id, name, transformed, ref_key in specs:
        source = refs.get(ref_key)
        if source is None:
            checks.append({
                "check_id": check_id, "check_name": name,
                "category": "reconciliation", "severity": "WARNING",
                "status": "WARNING",
                "message": (f"No system-of-record reference for {ref_key} "
                            f"({period}) — cannot verify"),
                "details": {"transformed": transformed, "source": None,
                            "reference_key": ref_key},
                "affected_row_count": 0,
            })
            continue
        diff = q.recon_diff_pct(transformed, source) or 0.0
        sev, st = q.classify_recon(diff, settings.recon_pass_pct,
                                   settings.recon_warn_pct)
        label = "Premium earned" if check_id == "recon_premium" else "Claims incurred"
        checks.append({
            "check_id": check_id, "check_name": name,
            "category": "reconciliation", "severity": sev, "status": st,
            "message": (
                f"{label} total {_inr(transformed)} vs system-of-record "
                f"{_inr(source)} ({diff:+.2f}%)"
                + (f" — exceeds ±{settings.recon_warn_pct:.1f}% tolerance"
                   if st == "BLOCKER" else
                   (f" — outside ±{settings.recon_pass_pct:.1f}% (watch)"
                    if st == "WARNING" else " — within tolerance"))),
            "details": {"source": source, "transformed": transformed,
                        "diff_pct": diff,
                        "thresholds": {"pass_pct": settings.recon_pass_pct,
                                       "warn_pct": settings.recon_warn_pct}},
            "affected_row_count": 0,
        })
    return checks


def _l4_behavioral(frames: dict[str, pd.DataFrame], dvs: dict,
                   prior: dict) -> list[dict]:
    cl, ex = frames["claims"], frames["exposure"]
    earned = float(_num(frames["premium"]["earned_premium"]).sum())
    incurred = float(_num(cl["incurred_amount"]).sum())
    checks: list[dict] = []

    if earned > 0 and prior["prior_lr"] is not None:
        cur_lr = incurred / earned
        delta = q.lr_shift_pp(cur_lr, prior["prior_lr"])
        warn = abs(delta) >= q.LR_SHIFT_WARN_PP
        checks.append({
            "check_id": "behav_lr_shift",
            "check_name": "Portfolio loss-ratio shift vs prior",
            "category": "behavioral",
            "severity": "WARNING" if warn else "INFO",
            "status": "WARNING" if warn else "PASS",
            "message": (
                f"Portfolio loss ratio {cur_lr:.1%} vs prior "
                f"{prior['prior_lr']:.1%} ({delta:+.1f}pp vs "
                f"{prior['workflow_ref']})"
                + (" — exceeds ±3.0pp review threshold" if warn else "")),
            "details": {"current_lr": cur_lr, "prior_lr": prior["prior_lr"],
                        "delta_pp": delta, "prior_ref": prior["workflow_ref"]},
            "affected_row_count": 0,
        })
    else:
        checks.append({
            "check_id": "behav_lr_shift",
            "check_name": "Portfolio loss-ratio shift vs prior",
            "category": "behavioral", "severity": "INFO", "status": "PASS",
            "message": ("Cannot compute shift (zero earned premium)"
                        if earned <= 0 else
                        "No prior-period loss ratio — shift check skipped"),
            "details": {"prior_ref": prior["workflow_ref"]},
            "affected_row_count": 0,
        })

    cur_vol = len(ex)
    if prior["prior_exposure_rows"]:
        chg = q.volume_change_pct(cur_vol, prior["prior_exposure_rows"]) or 0.0
        warn = abs(chg) >= q.VOLUME_SHIFT_WARN_PCT
        checks.append({
            "check_id": "behav_volume_shift",
            "check_name": "Policy volume shift vs prior",
            "category": "behavioral",
            "severity": "WARNING" if warn else "INFO",
            "status": "WARNING" if warn else "PASS",
            "message": (
                f"Active policies {cur_vol} vs prior {prior['prior_exposure_rows']} "
                f"({chg:+.1f}%)"
                + (" — exceeds ±30% threshold" if warn else "")),
            "details": {"current": cur_vol,
                        "prior": prior["prior_exposure_rows"],
                        "change_pct": chg, "prior_ref": prior["workflow_ref"]},
            "affected_row_count": 0,
        })
    else:
        checks.append({
            "check_id": "behav_volume_shift",
            "check_name": "Policy volume shift vs prior",
            "category": "behavioral", "severity": "INFO", "status": "PASS",
            "message": "No prior-period exposure baseline — volume check skipped",
            "details": {"current": cur_vol}, "affected_row_count": 0,
        })

    inc = _num(cl["incurred_amount"]).fillna(0.0)
    region_sums = inc.groupby(cl["region"].astype(str).str.strip()).sum()
    total = float(region_sums.sum())
    shares = {r: float(v) / total for r, v in region_sums.items() if total} or {}
    conc = q.concentration(shares)
    checks.append({
        "check_id": "behav_concentration",
        "check_name": "Regional concentration of incurred",
        "category": "behavioral",
        "severity": "WARNING" if conc["warn"] else "INFO",
        "status": "WARNING" if conc["warn"] else "PASS",
        "message": (f"Top region {conc['top']} holds {conc['share']:.1%} of "
                    f"incurred" + (" — concentrated book" if conc["warn"] else "")),
        "details": {"shares": {k: round(v, 4) for k, v in shares.items()},
                    "top": conc["top"], "share": conc["share"]},
        "affected_row_count": 0,
    })

    current_products = {str(p).strip() for p in cl["product"].unique()
                        if str(p).strip()}
    if prior["prior_products"]:
        ren = q.rename_candidates(prior["prior_products"], current_products)
        bad = bool(ren["vanished"])
        checks.append({
            "check_id": "behav_rename_detection",
            "check_name": "Product labels consistent vs prior",
            "category": "behavioral",
            "severity": "WARNING" if bad else "INFO",
            "status": "WARNING" if bad else "PASS",
            "message": (
                f"Possible rename: {ren['vanished']} vanished vs "
                f"{prior['period']}; reference mapping required "
                f"(appeared: {ren['appeared']})" if bad else
                "Product labels consistent vs prior period"),
            "details": {**ren, "prior_ref": prior["workflow_ref"]},
            "affected_row_count": 0,
        })
    else:
        checks.append({
            "check_id": "behav_rename_detection",
            "check_name": "Product labels consistent vs prior",
            "category": "behavioral", "severity": "INFO", "status": "PASS",
            "message": "No prior-period product baseline — rename check skipped",
            "details": {"current": sorted(current_products)},
            "affected_row_count": 0,
        })

    amts = _num(cl["incurred_amount"])
    valid = amts.dropna()
    id_list = cl.loc[valid.index, "claim_id"].astype(str).tolist()
    out = q.outlier_flags([float(v) for v in valid.tolist()], id_list)
    if out["threshold"] is None:
        checks.append({
            "check_id": "behav_outlier_claims", "check_name": "Large-claim outliers",
            "category": "behavioral", "severity": "INFO", "status": "PASS",
            "message": f"Outlier scan skipped: {out['note']}",
            "details": out, "affected_row_count": 0,
        })
    elif out["flagged_total"]:
        first = out["flagged"][0]
        extra = (f" (+{out['flagged_total'] - 1} more)"
                 if out["flagged_total"] > 1 else "")
        checks.append({
            "check_id": "behav_outlier_claims", "check_name": "Large-claim outliers",
            "category": "behavioral", "severity": "WARNING", "status": "WARNING",
            "message": (f"Claim {first['claim_id']} "
                        f"{_inr(first['incurred_amount'])} — unusual but not "
                        f"proven invalid{extra}"),
            "details": out, "affected_row_count": out["flagged_total"],
        })
    else:
        checks.append({
            "check_id": "behav_outlier_claims", "check_name": "Large-claim outliers",
            "category": "behavioral", "severity": "INFO", "status": "PASS",
            "message": (f"No claims above max(p99, mean+3σ) "
                        f"({_inr(out['threshold'])})"),
            "details": out, "affected_row_count": 0,
        })

    claim_counts = cl.groupby(["product", "segment"]).size().to_dict()
    policy_counts = ex.groupby(["product", "segment"]).size().to_dict()
    small = q.small_sample_cells(
        {tuple(k): int(v) for k, v in claim_counts.items()},
        {tuple(k): int(v) for k, v in policy_counts.items()})
    if small:
        small_msg = ("Small-sample cells (claims<20 or policies<10): " + "; ".join(
            f"{'/'.join(c['cell'])} ({c['policies']} policies, "
            f"{c['claims']} claims)" for c in small))
    else:
        small_msg = "No small-sample segments"
    checks.append({
        "check_id": "behav_small_sample", "check_name": "Small-sample segments",
        "category": "behavioral",
        "severity": "WARNING" if small else "INFO",
        "status": "WARNING" if small else "PASS",
        "message": small_msg,
        "details": {"cells": small}, "affected_row_count": 0,
    })
    return checks


async def run_validation(ctx: WorkflowContext) -> StageResult:
    session, wf = ctx.session, ctx.workflow
    period = wf.reporting_period
    dvs = {dv.kind: dv for dv in session.execute(
        select(DatasetVersion).where(DatasetVersion.workflow_id == wf.id)
    ).scalars().all()}
    missing = [k for k in KINDS if k not in dvs]
    if missing:
        return StageResult(
            status="FAILED",
            error=f"validation: no processed dataset for: {', '.join(missing)}")

    frames = {k: read_csv_bytes(await ctx.storage.download_bytes(dvs[k].storage_path))
              for k in KINDS}
    refs = {r.metric_key: float(r.value) for r in session.execute(
        select(ReferenceValue).where(ReferenceValue.period == period)
    ).scalars().all()}
    prior = _prior_baseline(session, wf)

    checks = (_l1_structural(frames, dvs, period)
              + _l2_record(frames, dvs)
              + _l3_reconciliation(frames, refs, period)
              + _l4_behavioral(frames, dvs, prior))
    for c in checks:
        _upsert(session, wf.id, c)
    session.flush()

    blockers = [c for c in checks if c["status"] == "BLOCKER"]
    warnings = [c["check_id"] for c in checks if c["status"] == "WARNING"]
    by_status = {"PASS": sum(1 for c in checks if c["status"] == "PASS"),
                 "WARNING": len(warnings), "BLOCKER": len(blockers)}

    if blockers:
        cp = checkpoints.raise_checkpoint(
            session, wf, checkpoint_type="validation_blocker", severity="red",
            blocking=True,
            title=("Validation blocked: "
                   + "; ".join(b["check_name"] for b in blockers)),
            context={
                "stage": "validation", "period": period,
                "blockers": [{"check_id": b["check_id"], "message": b["message"],
                              "details": b["details"]} for b in blockers],
                "likely_causes": LIKELY_CAUSES,
            },
            options=[
                {"decision": "accept_exception", "label": "Accept exception"},
                {"decision": "request_rerun", "label": "Request re-run"},
                {"decision": "reject_data", "label": "Reject data"},
            ],
        )
        record_event(
            session, workflow_id=wf.id, actor_type="agent",
            actor="validation_agent", action="validation_blocked",
            entity_type="workflow", entity_id=wf.id,
            summary=f"{len(blockers)} blocking checks: "
                    f"{', '.join(b['check_id'] for b in blockers)}",
            details={"by_status": by_status, "warnings": warnings},
        )
        return StageResult(status="BLOCKER", checkpoint_id=str(cp.id),
                           outputs={"checks": by_status,
                                    "blockers": [b["check_id"] for b in blockers],
                                    "warnings": warnings})

    record_event(
        session, workflow_id=wf.id, actor_type="agent",
        actor="validation_agent", action="validation_complete",
        entity_type="workflow", entity_id=wf.id,
        summary=(f"validation {by_status['PASS']} pass, "
                 f"{by_status['WARNING']} warnings, no blockers"),
        details={"by_status": by_status, "warnings": warnings},
    )
    return StageResult(status="WARNING" if warnings else "PASS",
                       outputs={"checks": by_status, "warnings": warnings})
