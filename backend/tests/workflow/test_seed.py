"""Phase 5 tests: seed idempotency + reset_demo cancellation (test schema)."""
import sys
from pathlib import Path

from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))

import reset_demo  # noqa: E402
import seed  # noqa: E402

from app.models import Finding, HumanDecision, KnowledgeDocument, Metric, Workflow  # noqa: E402


def _counts(db_session):
    wf = db_session.execute(
        select(func.count()).select_from(Workflow).where(Workflow.human_ref == seed.AUG_REF)
    ).scalar()
    return {
        "aug": wf,
        "metrics": db_session.execute(select(func.count()).select_from(Metric)).scalar(),
        "findings": db_session.execute(select(func.count()).select_from(Finding)).scalar(),
        "decisions": db_session.execute(select(func.count()).select_from(HumanDecision)).scalar(),
        "prior_reports": db_session.execute(
            select(func.count()).select_from(KnowledgeDocument).where(
                KnowledgeDocument.title == "Monthly Review 2026-08"
            )
        ).scalar(),
    }


def test_seed_idempotent(db_session):
    seed.main()
    before = _counts(db_session)
    assert before["aug"] == 1
    assert before["metrics"] == 10
    assert before["findings"] == 1
    assert before["decisions"] == 1
    assert before["prior_reports"] == 1
    seed.main()
    assert _counts(db_session) == before


def test_seed_metrics_match_storyline(db_session):
    seed.main()
    port = db_session.execute(
        select(Metric).where(Metric.metric_key == "loss_ratio", Metric.dimensions == {})
    ).scalar_one()
    assert abs(float(port.value) - 0.631) < 1e-6
    assert abs(float(port.expected_value) - 0.628) < 1e-9
    sev = db_session.execute(
        select(Metric).where(Metric.metric_key == "claim_severity")
    ).scalar_one()
    assert abs(float(sev.value) - 258519.0) < 1.0


def test_reset_cancels_non_seed(db_session):
    seed.main()
    extra = Workflow(human_ref="MPR-2026-09-XYZ", reporting_period="2026-09", status="INPUT_WAIT")
    db_session.add(extra)
    db_session.commit()
    reset_demo.main(verify_health=False)
    db_session.refresh(extra)
    aug = db_session.execute(
        select(Workflow).where(Workflow.human_ref == seed.AUG_REF)
    ).scalar_one()
    assert extra.status == "CANCELLED"
    assert aug.status == "COMPLETED"
