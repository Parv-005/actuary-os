"""Knowledge agent tests (§6.7): retrieval + prior-period link, empty
retrieval, LLM-failure fallback, version-conflict surfacing."""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import select

from app.agents.base import run_agent
from app.agents.context import WorkflowContext
from app.agents.knowledge import run_knowledge
from app.llm.client import FakeLLMClient
from app.models import Evidence, Finding, KnowledgeDocument, User, Workflow
from app.services import knowledge as ksvc
from app.storage.supabase import MemoryStorage


def _wf(db_session, period="2026-09", status="INVESTIGATING",
        portfolio="General Insurance") -> Workflow:
    user = User(email=f"{uuid.uuid4().hex[:8]}@t.app", name="T", role="actuary")
    db_session.add(user)
    wf = Workflow(human_ref=f"MPR-{period}-{uuid.uuid4().hex[:6].upper()}",
                  reporting_period=period, portfolio=portfolio, status=status,
                  created_by=user.id)
    db_session.add(wf)
    db_session.commit()
    return wf


def _doc(db_session, title, doc_type, version, text, tags,
         effective=None, superseded_by=None):
    d = KnowledgeDocument(title=title, doc_type=doc_type, version=version,
                          effective_date=effective, content_text=text,
                          tags=tags, superseded_by=superseded_by)
    db_session.add(d)
    db_session.commit()
    return d


def _seed_docs(db_session):
    v30 = _doc(db_session, "Reserve Methodology", "methodology", "v3.0",
               "Expected severity trend for Commercial Construction: +4.0%.",
               ["methodology", "reserving", "construction"],
               effective=date(2025, 1, 15))
    v31 = _doc(db_session, "Reserve Methodology", "methodology", "v3.1",
               "Expected severity trend for Commercial Construction: +5.0%.",
               ["methodology", "reserving", "construction", "assumptions"],
               effective=date(2026, 4, 1))
    v30.superseded_by = v31.id
    _doc(db_session, "Monthly Review 2026-08", "prior_report", "1.0",
         "August 2026. Decision: monitor construction severity next periods.",
         ["prior_report", "construction", "monitoring"],
         effective=date(2026, 8, 31))
    db_session.commit()
    return v31


def _seed_aug_finding(db_session, prior_wf):
    f = Finding(workflow_id=prior_wf.id, agent="insight",
                title="Construction severity trending up — monitor into "
                      "September",
                narrative="Evidence: Construction (South) severity rising.\n"
                          "Conclusion: watch item for September.",
                severity="medium", confidence=0.65, status="monitoring")
    db_session.add(f)
    db_session.commit()
    return f


def _finding(db_session, wf, title="Commercial Construction (South) drives "
                                   "deterioration"):
    f = Finding(workflow_id=wf.id, agent="insight", title=title,
                narrative="Evidence: severity +13.1%.\nConclusion: driver.",
                severity="high", confidence=0.9,
                possible_drivers=["large claims in Construction South"],
                alternatives=["large-loss volatility"], status="draft")
    db_session.add(f)
    db_session.commit()
    return f


def _ctx(db_session, wf):
    return WorkflowContext(session=db_session, workflow_id=wf.id,
                           workflow=wf, storage=MemoryStorage())


SUMMARY_JSON = {
    "summary": "August set a monitor on construction severity; v3.1 holds.",
    "citations": [{"title": "Monthly Review 2026-08", "version": "1.0",
                   "effective_date": "2026-08-31"}],
    "no_relevant_document": False,
}


@pytest.mark.asyncio
async def test_prior_link_and_evidence(db_session):
    _seed_docs(db_session)
    prior = _wf(db_session, period="2026-08", status="COMPLETED")
    aug = _seed_aug_finding(db_session, prior)
    wf = _wf(db_session)
    f = _finding(db_session, wf)
    fake = FakeLLMClient(script=[{"json": SUMMARY_JSON}])
    result = await run_agent(
        _ctx(db_session, wf), "knowledge", "knowledge",
        lambda c: run_knowledge(c, llm_client=fake))
    assert result.status == "PASS"
    assert result.outputs["prior_links"] == 1
    db_session.refresh(f)
    assert str(aug.id) in (f.links or [])
    rows = db_session.execute(
        select(Evidence).where(Evidence.finding_id == f.id)).scalars().all()
    assert rows and all(r.evidence_type == "knowledge" for r in rows)
    assert any("repeat monitoring item" in r.description for r in rows)
    assert result.outputs["fallback"] is False


def test_retrieval_ranking_prefers_current(db_session):
    _seed_docs(db_session)
    docs = ksvc.search_knowledge_base(
        db_session, "construction severity monitoring",
        ["construction", "monitoring", "methodology", "assumptions"],
        as_of="2026-09-01")
    titles = {d["title"] for d in docs}
    assert {"Monthly Review 2026-08", "Reserve Methodology"} <= titles
    scores = [d["score"] for d in docs]
    assert scores == sorted(scores, reverse=True) and all(s > 0 for s in scores)
    monthly = next(d for d in docs if d["title"] == "Monthly Review 2026-08")
    assert monthly["score"] >= 4  # tag + keyword signal
    v31 = next(d for d in docs if d["version"] == "v3.1")
    assert v31["superseded"] is False
    v30 = next(d for d in docs if d["version"] == "v3.0")
    assert v30["superseded"] is True and v30["outdated"] is True


@pytest.mark.asyncio
async def test_empty_retrieval_no_doc(db_session):
    wf = _wf(db_session)
    _finding(db_session, wf)
    fake = FakeLLMClient(script=[{"json": SUMMARY_JSON}])  # never consumed
    result = await run_agent(
        _ctx(db_session, wf), "knowledge", "knowledge",
        lambda c: run_knowledge(c, llm_client=fake))
    assert result.status == "WARNING"
    assert result.outputs["no_relevant_document"] is True
    row = db_session.execute(select(Evidence)).scalar_one()
    assert row.snapshot == {"no_relevant_document": True}
    assert "No supporting internal documentation" in row.description


@pytest.mark.asyncio
async def test_llm_failure_falls_back(db_session):
    _seed_docs(db_session)
    wf = _wf(db_session)
    _finding(db_session, wf)
    fake = FakeLLMClient(script=[])  # every json_call raises LLMError
    result = await run_agent(
        _ctx(db_session, wf), "knowledge", "knowledge",
        lambda c: run_knowledge(c, llm_client=fake))
    assert result.status == "WARNING"
    assert result.outputs["fallback"] is True
    rows = db_session.execute(select(Evidence)).scalars().all()
    assert rows and "Monthly Review 2026-08" in rows[0].snapshot["summary"]


@pytest.mark.asyncio
async def test_version_conflict_surfaced(db_session):
    _doc(db_session, "Reserving Policy", "policy", "2024",
         "Old reserving policy text.", ["policy"],
         effective=date(2024, 1, 1))
    _doc(db_session, "Reserving Policy", "policy", "2025",
         "New reserving policy text, no supersession link.", ["policy"],
         effective=date(2025, 1, 1))
    wf = _wf(db_session)
    _finding(db_session, wf, title="Reserving policy review")
    fake = FakeLLMClient(script=[{"json": SUMMARY_JSON}])
    result = await run_agent(
        _ctx(db_session, wf), "knowledge", "knowledge",
        lambda c: run_knowledge(c, llm_client=fake))
    assert result.status == "WARNING"
    assert result.outputs["conflict"]["doc_type"] == "policy"
    rows = db_session.execute(select(Evidence)).scalars().all()
    assert any("could not be established automatically" in r.description
               for r in rows)


@pytest.mark.asyncio
async def test_no_findings_still_passes(db_session):
    _seed_docs(db_session)
    wf = _wf(db_session)
    fake = FakeLLMClient(script=[{"json": SUMMARY_JSON}])
    result = await run_agent(
        _ctx(db_session, wf), "knowledge", "knowledge",
        lambda c: run_knowledge(c, llm_client=fake))
    assert result.status == "PASS"
    assert result.outputs["docs"] >= 0
