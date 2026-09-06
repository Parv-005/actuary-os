"""Knowledge retrieval (§6.7): deterministic tag + keyword search over
approved documents. Pure ranking over rows the caller loaded — no LLM here;
the agent makes exactly one LLM call to frame the summary (with a
deterministic fallback).
"""
from __future__ import annotations

import re
from datetime import date

STOPWORDS = frozenset({
    "the", "and", "for", "with", "into", "from", "this", "that", "are",
    "was", "were", "has", "have", "had", "will", "would", "should", "could",
    "per", "its", "our", "your", "but", "not", "all", "any", "over",
    "under", "more", "than", "then", "also", "such", "may", "one", "two",
    "new", "current", "next", "what", "why", "how", "does", "did",
})

EXCERPT_LEN = 500


def tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower())
            if t not in STOPWORDS and len(t) >= 3}


def search_knowledge_base(session, query: str, tags: list[str],
                          as_of: str | None = None,
                          limit: int = 5) -> list[dict]:
    """Tag + keyword retrieval (ILIKE-grade matching in Python, ranked [ID]).

    score = 2 x tag overlap + keyword hits on title/content. Docs with no
    signal are excluded (empty result -> explicit no-doc path). Docs whose
    effective_date predates `as_of` are annotated outdated ("may be
    superseded"), never silently dropped.
    """
    from sqlalchemy import select

    from app.models import KnowledgeDocument  # deferred: avoid import cycle

    wanted_tags = {t.lower() for t in (tags or [])}
    query_toks = tokens(query)
    as_of_date = date.fromisoformat(as_of) if as_of else None
    out = []
    for d in session.execute(select(KnowledgeDocument)).scalars().all():
        doc_tags = {t.lower() for t in (d.tags or [])}
        tag_hits = len(wanted_tags & doc_tags)
        hay = f"{d.title} {d.content_text}".lower()
        kw_hits = sum(1 for t in query_toks if t in hay)
        score = 2 * tag_hits + kw_hits
        if score == 0:
            continue
        outdated = bool(as_of_date and d.effective_date
                        and d.effective_date < as_of_date)
        out.append({
            "id": str(d.id), "title": d.title, "version": d.version,
            "doc_type": d.doc_type,
            "effective_date": (d.effective_date.isoformat()
                               if d.effective_date else None),
            "excerpt": (d.content_text or "")[:EXCERPT_LEN],
            "tags": list(d.tags or []), "score": score,
            "superseded": d.superseded_by is not None,
            "superseded_by": (str(d.superseded_by)
                              if d.superseded_by else None),
            "outdated": outdated,
        })
    out.sort(key=lambda d: (-d["score"], d["title"]))
    return out[:limit]


def detect_conflict(docs: list[dict]) -> dict | None:
    """Two same-type docs with no supersession link between them: the latest
    could not be established automatically (§13.6) — needs actuary confirm.
    Returns None when the set is clean (superseded docs defer to current).
    """
    by_type: dict[str, list[dict]] = {}
    for d in docs:
        by_type.setdefault(d["doc_type"], []).append(d)
    for doc_type, group in by_type.items():
        current = [d for d in group if not d["superseded"]]
        if len(current) > 1:
            ids = {d["id"] for d in group}
            linked = any(d.get("superseded_by") in ids for d in group)
            if not linked:
                return {"doc_type": doc_type,
                        "versions": [d["version"] for d in current],
                        "titles": [d["title"] for d in current]}
    return None


def link_prior_findings(session, wf, finding) -> list[dict]:
    """Link current finding to prior-period findings on shared vocabulary
    (the "repeat monitoring item" path, §15 scenario 10)."""
    from sqlalchemy import select

    from app.models import Finding, Workflow  # deferred: avoid import cycle

    mine = tokens(f"{finding.title} {finding.narrative}")
    links = []
    priors = session.execute(
        select(Finding, Workflow)
        .join(Workflow, Finding.workflow_id == Workflow.id)
        .where(Workflow.portfolio == wf.portfolio,
               Workflow.reporting_period < wf.reporting_period,
               Workflow.id != wf.id)
        .order_by(Workflow.reporting_period.desc())
    ).all()
    for pf, pwf in priors:
        overlap = mine & tokens(f"{pf.title} {pf.narrative}")
        if len(overlap) >= 2:
            links.append({"finding_id": str(pf.id),
                          "workflow_ref": pwf.human_ref,
                          "title": pf.title,
                          "note": "repeat monitoring item"})
            if len(links) >= 2:
                break
    return links
