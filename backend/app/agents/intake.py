"""Intake Agent (§6.2, deterministic): what arrived, period, duplicates,
missing inputs; creates input exceptions (CP-1). Never silently picks a file."""
from __future__ import annotations

import csv
import io
from collections import Counter

from sqlalchemy import select

from app.agents.base import StageResult
from app.agents.context import WorkflowContext
from app.orchestrator import states
from app.services import checkpoints
from app.services.audit import record_event

REQUIRED_KINDS = ("claims", "premium", "exposure")


def classify_kind(filename: str, header: list[str]) -> str:
    name = filename.lower()
    for kw, kind in (("claims", "claims"), ("premium", "premium"), ("exposure", "exposure")):
        if kw in name:
            return kind
    cols = {c.strip().lower() for c in header}
    if {"claim_id", "incurred_amount"} & cols:
        return "claims"
    if {"written_premium", "earned_premium"} & cols:
        return "premium"
    if {"earned_exposure_units", "active_policies"} & cols:
        return "exposure"
    return "unknown"


def parse_csv(data: bytes) -> tuple[list[str], int]:
    """Returns (header, data_row_count). Raises ValueError if unreadable."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    if "\x00" in text[:4096]:
        raise ValueError("binary content")
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration as e:
        raise ValueError("empty file") from e
    if len(header) < 2:
        raise ValueError("not a csv table (fewer than 2 columns)")
    n = sum(1 for row in reader if any(cell.strip() for cell in row))
    return [h.strip() for h in header], n


def infer_period(header: list[str], data: bytes, kind: str) -> str | None:
    """Most common YYYY-MM across the relevant date/period column."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    reader = csv.DictReader(io.StringIO(text))
    cols = {c.strip().lower(): c for c in (reader.fieldnames or [])}
    if kind == "claims":
        col = next((cols[c] for c in ("event_date", "report_date") if c in cols), None)
    else:
        col = cols.get("period")
    if col is None:
        return None
    counts: Counter[str] = Counter()
    for row in reader:
        raw = (row.get(col) or "").strip()
        if len(raw) >= 7 and raw[4] == "-":
            counts[raw[:7]] += 1
        elif len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
            counts[raw[:7]] += 1
    return counts.most_common(1)[0][0] if counts else None


def _profiles(files, wf_period: str) -> list[dict]:
    out = []
    for f in files:
        out.append({
            "file_id": str(f.id),
            "filename": f.filename,
            "row_count": f.row_count,
            "period_inferred": f.period_inferred,
            "stale": f.period_inferred is not None and f.period_inferred != wf_period,
            "checksum": f.checksum[:12],
        })
    return out


async def run_intake(ctx: WorkflowContext) -> StageResult:
    session, wf = ctx.session, ctx.workflow
    from app.models import File

    files = session.execute(
        select(File).where(File.workflow_id == wf.id).order_by(File.uploaded_at)
    ).scalars().all()

    exceptions: list[dict] = []
    quarantined = 0

    # 1. Parse + classify + period inference; quarantine unreadable
    for f in files:
        try:
            data = await ctx.storage.download_bytes(f.storage_path)
            header, n = parse_csv(data)
            kind = classify_kind(f.filename, header)
            f.row_count = n
            f.kind = kind
            f.period_inferred = infer_period(header, data, kind)
            f.role = "primary"
        except Exception as e:  # noqa: BLE001 — quarantine, keep others
            f.kind = f.kind if f.kind != "unknown" else "unknown"
            f.role = "quarantined"
            f.row_count = None
            quarantined += 1
            record_event(
                session, workflow_id=wf.id, actor_type="agent", actor="intake_agent",
                action="file_quarantined", entity_type="file", entity_id=f.id,
                summary=f"{f.filename} quarantined: unreadable ({str(e)[:120]})",
            )
        session.flush()

    valid = [f for f in files if f.role != "quarantined"]
    by_kind: dict[str, list] = {}
    for f in valid:
        by_kind.setdefault(f.kind, []).append(f)

    # 2. Unexpected extra files (§13.1): retained as unknown, never used
    for f in by_kind.get("unknown", []):
        record_event(
            session, workflow_id=wf.id, actor_type="agent", actor="intake_agent",
            action="unexpected_file", entity_type="file", entity_id=f.id,
            summary=f"Unexpected file {f.filename} retained, not used in calculations",
        )

    # 3. Zero valid files -> BLOCKED
    if not valid:
        cp = checkpoints.raise_checkpoint(
            session, wf, checkpoint_type="input_exception", severity="red",
            blocking=True,
            title="No readable input files",
            context={"exceptions": exceptions, "quarantined": quarantined},
            options=[{"decision": "request_rerun", "label": "Re-run intake"},
                     {"decision": "reject_data", "label": "Reject data"}],
        )
        states.apply_transition(session, wf, states.BLOCKED, actor_type="agent",
                                actor="intake_agent", reason="intake: no valid files")
        return StageResult(status="BLOCKER", checkpoint_id=str(cp.id),
                           outputs={"quarantined": quarantined})

    # 4. Duplicate submissions (§6.2): never silently pick (CP-1 red)
    for kind in ("claims", "premium", "exposure"):
        cands = by_kind.get(kind, [])
        if len(cands) < 2:
            continue
        for f in cands:
            f.role = "duplicate"
        session.flush()
        cp = checkpoints.raise_checkpoint(
            session, wf, checkpoint_type="input_exception", severity="red",
            blocking=True,
            title=f"Two {kind} files found — which is authoritative?",
            context={
                "kind": kind,
                "profiles": _profiles(cands, wf.reporting_period),
                "workflow_period": wf.reporting_period,
            },
            options=[{"decision": "select_file", "label": f"Select {f.filename}",
                      "payload": {"file_id": str(f.id)}} for f in cands]
            + [{"decision": "reject_data", "label": "Reject both"}],
        )
        states.apply_transition(session, wf, states.BLOCKED, actor_type="agent",
                                actor="intake_agent",
                                reason=f"intake: duplicate {kind} submission")
        return StageResult(status="BLOCKER", checkpoint_id=str(cp.id),
                           outputs={"duplicate_kind": kind})

    # 5. Missing required kind post-start (e.g., quarantined) -> CP-1 red
    missing = [k for k in REQUIRED_KINDS if not by_kind.get(k)]
    if missing:
        cp = checkpoints.raise_checkpoint(
            session, wf, checkpoint_type="input_exception", severity="red",
            blocking=True,
            title=f"Missing required input(s): {', '.join(missing)}",
            context={"missing": missing, "quarantined": quarantined,
                     "profiles": _profiles(valid, wf.reporting_period)},
            options=[{"decision": "request_rerun", "label": "Re-run intake"},
                     {"decision": "reject_data", "label": "Reject data"}],
        )
        states.apply_transition(session, wf, states.BLOCKED, actor_type="agent",
                                actor="intake_agent", reason=f"intake: missing {missing}")
        return StageResult(status="BLOCKER", checkpoint_id=str(cp.id),
                           outputs={"missing": missing})

    # 6. Stale-file warning (period mismatch) — surfaced in CP profiles; if the
    # only file for a kind is stale we still continue (validation L1 will gate
    # on period coverage). Audit for visibility.
    for f in valid:
        if f.period_inferred and f.period_inferred != wf.reporting_period:
            record_event(
                session, workflow_id=wf.id, actor_type="agent", actor="intake_agent",
                action="stale_period_detected", entity_type="file", entity_id=f.id,
                summary=(f"{f.filename}: period {f.period_inferred} != workflow "
                         f"period {wf.reporting_period}"),
            )

    report = {
        "kinds": {k: [f.filename for f in fs] for k, fs in by_kind.items() if k != "unknown"},
        "quarantined": quarantined,
        "exceptions": exceptions,
    }
    record_event(
        session, workflow_id=wf.id, actor_type="agent", actor="intake_agent",
        action="intake_complete", entity_type="workflow", entity_id=wf.id,
        summary=f"Intake OK: {len(valid)} valid files, {quarantined} quarantined",
        details=report,
    )
    return StageResult(status="PASS", outputs={"intake_report": report})
