"""Reset demo state before judging (§16): cancel non-seeded workflows, re-seed
clean state (Aug workflow, knowledge, demo files), verify /health + /health/deep.

Usage: python scripts/reset_demo.py [--api-url URL] [--no-verify]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import seed  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.auth.actor import DEMO_EMAIL  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import User, Workflow  # noqa: E402
from app.services.audit import record_event  # noqa: E402
from app.utils.logging import json_log  # noqa: E402

TERMINAL = ("COMPLETED", "CANCELLED", "REJECTED")


def cancel_non_seed_workflows() -> int:
    n = 0
    with SessionLocal() as session:
        actor = session.execute(select(User).where(User.email == DEMO_EMAIL)).scalar_one()
        rows = session.execute(
            select(Workflow).where(
                Workflow.human_ref != seed.AUG_REF, Workflow.status.notin_(TERMINAL)
            )
        ).scalars().all()
        for wf in rows:
            frm = wf.status
            wf.status = "CANCELLED"
            record_event(session, workflow_id=wf.id, actor_type="human", actor=actor.name,
                         action="workflow_cancelled", from_status=frm, to_status="CANCELLED",
                         summary="cancelled by reset_demo")
            n += 1
        session.commit()
    print(f"cancelled {n} non-seed workflow(s)")
    return n


def verify(api_url: str) -> None:
    import httpx

    base = api_url.rstrip("/")
    r = httpx.get(f"{base}/health", timeout=15)
    assert r.status_code == 200 and r.json().get("status") == "ok", f"/health: {r.text[:200]}"
    r = httpx.get(f"{base}/health/deep", timeout=30)
    body = r.json()
    assert r.status_code == 200 and all(body.get(k) is True for k in ("db", "storage", "llm")), (
        f"/health/deep: {r.text[:200]}"
    )
    print(f"verify OK: {base}/health + /health/deep green")


def main(api_url: str = "http://localhost:8000", verify_health: bool = True) -> None:
    cancel_non_seed_workflows()
    seed.main()
    if verify_health:
        verify(api_url)
    json_log("reset_demo_complete")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--api-url", default="http://localhost:8000")
    ap.add_argument("--no-verify", action="store_true")
    args = ap.parse_args()
    try:
        main(api_url=args.api_url, verify_health=not args.no_verify)
    except Exception as e:
        print(f"reset_demo FAILED: {e}", file=sys.stderr)
        sys.exit(1)
