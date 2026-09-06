"""State machine tests (§7): legal/illegal transitions."""
import uuid

import pytest

from app.models import User, Workflow
from app.orchestrator.states import (
    ALLOWED_TRANSITIONS,
    BLOCKED,
    COMPLETED,
    REJECTED,
    IllegalTransition,
    apply_transition,
    can_transition,
)


def _wf(db_session, status="INGESTING") -> Workflow:
    user = User(email=f"{uuid.uuid4().hex[:8]}@t.app", name="T", role="actuary")
    db_session.add(user)
    wf = Workflow(human_ref=f"MPR-2026-09-{uuid.uuid4().hex[:6].upper()}",
                  reporting_period="2026-09", status=status, created_by=user.id)
    db_session.add(wf)
    db_session.commit()
    return wf


def test_all_states_have_transition_maps():
    for state in ALLOWED_TRANSITIONS:
        assert isinstance(ALLOWED_TRANSITIONS[state], set)


def test_terminal_states_have_no_exits():
    for state in (COMPLETED, REJECTED, "CANCELLED"):
        assert ALLOWED_TRANSITIONS[state] == set()


def test_legal_transition_writes_audit(db_session):
    wf = _wf(db_session)
    apply_transition(db_session, wf, "VALIDATING", actor="test", reason="r")
    db_session.commit()
    assert wf.status == "VALIDATING"
    from app.models import AuditEvent
    evt = db_session.query(AuditEvent).order_by(AuditEvent.created_at.desc()).first()
    assert evt.from_status == "INGESTING" and evt.to_status == "VALIDATING"


def test_illegal_transition_raises(db_session):
    wf = _wf(db_session, status="COMPLETED")
    with pytest.raises(IllegalTransition):
        apply_transition(db_session, wf, "VALIDATING")
    with pytest.raises(IllegalTransition):
        apply_transition(db_session, wf, "CANCELLED")  # terminal immutable


def test_blocked_paths():
    assert can_transition("INGESTING", BLOCKED)
    assert can_transition("VALIDATING", BLOCKED)
    assert not can_transition("ANALYZING", BLOCKED)
    assert can_transition(BLOCKED, "INGESTING")
    assert can_transition(BLOCKED, REJECTED)
