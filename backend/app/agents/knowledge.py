"""Knowledge / Research Agent (§6.7: deterministic retrieval + 1 LLM call).

Retrieves approved context (prior reports, methodology, definitions),
links prior-period monitoring findings ("repeat monitoring item"), and
frames one summary with a single LLM call — falling back to deterministic
excerpt concatenation when the LLM fails. Never invents policy.
"""
from __future__ import annotations

import json

from pydantic import ValidationError
from sqlalchemy import select

from app.agents.base import StageResult
from app.agents.context import WorkflowContext
from app.agents.usage import record_llm_usage
from app.llm.client import LLMError, LLMUsage, get_llm_client, load_prompt, prompt_hash
from app.llm.schemas import KnowledgeSummary
from app.models import Finding
from app.services import knowledge as ksvc
from app.services.audit import record_event
from app.services.knowledge import tokens as _tokens

AGENT_VERSION = "v1"
MAX_LLM_ATTEMPTS = 2  # then deterministic fallback (§6.7)


def _finding_tags(findings: list[Finding]) -> list[str]:
    tags: set[str] = set()
    for f in findings:
        for t in _tokens(f"{f.title} {' '.join(f.possible_drivers or [])}"):
            tags.add(t)
    tags.update({"monitoring", "prior_report", "methodology", "assumptions",
                 "definitions"})
    return sorted(tags)


def _fallback_summary(docs: list[dict]) -> KnowledgeSummary:
    parts = [f"{d['title']} ({d['version']}): {d['excerpt']}" for d in docs]
    return KnowledgeSummary(
        summary="\n\n".join(parts),
        citations=[{"title": d["title"], "version": d["version"],
                    "effective_date": d["effective_date"]} for d in docs],
        no_relevant_document=False,
    )


async def run_knowledge(ctx: WorkflowContext, llm_client=None) -> StageResult:
    session, wf = ctx.session, ctx.workflow
    findings = session.execute(
        select(Finding).where(Finding.workflow_id == wf.id)
        .order_by(Finding.created_at)
    ).scalars().all()

    query = " ".join(f.title for f in findings) or wf.human_ref
    docs = ksvc.search_knowledge_base(
        session, query, _finding_tags(findings),
        as_of=f"{wf.reporting_period}-01")
    conflict = ksvc.detect_conflict(docs)

    # link prior-period monitoring findings on every current finding
    prior_links = 0
    for f in findings:
        links = ksvc.link_prior_findings(session, wf, f)
        if links:
            have = set(f.links or [])
            for link in links:
                if link["finding_id"] not in have:
                    f.links = [*(f.links or []), link["finding_id"]]
                    have.add(link["finding_id"])
                    prior_links += 1
    session.flush()

    usage = LLMUsage()
    used_fallback = False
    if not docs:
        summary = KnowledgeSummary(summary="", citations=[],
                                   no_relevant_document=True)
    else:
        system = load_prompt("knowledge_system")
        user = json.dumps({
            "workflow": wf.human_ref, "period": wf.reporting_period,
            "findings": [{"title": f.title,
                          "drivers": f.possible_drivers or []}
                         for f in findings],
            "documents": docs,
        })
        client = llm_client or get_llm_client()
        summary = None
        errors: list[str] = []
        for _ in range(MAX_LLM_ATTEMPTS):
            try:
                result = await client.json_call(
                    system, user, KnowledgeSummary)
                summary = result.output
                usage.calls += result.usage.calls
                usage.tokens_in += result.usage.tokens_in
                usage.tokens_out += result.usage.tokens_out
                record_llm_usage(session, wf.id, "knowledge", "knowledge",
                                 usage, prompt_hash(system, user))
                break
            except (LLMError, ValidationError) as e:
                errors.append(str(e)[:200])
        if summary is None:
            summary = _fallback_summary(docs)
            used_fallback = True

    # evidence: one knowledge row per finding (excerpt + citations), or an
    # explicit no-doc row when retrieval was empty
    n_evidence = 0
    if not docs:
        session.add(_evidence_row(
            wf.id, findings[0].id if findings else None, "",
            {"no_relevant_document": True},
            "No supporting internal documentation found"))
        n_evidence = 1
    else:
        conflict_note = (f" Documentation conflict ({conflict['doc_type']} "
                         f"{' vs '.join(conflict['versions'])}): latest could "
                         "not be established automatically — confirm."
                         if conflict else "")
        outdated = [d for d in docs if d["outdated"]]
        outdated_note = (f" Possibly superseded: "
                         f"{', '.join(d['title'] + ' ' + d['version'] for d in outdated)}."
                         if outdated else "")
        repeat_note = (f" repeat monitoring item: linked {prior_links} "
                       f"prior finding(s)." if prior_links else "")
        for f in findings or [None]:
            for d in docs:
                session.add(_evidence_row(
                    wf.id, f.id if f else None, d["id"],
                    {"title": d["title"], "version": d["version"],
                     "effective_date": d["effective_date"],
                     "excerpt": d["excerpt"], "score": d["score"],
                     "summary": summary.summary[:2000],
                     "citations": [c.model_dump() for c in summary.citations]},
                    f"{d['title']} ({d['version']}): "
                    f"{d['excerpt'][:200]}" + conflict_note + outdated_note
                    + repeat_note))
                n_evidence += 1
    session.flush()

    record_event(
        session, workflow_id=wf.id, actor_type="agent",
        actor="knowledge_agent", action="knowledge_complete",
        entity_type="workflow", entity_id=wf.id,
        summary=(f"knowledge: {len(docs)} docs, {n_evidence} evidence rows"
                 + (" (deterministic fallback)" if used_fallback else "")
                 + (" (doc conflict surfaced)" if conflict else "")),
        details={"docs": [d["id"] for d in docs], "evidence": n_evidence,
                 "fallback": used_fallback, "conflict": conflict,
                 "prior_links": prior_links,
                 "no_relevant_document": summary.no_relevant_document},
    )
    status = "PASS"
    if used_fallback or conflict or summary.no_relevant_document:
        status = "WARNING"
    return StageResult(status=status,
                       outputs={"docs": len(docs), "evidence": n_evidence,
                                "fallback": used_fallback, "conflict": conflict,
                                "prior_links": prior_links,
                                "no_relevant_document":
                                    summary.no_relevant_document})


def _evidence_row(workflow_id, finding_id, ref_id, snapshot, description):
    from app.models import Evidence

    return Evidence(workflow_id=workflow_id, finding_id=finding_id,
                    evidence_type="knowledge", ref_id=ref_id,
                    snapshot=snapshot, description=description)
