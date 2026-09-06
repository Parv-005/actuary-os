"""Workflows router (§10): create/demo, upload, start, status (+resume trigger),
resume, download, checkpoints."""
from __future__ import annotations

import csv
import hashlib
import io
import os
import re
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi import File as UploadFileType
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.actor import get_current_actor
from app.db import get_session
from app.models import AgentRun, File, HumanCheckpoint, User, Workflow
from app.orchestrator import engine, states
from app.orchestrator.states import apply_transition
from app.schemas.workflow import CreateWorkflow
from app.services import checkpoints as cp_svc
from app.services.audit import record_event
from app.storage.supabase import get_storage

router = APIRouter(prefix="/workflows", tags=["workflows"])

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_ROWS = 20_000
REQUIRED_KINDS = {"claims", "premium", "exposure"}
SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
DEMO_FILES = ("claims_2026_09.csv", "claims_2026_09_v2.csv",
              "premium_2026_09.csv", "exposure_2026_09.csv")


def _sanitize(name: str) -> str:
    base = os.path.basename(name or "file.csv")
    clean = SAFE_NAME.sub("_", base)[:120]
    return clean if clean.lower().endswith(".csv") else f"{clean}.csv"


def _human_ref(session: Session, period: str) -> str:
    prefix = f"MPR-{period}-"
    rows = session.execute(
        select(Workflow.human_ref).where(Workflow.human_ref.like(f"{prefix}%"))
    ).scalars().all()
    seq = 0
    for r in rows:
        try:
            seq = max(seq, int(r[len(prefix):]))
        except ValueError:
            continue
    return f"{prefix}{seq + 1:03d}"


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


def _parse_csv_text(text: str) -> tuple[list[list[str]], int]:
    rows = list(csv.reader(io.StringIO(text)))
    if len(rows) < 1 or len(rows[0]) < 2:
        raise HTTPException(415, "not a csv table", {"code": "bad_sniff"})
    n_data = len(rows) - 1
    if n_data > MAX_ROWS:
        raise HTTPException(400, f"file exceeds {MAX_ROWS} row limit", {"code": "too_big"})
    return rows, n_data


def _read_upload(up: UploadFile) -> tuple[str, bytes, list[list[str]]]:
    """Returns (sanitized_filename, data, csv_rows). Raises 400/415."""
    if not (up.filename or "").lower().endswith(".csv"):
        raise HTTPException(400, "only .csv files accepted", {"code": "not_csv"})
    data = up.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "file exceeds 10MB limit", {"code": "too_big"})
    if b"\x00" in data[:4096]:
        raise HTTPException(415, "unsupported media type (binary)", {"code": "bad_sniff"})
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    rows, _n = _parse_csv_text(text)
    return _sanitize(up.filename or "file.csv"), data, rows


def _infer_period(rows: list[list[str]], kind: str) -> str | None:
    if not rows:
        return None
    header = [h.strip().lower() for h in rows[0]]
    if kind == "claims":
        idx = next((header.index(c) for c in ("event_date", "report_date") if c in header), None)
    else:
        idx = header.index("period") if "period" in header else None
    if idx is None:
        return None
    counts: dict[str, int] = {}
    for row in rows[1:]:
        raw = (row[idx] if idx < len(row) else "").strip()
        if len(raw) >= 7 and raw[4] == "-":
            counts[raw[:7]] = counts.get(raw[:7], 0) + 1
    return max(counts, key=counts.get) if counts else None


async def _store_files(
    session: Session, wf: Workflow, actor: User,
    entries: list[tuple[str, bytes, str, str | None]],
) -> list[File]:
    """entries: (filename, data, kind, period_inferred)."""
    storage = get_storage()
    existing = set(session.execute(
        select(File.filename).where(File.workflow_id == wf.id)
    ).scalars().all())
    for filename, _data, _k, _p in entries:
        if filename in existing:
            raise HTTPException(409, f"duplicate filename: {filename}",
                                {"code": "duplicate_filename"})
    for filename, data, _k, _p in entries:
        await storage.upload_bytes(f"workflows/{wf.id}/raw/{filename}", data)
    created: list[File] = []
    for filename, data, kind, period in entries:
        f = File(
            workflow_id=wf.id, kind=kind, filename=filename,
            storage_path=f"workflows/{wf.id}/raw/{filename}",
            size_bytes=len(data), checksum=hashlib.sha256(data).hexdigest(),
            mime="text/csv", row_count=None, role="primary", period_inferred=period,
            uploaded_by=actor.id,
        )
        session.add(f)
        session.flush()
        created.append(f)
        record_event(session, workflow_id=wf.id, actor_type="human", actor=actor.name,
                     action="file_uploaded", entity_type="file", entity_id=f.id,
                     summary=f"{filename} ({kind}, {len(data)} bytes)",
                     details={"kind": kind, "checksum": f.checksum[:12]})
    session.commit()
    return created


def _kinds_present(session: Session, wf_id) -> set[str]:
    return set(session.execute(
        select(File.kind).where(File.workflow_id == wf_id, File.role != "quarantined")
    ).scalars().all())


def _maybe_auto_start(session: Session, wf: Workflow, actor: User) -> bool:
    if wf.status != states.INPUT_WAIT:
        return False
    if not REQUIRED_KINDS.issubset(_kinds_present(session, wf.id)):
        return False
    apply_transition(session, wf, states.INGESTING, actor_type="human",
                     actor=actor.name, reason="required inputs present — auto-start")
    session.commit()
    engine.launch(wf.id)
    return True


@router.get("")
def list_workflows(session: Session = Depends(get_session)) -> dict:
    rows = session.execute(
        select(Workflow).order_by(Workflow.created_at.desc()).limit(50)
    ).scalars().all()
    pending = dict(session.execute(
        select(HumanCheckpoint.workflow_id, func.count())
        .where(HumanCheckpoint.status == "pending")
        .group_by(HumanCheckpoint.workflow_id)
    ).all())
    return {"workflows": [
        {
            "id": str(w.id), "human_ref": w.human_ref, "period": w.reporting_period,
            "portfolio": w.portfolio, "status": w.status, "stage": w.stage,
            "pending_checkpoints": pending.get(w.id, 0),
            "updated_at": w.updated_at.isoformat() if w.updated_at else None,
        } for w in rows
    ]}


@router.post("", status_code=201)
async def create_workflow(
    body: CreateWorkflow,
    session: Session = Depends(get_session),
    actor: User = Depends(get_current_actor),
) -> dict:
    wf = Workflow(
        human_ref=_human_ref(session, body.reporting_period),
        portfolio=body.portfolio, reporting_period=body.reporting_period,
        status=states.CREATED, config={"expected_files": list(REQUIRED_KINDS)},
        created_by=actor.id,
    )
    session.add(wf)
    session.flush()
    record_event(session, workflow_id=wf.id, actor_type="human", actor=actor.name,
                 action="workflow_created", to_status=states.CREATED,
                 entity_type="workflow", entity_id=wf.id,
                 summary=f"created {wf.human_ref} for {body.reporting_period}")
    apply_transition(session, wf, states.INPUT_WAIT, actor_type="human",
                     actor=actor.name, reason="awaiting uploads")
    session.commit()
    if body.demo:
        storage = get_storage()
        entries: list[tuple[str, bytes, str, str | None]] = []
        for name in DEMO_FILES:
            data = await storage.download_bytes(f"demo/{name}")
            rows, _n = _parse_csv_text(data.decode("utf-8-sig", "latin-1"))
            kind = classify_kind(name, rows[0])
            entries.append((name, data, kind, _infer_period(rows, kind)))
        await _store_files(session, wf, actor, entries)
        _maybe_auto_start(session, wf, actor)
        session.refresh(wf)
    return {"id": str(wf.id), "human_ref": wf.human_ref, "status": wf.status}


@router.post("/{workflow_id}/upload", status_code=202)
async def upload_files(
    workflow_id: str,
    files: list[UploadFile] = UploadFileType(...),
    session: Session = Depends(get_session),
    actor: User = Depends(get_current_actor),
) -> dict:
    wf = session.get(Workflow, workflow_id)
    if wf is None:
        raise HTTPException(404, "workflow not found")
    if wf.status != states.INPUT_WAIT:
        raise HTTPException(409, f"upload allowed only in INPUT_WAIT (state={wf.status})",
                            {"code": "wrong_state"})
    entries: list[tuple[str, bytes, str, str | None]] = []
    for up in files:
        filename, data, rows = _read_upload(up)
        kind = classify_kind(filename, rows[0])
        entries.append((filename, data, kind, _infer_period(rows, kind)))
    created = await _store_files(session, wf, actor, entries)
    started = _maybe_auto_start(session, wf, actor)
    session.refresh(wf)
    return {
        "files": [{"id": str(f.id), "kind": f.kind, "filename": f.filename,
                   "period_inferred": f.period_inferred} for f in created],
        "status": wf.status, "auto_started": started,
    }


@router.post("/{workflow_id}/start", status_code=202)
def start_workflow(
    workflow_id: str,
    session: Session = Depends(get_session),
    actor: User = Depends(get_current_actor),
) -> dict:
    wf = session.get(Workflow, workflow_id)
    if wf is None:
        raise HTTPException(404, "workflow not found")
    if wf.status != states.INPUT_WAIT:
        raise HTTPException(409, f"cannot start from state {wf.status}",
                            {"code": "wrong_state"})
    missing = [k for k in REQUIRED_KINDS if k not in _kinds_present(session, wf.id)]
    if missing:
        raise HTTPException(409, f"missing required files: {', '.join(missing)}",
                            {"code": "missing_files"})
    apply_transition(session, wf, states.INGESTING, actor_type="human",
                     actor=actor.name, reason="manual start")
    session.commit()
    engine.launch(wf.id)
    return {"status": wf.status}


@router.get("/{workflow_id}/status")
def workflow_status(workflow_id: str, session: Session = Depends(get_session)) -> dict:
    wf = session.get(Workflow, workflow_id)
    if wf is None:
        raise HTTPException(404, "workflow not found")
    _resume_if_stale(session, wf)
    session.refresh(wf)
    runs = session.execute(
        select(AgentRun).where(AgentRun.workflow_id == wf.id)
        .order_by(AgentRun.started_at)
    ).scalars().all()
    latest: dict[tuple[str, str], AgentRun] = {}
    for r in runs:
        latest[(r.stage, r.agent)] = r
    stage_statuses = [
        {"stage": r.stage, "agent": r.agent, "state": r.status, "attempt": r.attempt,
         "duration_ms": r.duration_ms, "error": r.error}
        for _k, r in latest.items()
    ]
    pending = cp_svc.get_pending(session, wf.id)
    return {
        "id": str(wf.id), "human_ref": wf.human_ref, "status": wf.status,
        "stage": wf.stage, "stage_statuses": stage_statuses,
        "pending_checkpoints": [
            {"id": str(c.id), "type": c.checkpoint_type, "severity": c.severity,
             "blocking": c.blocking, "title": c.title,
             "context": c.context, "options": c.options}
            for c in pending
        ],
        "error": wf.error, "updated_at": wf.updated_at.isoformat() if wf.updated_at else None,
    }


def _resume_if_stale(session: Session, wf: Workflow) -> None:
    now = datetime.now(UTC)
    if wf.status not in states.RUNNABLE:
        return
    lease_free = wf.locked_until is None or wf.locked_until < now
    stale = (now - (wf.updated_at or wf.created_at)).total_seconds() > 90
    if lease_free and stale:
        wf.resume_count = (wf.resume_count or 0) + 1
        record_event(session, workflow_id=wf.id, actor_type="system", actor="orchestrator",
                     action="resume_sweep_pickup", summary="status poll triggered resume")
        session.commit()
        engine.launch(wf.id)


@router.post("/{workflow_id}/resume", status_code=202)
def resume_workflow(
    workflow_id: str,
    session: Session = Depends(get_session),
    actor: User = Depends(get_current_actor),
) -> dict:
    wf = session.get(Workflow, workflow_id)
    if wf is None:
        raise HTTPException(404, "workflow not found")
    if wf.status != states.FAILED:
        raise HTTPException(409, f"resume allowed only from FAILED (state={wf.status})",
                            {"code": "wrong_state"})
    apply_transition(session, wf, states.RETRYING, actor_type="human",
                     actor=actor.name, reason="manual retry after failure")
    wf.error = None
    session.commit()
    engine.launch(wf.id)
    return {"status": wf.status}


@router.get("/{workflow_id}/files/{file_id}/download")
async def download_file(workflow_id: str, file_id: str,
                        session: Session = Depends(get_session)):
    wf = session.get(Workflow, workflow_id)
    if wf is None:
        raise HTTPException(404, "workflow not found")
    f = session.get(File, file_id)
    if f is None or f.workflow_id != wf.id:
        raise HTTPException(404, "file not found")
    url = await get_storage().signed_url(f.storage_path)
    return RedirectResponse(url, status_code=302)


@router.get("/{workflow_id}/checkpoints")
def list_checkpoints(workflow_id: str, session: Session = Depends(get_session)) -> dict:
    wf = session.get(Workflow, workflow_id)
    if wf is None:
        raise HTTPException(404, "workflow not found")
    pending = cp_svc.get_pending(session, wf.id)
    return {"pending": [
        {"id": str(c.id), "type": c.checkpoint_type, "severity": c.severity,
         "blocking": c.blocking, "title": c.title, "context": c.context,
         "options": c.options, "raised_at": c.raised_at.isoformat()}
        for c in pending
    ]}
