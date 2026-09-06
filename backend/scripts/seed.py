"""Seed demo state (Phase 5, idempotent — safe to re-run).

- Uploads Sep demo CSVs -> Storage `demo/`, Aug history CSVs -> `history/`
- Adds the "Monthly Review 2026-08" prior-report knowledge document
- Builds the seeded COMPLETED August workflow (MPR-2026-08-001) with REAL
  deterministic metrics computed from the history CSVs (pandas) + scripted
  insight/report content only, incl. the "monitor construction severity"
  finding + decision that September's Knowledge Agent will retrieve.

Order: migrate -> generate_sample_data -> seed. Run from backend/.
"""
from __future__ import annotations

import asyncio
import hashlib
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from sqlalchemy import select

from app.auth.actor import get_current_actor
from app.db import SessionLocal
from app.models import (
    AgentRun,
    DatasetVersion,
    Evidence,
    File,
    Finding,
    HumanDecision,
    KnowledgeDocument,
    Metric,
    Report,
    User,
    ValidationResult,
    Workflow,
)
from app.services.audit import record_event
from app.storage.supabase import get_storage
from app.utils.logging import json_log

AUG_REF = "MPR-2026-08-001"
AUG_PERIOD = "2026-08"
SAMPLE_DATA = Path(__file__).resolve().parent.parent.parent / "sample_data"

DEMO_FILES = [
    "claims_2026_09.csv",
    "claims_2026_09_v2.csv",
    "premium_2026_09.csv",
    "exposure_2026_09.csv",
]
HISTORY_FILES = [
    "claims_2026_08.csv",
    "premium_2026_08.csv",
    "exposure_2026_08.csv",
]

FINDING_TITLE = "Construction severity trending up — monitor into September"


def _now() -> datetime:
    return datetime.now(UTC)


def _upload_prefix(storage, prefix: str, names: list[str]) -> dict[str, dict]:
    """Upload sample files to a storage prefix. Returns {filename: file_meta}."""

    async def _run() -> None:
        for name in names:
            src = SAMPLE_DATA / name if prefix == "demo" else SAMPLE_DATA / "history" / name
            await storage.upload_bytes(f"{prefix}/{name}", src.read_bytes())

    asyncio.run(_run())
    meta = {}
    for name in names:
        p = SAMPLE_DATA / name if prefix == "demo" else SAMPLE_DATA / "history" / name
        data = p.read_bytes()
        with open(p, newline="") as f:
            rows = sum(1 for _ in f) - 1
        meta[name] = {
            "path": f"{prefix}/{name}",
            "size": len(data),
            "checksum": hashlib.sha256(data).hexdigest(),
            "rows": rows,
        }
    return meta


def _kind_of(name: str) -> str:
    return "claims" if "claims" in name else ("premium" if "premium" in name else "exposure")


def seed_knowledge(session, actor: User) -> None:
    exists = session.execute(
        select(KnowledgeDocument).where(
            KnowledgeDocument.title == "Monthly Review 2026-08",
            KnowledgeDocument.version == "1.0",
        )
    ).scalar_one_or_none()
    if exists:
        print("SKIP knowledge Monthly Review 2026-08 (already seeded)")
        return
    session.add(
        KnowledgeDocument(
            title="Monthly Review 2026-08",
            doc_type="prior_report",
            version="1.0",
            effective_date=datetime(2026, 8, 31).date(),
            content_text=(
                "Monthly Portfolio Review — August 2026 (Meridian General Insurance). "
                "Portfolio loss ratio 63.1% vs expected 62.8% (+0.3pp). Commercial "
                "Construction (South) severity rising faster than the +5.0% methodology "
                "trend. Decision: monitor construction severity next periods "
                "(recorded by Demo Actuary). No assumption change."
            ),
            tags=["prior_report", "construction", "monitoring"],
        )
    )
    session.commit()
    print("SEED knowledge Monthly Review 2026-08")


def _aug_metrics() -> tuple[list[dict], float]:
    """Real deterministic metrics from the history CSVs (preview of Phase 9 analytics)."""
    claims = pd.read_csv(SAMPLE_DATA / "history" / "claims_2026_08.csv")
    premium = pd.read_csv(SAMPLE_DATA / "history" / "premium_2026_08.csv")
    exposure = pd.read_csv(SAMPLE_DATA / "history" / "exposure_2026_08.csv")
    out: list[dict] = []

    def lr_row(key: str, dims: dict, inc: float, ern: float, expected: float | None) -> dict:
        value = inc / ern
        return {
            "metric_key": key,
            "dimensions": dims,
            "period": AUG_PERIOD,
            "value": value,
            "prev_value": None,
            "expected_value": expected,
            "delta_pp": (value - expected) * 100 if expected is not None else None,
            "unit": "ratio",
            "formula": "loss_ratio = Σincurred/Σearned_premium",
            "inputs": {"source": "history", "period": AUG_PERIOD},
        }

    port_lr = claims["incurred_amount"].sum() / premium["earned_premium"].sum()
    out.append(lr_row("loss_ratio", {}, float(claims["incurred_amount"].sum()),
                      float(premium["earned_premium"].sum()), 0.628))
    for (prod, seg), g in claims.groupby(["product", "segment"]):
        pe = premium[(premium["product"] == prod) & (premium["segment"] == seg)]
        out.append(lr_row("loss_ratio", {"product": prod, "segment": seg},
                          float(g["incurred_amount"].sum()),
                          float(pe["earned_premium"].sum()), None))
    cs = claims[(claims["segment"] == "Construction") & (claims["region"] == "South")]
    cs_units = float(exposure[(exposure["segment"] == "Construction")
                              & (exposure["region"] == "South")]["earned_exposure_units"].sum())
    cs_dims = {"product": "Commercial", "segment": "Construction", "region": "South"}
    out.append({
        "metric_key": "claim_severity", "dimensions": cs_dims,
        "period": AUG_PERIOD, "value": float(cs["incurred_amount"].sum()) / len(cs),
        "prev_value": None, "expected_value": None, "delta_pp": None, "unit": "inr",
        "formula": "claim_severity = Σincurred/claim_count",
        "inputs": {"source": "history", "period": AUG_PERIOD},
    })
    out.append({
        "metric_key": "claim_frequency", "dimensions": cs_dims,
        "period": AUG_PERIOD, "value": len(cs) / cs_units,
        "prev_value": None, "expected_value": None, "delta_pp": None, "unit": "per_unit",
        "formula": "claim_frequency = claim_count/earned_exposure_units",
        "inputs": {"source": "history", "period": AUG_PERIOD},
    })
    return out, port_lr


def seed_august_workflow(session, actor: User, history_meta: dict[str, dict]) -> Workflow:
    wf = session.execute(select(Workflow).where(Workflow.human_ref == AUG_REF)).scalar_one_or_none()
    if wf is not None:
        print(f"SKIP workflow {AUG_REF} (already seeded)")
        return wf

    wf = Workflow(
        human_ref=AUG_REF, portfolio="General Insurance", reporting_period=AUG_PERIOD,
        status="COMPLETED", stage="COMPLETED",
        config={"expected_files": ["claims", "premium", "exposure"], "seeded": True},
        created_by=actor.id, completed_at=_now(),
    )
    session.add(wf)
    session.flush()

    file_ids: dict[str, str] = {}
    for name, m in history_meta.items():
        f = File(workflow_id=wf.id, kind=_kind_of(name), filename=name,
                 storage_path=m["path"], size_bytes=m["size"], checksum=m["checksum"],
                 row_count=m["rows"], role="primary", period_inferred=AUG_PERIOD,
                 uploaded_by=actor.id)
        session.add(f)
        session.flush()
        file_ids[name] = f.id
        session.add(DatasetVersion(
            workflow_id=wf.id, kind=_kind_of(name), source_file_ids=[f.id],
            storage_path=m["path"], row_count=m["rows"], column_map={},
            transform_log={"seeded": True, "note": "August baseline, no transformations required"},
            checksum=m["checksum"],
        ))
    session.flush()

    for check_id, name, details in [
        ("struct_schema", "Schema present", {"missing_columns": []}),
        ("recon_totals", "Reconciliation within tolerance", {"diff_pct": 0.1}),
        ("behavioral_volume", "Volume stable vs prior", {"change_pct": 1.2}),
    ]:
        session.add(ValidationResult(
            workflow_id=wf.id, check_id=check_id, check_name=name, category="structural",
            severity="INFO", status="PASS", message=f"{name} — pass", details=details,
        ))

    metric_rows, port_lr = _aug_metrics()
    metric_ids: dict[str, str] = {}
    for m in metric_rows:
        row = Metric(workflow_id=wf.id, flags={}, module_version="m1", **m)
        session.add(row)
        session.flush()
        key = (m["metric_key"], str(sorted(m["dimensions"].items())))
        metric_ids[key] = row.id
    session.flush()

    finding = Finding(
        workflow_id=wf.id, agent="insight", agent_version="seed-v1", model="scripted",
        title=FINDING_TITLE,
        narrative=("Evidence: Construction (South) loss ratio 62.9%, severity rising faster "
                    "than the +5.0% methodology trend.\nHypothesis: early severity-driven "
                    "deterioration coincides with large-claim activity.\nConclusion: largest "
                    "watch item for September; no action yet."),
        severity="medium", confidence=0.65,
        possible_drivers=["large claims in Construction South"],
        alternatives=["reporting timing"],
        correlation_caveat="Single-period move; causation not established.",
        human_review_required=False,
        decision_question="Continue monitoring construction severity into September?",
        data_quality="August data passed all validation checks.",
        status="monitoring", links=[],
    )
    session.add(finding)
    session.flush()
    port_key = ("loss_ratio", "[]")
    if port_key in metric_ids:
        session.add(Evidence(
            workflow_id=wf.id, finding_id=finding.id, evidence_type="metric",
            ref_id=str(metric_ids[port_key]),
            snapshot={"metric_key": "loss_ratio", "period": AUG_PERIOD,
                      "value": round(port_lr, 4),
                      "formula": "loss_ratio = Σincurred/Σearned_premium"},
            description="August portfolio loss ratio 63.1%",
        ))

    session.add(HumanDecision(
        workflow_id=wf.id, finding_id=finding.id, decision="monitor",
        rationale=("Construction severity up vs trend; "
                   "watch September before any assumption review."),
        payload={"finding_title": FINDING_TITLE}, decided_by=actor.id,
    ))

    session.add(Report(
        workflow_id=wf.id, version=1, status="approved",
        sections={
            "executive_summary": ("August portfolio loss ratio 63.1% vs expected 62.8%. "
                                    "Construction (South) severity is the watch item; "
                                    "decision: monitor."),
            "key_metrics": [{"metric_key": "loss_ratio", "period": AUG_PERIOD,
                             "value": round(port_lr, 4)}],
            "findings": [{"title": FINDING_TITLE, "severity": "medium"}],
            "exceptions": [], "decisions": [{"decision": "monitor", "by": actor.name}],
            "open_questions": ["Is Construction South severity a trend or large-loss noise?"],
            "charts": [],
        },
        body_markdown="# Monthly Review 2026-08\n\nApproved.",
        approved_by=actor.id, approved_at=_now(),
    ))

    stages = [("intake", "INGESTING"), ("data_prep", "INGESTING"),
              ("validation", "VALIDATING"), ("analysis", "ANALYZING"),
              ("insight", "INVESTIGATING"), ("knowledge", "INVESTIGATING"),
              ("reporting", "REPORTING"), ("qa", "QA")]
    for agent, stage in stages:
        session.add(AgentRun(workflow_id=wf.id, agent=agent, stage=stage, attempt=1,
                             status="succeeded", duration_ms=1200))

    transitions = [(None, "INPUT_WAIT", "workflow_created", "human", actor.name),
                   ("INPUT_WAIT", "INGESTING", "run_started", "system", "orchestrator"),
                   ("INGESTING", "VALIDATING", "ingest_complete", "system", "orchestrator"),
                   ("VALIDATING", "VALIDATED", "validation_passed", "agent", "validation_agent"),
                   ("VALIDATED", "ANALYZING", "analysis_started", "system", "orchestrator"),
                   ("ANALYZING", "ANALYZED", "analysis_complete", "agent", "analysis_agent"),
                   ("ANALYZED", "INVESTIGATING", "investigation_started", "system", "orchestrator"),
                   ("INVESTIGATING", "INSIGHTS_READY", "insights_ready", "agent", "insight_agent"),
                   ("INSIGHTS_READY", "REPORTING", "report_started", "system", "orchestrator"),
                   ("REPORTING", "QA", "report_drafted", "agent", "reporting_agent"),
                   ("QA", "WAITING_FOR_HUMAN", "qa_passed", "agent", "qa_agent"),
                   ("WAITING_FOR_HUMAN", "APPROVED", "report_approved", "human", actor.name),
                   ("APPROVED", "COMPLETED", "workflow_completed", "system", "orchestrator")]
    for frm, to, action, atype, aname in transitions:
        record_event(session, workflow_id=wf.id, actor_type=atype, actor=aname,
                     action=action, from_status=frm, to_status=to, summary=f"{frm} -> {to}")
    session.commit()
    print(f"SEED workflow {AUG_REF} (COMPLETED, portfolio LR {port_lr * 100:.1f}%)")
    return wf


def main() -> None:
    storage = get_storage()
    print(f"storage backend: {type(storage).__name__}")
    demo_meta = _upload_prefix(storage, "demo", DEMO_FILES)
    history_meta = _upload_prefix(storage, "history", HISTORY_FILES)
    print(f"uploaded {len(demo_meta)} demo + {len(history_meta)} history files")
    with SessionLocal() as session:
        actor = get_current_actor(session)
        seed_knowledge(session, actor)
        seed_august_workflow(session, actor, history_meta)
    json_log("seed_complete", august_ref=AUG_REF)


if __name__ == "__main__":
    main()
