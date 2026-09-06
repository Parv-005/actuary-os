"""Demo router (§16/§25): static guided-demo script + sample-file manifest.

No DB access: the judge flow (exact script from §25) is fixed content; the
frontend renders it as an overlay. Sample files are the seeded Storage
`demo/` objects, launched via POST /workflows {demo:true}.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.routers.workflows import DEMO_FILES

router = APIRouter(prefix="/demo", tags=["demo"])

FILE_BLURBS = {
    "claims_2026_09.csv": "Stale claims extract (August data) — triggers CP-1",
    "claims_2026_09_v2.csv": "Current claims extract — the authoritative file",
    "premium_2026_09.csv": "September written/earned premium by policy",
    "exposure_2026_09.csv": "September active policies + exposure units",
}

STEPS = [
    {"n": 1, "title": "Open the dashboard",
     "detail": "August review shows COMPLETED with its monitoring decision; "
               "September starts from Start Guided Demo."},
    {"n": 2, "title": "Launch the guided demo",
     "detail": "Period 2026-09 is pre-filled; the 4 demo files are attached "
               "from seeded storage — no upload needed."},
    {"n": 3, "title": "Resolve CP-1: pick the claims file",
     "detail": "Two claims files detected. Select claims_2026_09_v2.csv "
               "(current) as authoritative."},
    {"n": 4, "title": "Accept CP-2 with rationale",
     "detail": "Premium reconciles to -2.1% vs system-of-record. Accept as "
               "exception with rationale (min 20 chars)."},
    {"n": 5, "title": "Watch analysis + investigation",
     "detail": "Loss ratio 63.1% -> 67.3% (+4.2pp); the insight agent links "
               "the Commercial Construction South severity finding."},
    {"n": 6, "title": "Decide CP-4: no change, monitor",
     "detail": "Observed severity +13.1% vs assumed +5.0%. Record "
               "No Change Required — the AI never proposes a new value."},
    {"n": 7, "title": "Review findings + drill down",
     "detail": "Open the red finding; walk Finding -> Evidence -> "
               "Calculation -> Dataset -> File."},
    {"n": 8, "title": "Approve the report (CP-6)",
     "detail": "QA badge must read qa_passed. Approve to COMPLETED, then "
               "read the full timeline in the Audit tab."},
]


@router.get("/instructions")
def get_instructions() -> dict:
    return {
        "period": "2026-09",
        "portfolio": "General Insurance",
        "files": [{"filename": name,
                   "blurb": FILE_BLURBS.get(name, "")}
                  for name in DEMO_FILES],
        "steps": STEPS,
    }
