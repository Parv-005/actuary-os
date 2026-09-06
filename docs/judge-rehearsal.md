# Judge rehearsal script (§25)

Maps each demo step to the UI. Assumes a freshly reset backend
(`reset_demo.py`) and the frontend pointed at it.

## Reset (2 min before judging)

```bash
cd backend
APP_DB_SCHEMA=public .venv/bin/python scripts/reset_demo.py \
  --api-url https://<render-service>.onrender.com
```

This migrates (incl. static seeds), cancels stray non-seed workflows, re-seeds
the August COMPLETED review (`MPR-2026-08-001`) + demo/history files, and
verifies `/health` + `/health/deep`.

Cold-start note: Render free tier sleeps. Hit `/health` once before the slot;
the dashboard shows a "Waking backend…" notice until the API responds.

## Script

| # | Action | Expected |
|---|---|---|
| 1 | Open URL | Dashboard: Sep-2026 hero, Aug card (COMPLETED, monitoring decision visible), [Start Guided Demo] |
| 2 | Start Guided Demo | Workflow detail, StageTracker on ● Intake |
| 3 | CP-1 modal (auto-opened): pick `claims_2026_09_v2.csv` → Submit | Banner clears; toast-equivalent: 12 duplicate rows removed (Validation INFO + Audit) |
| 4 | CP-2 banner → Decide → Accept exception + rationale `Known endorsement processing lag — documented` | Validation tab: `recon_premium` → ACCEPTED_EXCEPTION with rationale |
| 5 | Overview tab, wait ~1–2 min | KPIs: LR 67.3% vs 63.1%, AvE +4.5pp; trend + segment charts render |
| 6 | CP-4 assumption alert → No Change Required + comment | Tracker advances to ● Reporting. Note the banner: **AI does NOT recommend an assumption change** |
| 7 | Findings tab | 🔴 construction deterioration (confidence ~0.9, alternatives, repeat-monitoring badge); 🟠 South concentration; outlier + Health notes |
| 8 | Open the red finding | Narrative split evidence/hypothesis/conclusion; chain Finding→Evidence→Calculation→Dataset→File download |
| 9 | Report tab | "Draft — Awaiting Actuary Approval", QA ✓ badge, exceptions incl. the accepted recon w/ rationale, open questions |
| 10 | Review (CP-6) → Approve | Status COMPLETED; report locked (v1 approved) |
| 11 | Audit tab | Full timeline: agent runs, transitions, every human decision with rationale, LLM cost per run |
| 12 | (Optional) Stop the backend mid-run earlier in rehearsal | agent-runs show the gap; status poll resumes from the last completed stage |

## Failure arsenal (demoable strengths)

- Insight/LLM outage → workflow FAILED with the agent error on the Overview
  card → [Retry from failed stage] resumes only that stage (agent-runs prove it).
- QA failure → CP-7 banner with exact mismatch detail → Request revision
  regenerates v+1 → re-QA. Approve is not offered (QA integrity non-negotiable).

## Automated coverage

```bash
cd frontend
npm test                          # vitest: api helpers
PLAYWRIGHT_API_URL=http://localhost:8100 npx playwright test   # e2e vs local backend
```

`tests/e2e/judge-flow.spec.ts` drives steps 1–4 + audit through a real
browser against a live backend (deterministic stages only — insight/LLM
paths are covered by the 150 backend tests with scripted FakeLLM).
`tests/e2e/depth.spec.ts` covers steps 7–11 read-only on the seeded August
workflow.
