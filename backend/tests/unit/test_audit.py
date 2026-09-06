import uuid

from sqlalchemy import select

from app.models import AuditEvent, User, Workflow
from app.services.audit import record_event


def _workflow(db_session) -> Workflow:
    user = User(email=f"{uuid.uuid4().hex[:8]}@test.app", name="T", role="actuary")
    db_session.add(user)
    wf = Workflow(
        human_ref=f"MPR-2026-09-{uuid.uuid4().hex[:6].upper()}",
        reporting_period="2026-09",
        status="VALIDATING",
        created_by=user.id,
    )
    db_session.add(wf)
    db_session.commit()
    return wf


def test_record_event_persists(db_session):
    wf = _workflow(db_session)
    record_event(
        db_session,
        workflow_id=wf.id,
        actor_type="agent",
        actor="validation_agent",
        action="validation_blocker_raised",
        from_status="VALIDATING",
        to_status="BLOCKED",
        entity_type="validation_result",
        summary="premium recon -2.1%",
        details={"diff_pct": 2.1},
    )
    db_session.commit()
    rows = db_session.execute(select(AuditEvent)).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.actor == "validation_agent"
    assert row.to_status == "BLOCKED"
    assert row.details["diff_pct"] == 2.1


def test_record_event_defaults(db_session):
    wf = _workflow(db_session)
    evt = record_event(
        db_session, workflow_id=wf.id, actor_type="system", actor="orchestrator", action="ping"
    )
    db_session.commit()
    assert evt.from_status is None
    assert evt.details == {}
