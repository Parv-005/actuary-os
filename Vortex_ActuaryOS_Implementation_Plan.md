# Vortex ActuaryOS — Implementation & Coding Plan (v1.1)

> **Status:** Implementation-ready plan. No code written yet.
> **Source of truth:** `Vortex_ActuaryOS_Multi_Agent_System_Specification.md` (2,498 lines, read in full).
> Items marked **[ID]** are implementation decisions where the specification is silent — not spec-derived claims.
> v1.1 folds in all review corrections (lease/heartbeat race fix, unified number canon, tool-contract unification, CP-7, dedup redesign, schema completions).

---

## 0. Decision Log (locked)

| # | Decision | Choice | Consequence |
|---|---|---|---|
| D1 | LLM provider | **OpenAI-compatible endpoint** via one thin client (`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` env vars) | Works with OpenAI, GLM/Z.ai, Groq, or local vLLM; JSON mode + tool calling through the standard API |
| D2 | Authentication | **No auth — public demo.** All mutations attributed to a seeded `Demo Actuary` user | Zero judge friction; audit trail still records a real actor. Auth seam kept (`get_current_actor()` dependency) so real auth can be added without refactoring |
| D3 | Local database | **Shared Supabase project for dev + prod** | No local Postgres. Guardrails: idempotent migrations, isolated `test` schema for tests (`APP_DB_SCHEMA=test`), `reset_demo.py` to restore clean state before judging. CI uses an ephemeral Postgres container with identical migrations |
| D4 | Build window | **1–2 weeks, full scope** | All 8 agents, prior-period continuity, evidence drill-down, deployed public prototype. Phases tagged P0/P1/P2 |

Other locked **[ID]**s: currency **₹ (INR)** (follows spec examples); **REST polling** for progress; custom DB-backed orchestrator (no Celery/Temporal); QA Agent fully deterministic; Knowledge Agent = deterministic retrieval + 1 small LLM call; Intake and Data Prep implemented as **two separate stage-agents** (matches spec §6 and §7; spec §41's "Intake/Data" is an abbreviation); state `WAITING_FOR_HUMAN` used for all human gates (spec lists both `HUMAN_REVIEW` and `WAITING_FOR_HUMAN`).

---

## 1. Executive Summary

Vortex ActuaryOS is a human-in-the-loop multi-agent system that executes one recurring actuarial workflow — the **Monthly Portfolio Review** — end-to-end: upload → intake → data preparation → validation → deterministic analysis → LLM-driven investigation → knowledge retrieval → report drafting → QA → human actuary review → decision → audit trail.

**The architectural spine of the prototype:**

1. **AI prepares. Actuary decides.** — enforced structurally: the AI never writes to `human_decisions`, never marks a report approved, never proposes a specific assumption value. Decision application is human-only; prompts contain refusal rules; assumption variance and final approval are hard gates (§12).
2. **Deterministic computation, LLM reasoning.** Every number comes from pandas/NumPy in `backend/app/analytics/`. The LLM is used in exactly three places: Insight Agent (investigation + findings narrative), Knowledge Agent (one small summarization call), Reporting Agent (executive summary prose — with numbers injected, and QA re-verifying every number deterministically).
3. **Everything resumable.** Workflow state, per-stage checkpoints (`agent_runs`), evidence, and audit events live in Postgres (Supabase). Uploaded and processed files live in Supabase Storage. A backend restart, Render cold start, or mid-run crash loses nothing; a resume sweep continues from the last completed **(stage, agent)** pair. A DB lease with heartbeat renewal makes duplicate execution impossible.
4. **Every finding traceable.** Finding → Evidence (immutable snapshot) → Metric (formula + inputs) → Dataset version → Source file → Storage path. One API call returns the whole chain; the frontend renders it as a drill-down.

**Deployment shape:** Next.js on Vercel → FastAPI on Render → Supabase Postgres + Supabase Storage → OpenAI-compatible LLM API. The judge completes the entire workflow from a browser at a public URL, using a seeded demo scenario ("Commercial construction deterioration") in which the real pipeline genuinely runs — nothing is faked.

---

## 2. MVP Scope

### 2.1 In scope (P0)

| Included | Detail |
|---|---|
| One workflow type | Monthly Portfolio Review (MPR) for one portfolio, one reporting period |
| Upload | `claims.csv`, `premium.csv`, `exposure.csv` (plus optional duplicate `claims_v2.csv` to trigger the intake checkpoint) |
| 9 agent units | Orchestrator (deterministic), Intake, Data Prep, Validation, Analysis (all deterministic); Insight (LLM), Knowledge (deterministic retrieval + 1 LLM call), Reporting (LLM prose + deterministic numbers), QA (deterministic) |
| Metrics | Loss ratio, claim frequency, claim severity, period-over-period delta, actual-vs-expected, deterioration contribution decomposition |
| Human checkpoints | **7** risk-tiered checkpoints (§12): input exception, validation blocker, ambiguous mapping (yellow), assumption variance, finding review (yellow, non-blocking), final approval, QA failure |
| Decisions | Accept / Reject / Override / Investigate Further / Request Revision / Accept Exception / Select File / Monitor / Confirm Mapping / Comment — with mandatory rationale (≥20 chars) on reject/override/accept_exception |
| Report | Executive summary, key metrics, findings, exceptions, decisions, open questions, charts |
| Audit trail | Full event log + evidence chain + drill-down UI |
| Demo mode | "Start Guided Demo" pre-stages seeded inputs in Storage; the real pipeline runs |
| Prior-period continuity | A seeded completed August workflow whose finding ("monitor construction severity") is retrieved by the September run |
| Deployment | Public URL: Vercel + Render + Supabase |

### 2.2 Out of scope (deferred — do not build)

Reserving/pricing workflows; assumption editing UI (actuaries record *decisions about* assumptions; the system stores no new assumption values); Excel export; SSE/WebSocket live push (polling instead); embeddings/semantic knowledge search (tagged + keyword retrieval instead); LLM schema mapping (deterministic alias table + fuzzy match; LLM mapping is a P2 future extension); multi-tenant/real auth; scheduled/automatic monthly runs; chat interface of any kind; admin console; regulatory filings; model registry; Celery/Redis/Temporal; external submission.

---

## 3. Architecture

```text
                         JUDGE / ACTUARY (browser)
                                   │
                    ┌──────────────▼──────────────┐
                    │  Next.js + React (Vercel)   │
                    │  Tailwind + Recharts        │
                    │  REST polling (2s)          │
                    └──────────────┬──────────────┘
                                   │ HTTPS (CORS-locked)
                    ┌──────────────▼──────────────┐
                    │  FastAPI (Render)           │
                    │  ┌────────────────────────┐ │
                    │  │ ORCHESTRATOR (pure Py) │ │  state machine, retries,
                    │  │  engine + resume sweep │ │  lease+heartbeat, audit
                    │  └───────────┬────────────┘ │
                    │  ┌───────────▼────────────┐ │
                    │  │ AGENTS (stage fns)     │ │
                    │  │ intake·data·validation │ │  deterministic
                    │  │ analysis·qa            │ │
                    │  │ insight·knowledge·rep. │ │  LLM (3 agents)
                    │  └───────────┬────────────┘ │
                    │  ┌───────────▼────────────┐ │
                    │  │ ANALYTICS TOOLS        │ │  pandas/numpy, pure
                    │  │ (deterministic calc)   │ │  functions, no LLM
                    │  └────────────────────────┘ │
                    └───┬──────────┬──────────┬───┘
                        │          │          │
              ┌─────────▼───┐ ┌────▼─────┐ ┌──▼──────────────┐
              │ Supabase    │ │ Supabase │ │ LLM API         │
              │ PostgreSQL  │ │ Storage  │ │ (OpenAI-compat) │
              │ (all state) │ │ (files)  │ │ backend-only key│
              └─────────────┘ └──────────┘ └─────────────────┘
```

**Layer responsibilities**

| Layer | Owns | Never does |
|---|---|---|
| Frontend | Presentation, polling, decision capture forms | Business logic, numbers, secret keys |
| FastAPI routers | Validation, actor seam, serialization | Workflow logic |
| Orchestrator | State transitions, retries, resume, checkpoints, audit | Calculation, prose |
| Agents | Stage-specific judgment + orchestration of tools | Direct DB writes outside their contract |
| Analytics tools | All math (pure functions over DataFrames) | LLM calls, DB access |
| LLM client | Insight investigation, knowledge summary, report prose | Arithmetic that tools can do; assumption recommendations |
| Postgres | State, evidence, metrics, decisions, audit | File blobs |
| Supabase Storage | Raw + processed CSVs | State |

**"AI prepares, actuary decides" — five structural enforcement points [ID]:**
1. Only the decisions API (human action) can insert rows into `human_decisions` and move workflow out of `BLOCKED`/`WAITING_FOR_HUMAN`.
2. `reports.status` becomes `approved` only via a human `approve` decision; the Reporting Agent can only produce `draft`; QA can only set `qa_passed`.
3. Insight/Reporting prompts contain refusal rules: never propose a specific assumption value, price change, or reserve action; output observations with evidence and escalate decision questions (§14).
4. The assumption-variance checkpoint (CP-4) is a hard gate: workflow pauses before reporting whenever observed vs configured variance ≥ 5pp.
5. UI: AI outputs are labeled "AI Observation — confidence X"; decision controls appear only in Human panels.

---

## 4. Tech Stack

| Layer | Choice | Notes |
|---|---|---|
| Frontend | Next.js 14 (App Router, TypeScript), React 18, Tailwind CSS, Recharts, lucide-react icons | No component library — hand-rolled Tailwind primitives [ID] |
| Backend | Python 3.11, FastAPI, Pydantic v2, pandas, NumPy, SQLAlchemy 2 (Core-style, typed sessions), httpx | |
| DB | Supabase PostgreSQL (same project dev+prod, D3) | Migrations = plain SQL files + tiny runner; runner respects `APP_DB_SCHEMA` [ID] |
| Storage | Supabase Storage, bucket `vortex-files` | Backend-only service key; signed URLs for downloads |
| LLM | OpenAI-compatible client (D1): env `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` (default `gpt-4o-mini` class), `LLM_PROVIDER=openai_compatible\|fake` (`fake` = deterministic canned LLM for CI/offline dev/tests) [ID] | JSON mode + tool calling; fallback single-shot path |
| Hosting | Vercel (frontend), Render (backend), Supabase (DB+Storage) | |
| Tests | pytest, httpx, Playwright, Vitest+RTL (minimal) | |
| CI | GitHub Actions | |

---

## 5. Repository Structure

```text
vortex-actuaryos/
├── README.md
├── .env.example                    # all env vars documented
├── .gitignore
├── Makefile                        # setup / migrate / seed / reset-demo / test / lint
├── .github/workflows/
│   ├── ci.yml                      # backend + frontend tests (Playwright P1)
│   └── keepalive.yml               # [P1] scheduled ping of Render /health every 10 min
├── backend/
│   ├── requirements.txt
│   ├── pyproject.toml              # ruff + pytest config
│   ├── migrations/                 # 001_init.sql, 002_seed_static.sql …
│   ├── scripts/
│   │   ├── migrate.py              # applies *.sql idempotently (schema_migrations;
│   │   │                           # respects APP_DB_SCHEMA; run over DIRECT pg URL)
│   │   ├── seed.py                 # users, knowledge, reference values,
│   │   │                           # Aug workflow (real deterministic stages),
│   │   │                           # demo files → Storage
│   │   ├── reset_demo.py           # wipes demo state + re-seeds clean (pre-judging)
│   │   └── generate_sample_data.py # writes sample_data/*.csv + history/*.csv (RNG seed 42)
│   ├── app/
│   │   ├── main.py                 # FastAPI app, CORS, routers, startup resume sweep
│   │   ├── config.py               # pydantic-settings: thresholds, env, model names
│   │   ├── db.py                   # engine/session/get_session dependency
│   │   ├── auth/
│   │   │   ├── actor.py            # get_current_actor() → seeded Demo Actuary (D2 seam)
│   │   │   └── ratelimit.py        # in-memory token-bucket middleware
│   │   ├── models/                 # SQLAlchemy models, one module per table (§8)
│   │   ├── schemas/                # API pydantic request/response schemas
│   │   ├── routers/
│   │   │   ├── health.py           # GET /health (process+DB) and /health/deep (+LLM)
│   │   │   ├── workflows.py        # create/list/get/start/upload/status/resume/files-download
│   │   │   ├── validation.py       # GET validation results
│   │   │   ├── metrics.py          # GET metrics (group_by/period filters + series=)
│   │   │   ├── findings.py         # GET findings, GET finding drill-down (evidence chain)
│   │   │   ├── decisions.py        # POST decisions (checkpoints, findings, report)
│   │   │   ├── reports.py          # GET report (versioned)
│   │   │   └── audit.py            # GET audit-log, GET agent-runs
│   │   ├── orchestrator/
│   │   │   ├── states.py           # state constants, ALLOWED_TRANSITIONS, guards
│   │   │   ├── engine.py           # run loop, lease+heartbeat, retries, resume sweep
│   │   │   └── stages.py           # STAGE_REGISTRY: (stage, agent) → agent fn, deps
│   │   ├── agents/
│   │   │   ├── base.py             # run_agent(): agent_runs lifecycle, heartbeat,
│   │   │   │                       # lease renewal, timeout
│   │   │   ├── context.py          # WorkflowContext dataclass
│   │   │   ├── intake.py  data_prep.py  validation.py  analysis.py
│   │   │   ├── insight.py  knowledge.py  reporting.py  qa.py
│   │   ├── analytics/
│   │   │   ├── loader.py           # processed CSV → DataFrame (per-request cache)
│   │   │   ├── metrics.py          # loss_ratio, frequency, severity, AvE, deltas
│   │   │   ├── decomposition.py    # contribution analysis, drilldowns, top movers
│   │   │   ├── quality.py          # outliers, small-sample, volume-shift checks
│   │   │   └── tools.py            # @tool registry exposed to Insight Agent (§9)
│   │   ├── llm/
│   │   │   ├── client.py           # OpenAI-compatible wrapper: retries, JSON mode,
│   │   │   │                       # tool loop, fake provider
│   │   │   ├── schemas.py          # InsightFindings, KnowledgeSummary, ReportDraft
│   │   │   └── cache.py            # [P1] input-hash response cache
│   │   ├── prompts/                # §14
│   │   │   ├── insight_system.md  knowledge_system.md  reporting_system.md
│   │   │   └── README.md           # prompt conventions (schema_mapping_system.md = P2)
│   │   ├── mappings/
│   │   │   └── aliases.json        # raw-column → canonical-column alias table
│   │   ├── services/
│   │   │   ├── audit.py            # record_event() — every mutation goes through here
│   │   │   ├── checkpoints.py      # raise/resolve checkpoint, decision application
│   │   │   ├── evidence.py         # immutable evidence snapshots from metrics/validations
│   │   │   ├── datasets.py         # standardize → processed CSV → Storage → versions
│   │   │   ├── reports.py          # assemble sections JSON, chart specs
│   │   │   └── qa_verify.py        # numeral-extraction number verification
│   │   ├── storage/supabase.py     # upload/download/signed-url wrapper
│   │   └── utils/ (ids.py, logging.py, time.py, numbers.py)
│   └── tests/
│       ├── conftest.py             # APP_DB_SCHEMA=test, migrate, truncate; fake storage/LLM
│       ├── unit/  (test_metrics.py test_decomposition.py test_validation_rules.py
│       │            test_states.py test_intake.py test_mapping.py test_qa_numbers.py)
│       ├── agents/ (test_insight.py test_reporting.py test_knowledge.py)
│       └── workflow/ (test_full_run.py test_failure_resume.py test_blocker_flow.py
│                     test_override_flow.py test_no_anomaly.py)
├── frontend/
│   ├── package.json  next.config.mjs  tailwind.config.ts  tsconfig.json
│   ├── src/app/                  # /dashboard, /workflows/new, /workflows/[id],
│   │                             # /workflows/[id]/findings/[findingId]
│   ├── src/components/ui/        # Card Badge Button Table Modal Tabs Toast ProgressSteps
│   ├── src/components/workflow/  # StageTracker KpiCards CheckpointPanel DecisionDialog
│   │                             # ValidationTable FindingsList FindingDetail EvidenceChain
│   │                             # ReportView AuditTimeline UploadZone ChartsPanel
│   ├── src/lib/ (api.ts types.ts format.ts)
│   ├── src/hooks/ (useWorkflowPolling.ts useDecision.ts)
│   └── tests/ (e2e/judge-flow.spec.ts, unit/*.spec.tsx)
├── sample_data/                  # generated, checked in (small: ~12k rows total)
│   ├── claims_2026_09.csv  claims_2026_09_v2.csv
│   ├── premium_2026_09.csv  exposure_2026_09.csv
│   ├── history/                  # Aug 2026 files (seed only; not for the Sep demo)
│   └── SCENARIOS.md              # the 10 seeded scenarios documented
├── docs/
│   ├── architecture.md  api.md  runbook.md  demo-script.md
└── scripts/ (setup.sh — one-command bootstrap)
```

**Storage layout** (bucket `vortex-files`): `demo/` (seeded Sep files), `history/` (seeded Aug files), `workflows/{id}/raw/…`, `workflows/{id}/processed/…`.

---

## 6. Agent Architecture

Common contract: every agent is `async def run_<name>(ctx: WorkflowContext) -> StageResult`, executed only through `agents/base.py:run_agent()` which creates an `agent_runs` row (attempt #, heartbeat, duration, error, llm_calls), renews the DB lease every 30s while running, enforces timeouts (deterministic 120s, LLM 180s), and writes audit events. `StageResult = {status: PASS|WARNING|BLOCKER|FAILED, outputs, checkpoint?, error?}`.

**LLM vs deterministic summary**

| Agent | Type | Why |
|---|---|---|
| Orchestrator | Deterministic Python | Workflow control must be reliable, replayable, free |
| Intake | Deterministic | File registry logic is rule-based |
| Data Prep | Deterministic | Alias table + fuzzy match covers demo; cheap |
| Validation | Deterministic | Rules engine; trust demands reproducibility |
| Analysis | Deterministic | Spec §10: LLM must not be the calculator |
| Insight | **LLM** (tool loop over deterministic analytics) | The demo's star: investigation judgment |
| Knowledge | Deterministic retrieval + **1 small LLM call** for summary framing | Retrieval is lookup; prose is LLM |
| Reporting | **LLM** prose only; all numbers injected from DB | Drafting is linguistic; QA re-verifies numbers |
| QA | Deterministic | Consistency checking is mechanical |

### 6.1 Orchestrator Agent (deterministic)

| Field | Content |
|---|---|
| Purpose | Decide next stage, enforce dependencies/gates, retry, pause for humans, resume after restart, prevent loops and double-runs |
| Inputs | `workflow_id`, DB state (`workflows`, `agent_runs`, `human_checkpoints`) |
| Outputs | State transitions, stage invocations, audit events, lease acquisition/renewal |
| Responsibilities | Stage sequencing over the registry of **(stage, agent)** pairs; gate checks (e.g., cannot ANALYZE with unresolved BLOCKER); retry with backoff (3 attempts, 5s/15s/45s); lease (`workflows.locked_until`, conditional UPDATE, 120s TTL, renewed every 30s by the running agent); heartbeat via `workflows.updated_at`; resume sweep; loop guard (same (stage,agent) re-entered 3× with no new artifacts → FAILED `loop_detected`; `resume_count > 5` → FAILED "stuck"); raise/resolve checkpoints |
| Tools | `states.apply_transition()`, `run_agent()`, `checkpoints.raise()`, `audit.record_event()` — no analytics tools, no LLM |
| Reads | All workflow-scoped tables |
| Modifies | `workflows` (status/stage/heartbeat/lease), `agent_runs`, `human_checkpoints` (status), `audit_events` |
| Failure conditions | DB unreachable; agent timeout; LLM invalid output ×2; stuck guard |
| Retry policy | 3 attempts, exponential backoff 5s/15s/45s (LLM: fresh attempt re-prompts); then FAILED + user-visible error + [Retry] button (RETRYING on click) |
| Human escalation | BLOCKER checkpoint (data), FAILED state (ops), WAITING_FOR_HUMAN (approvals) |
| Example in | Workflow `MPR-2026-09-002` in VALIDATED |
| Example out | `stage=analyzing → run_agent(analysis) → transition ANALYZED` + audit rows |
| Interface | `orchestrator/engine.py: async def run_workflow(workflow_id) · async def resume_stale_workflows()` |
| Tests | Legal/illegal transition matrix; retry counts; loop guard triggers; lease prevents double-run; resume skips completed (stage,agent) pairs |

### 6.2 Intake Agent (deterministic)

| Field | Content |
|---|---|
| Purpose | Determine what arrived, period, duplicates, missing expected inputs; create input exceptions |
| Inputs | Uploaded files (`files` rows + raw CSVs in Storage), workflow config (`expected: claims, premium, exposure`), reporting period |
| Outputs | `intake_report` JSON (per-file status, row counts, checksums, period inference from date columns), input exceptions as red checkpoints, file roles (primary/duplicate/quarantined/superseded) |
| Responsibilities | Classify file kind (filename heuristics + column sniff); detect duplicate *submissions* (same kind, ≥2 files); detect partial/stale files (period-column stats vs workflow period → shown in CP-1 profiles); quarantine unreadable CSVs; never silently pick a file |
| Tools | `storage.download_csv()`, `profile_csv()` (head/rows/dtypes), sha256 checksum |
| Reads | `files`, Storage raw paths |
| Modifies | `files` (role, quarantine), `human_checkpoints` (CP-1), audit |
| Failure conditions | Unreadable CSV (→ quarantine file, continue with others); zero valid files (→ BLOCKED). Deterministic; 2 attempts then BLOCKED with details |
| Retry policy | Deterministic; 2 attempts then BLOCKED with details |
| Escalation | CP-1: "Which claims file is authoritative?" options: Select v1 / Select v2 / Reject both |
| Example in | 4 files incl. `claims_2026_09.csv` (stale Aug data) + `claims_2026_09_v2.csv` (current) |
| Example out | `{kinds:{claims:[v1,v2],premium:1,exposure:1}, exceptions:[{type:DUPLICATE_SUBMISSION,severity:red,options:…}]}` |
| Interface | `agents/intake.py: async def run_intake(ctx) -> StageResult` |
| Tests | Duplicate submission → red CP; missing kind post-start (quarantine) → red CP; pre-start missing → stays INPUT_WAIT; corrupted CSV quarantined; stale v1 detected by period stats |

### 6.3 Data Prep Agent (deterministic)

| Field | Content |
|---|---|
| Purpose | Raw CSVs → canonical standardized datasets |
| Inputs | Primary raw files, alias map (`app/mappings/aliases.json`), canonical schema per kind |
| Outputs | Processed CSVs → Storage (`processed/`), `dataset_versions` rows (row counts, column map, checksum), `transform_log` JSONB (standardizations, type conversions, rejected values, dedup removals), unmapped-column list |
| Responsibilities | Column mapping (exact alias → canonical; difflib fuzzy ≥0.85 auto-map + log; <0.85 → unmapped); safe type parsing (strip `₹`, commas; reject ambiguous values individually; >1% rejection rate → warning); date normalization; categorical standardization; **exact full-row duplicates: auto-removed per the pre-approved validated rule in workflow config** (spec §7 permits auto-deletion *with* a validated rule) — recorded in `transform_log` + audit + INFO validation notice + UI toast; **key collisions (same `claim_id`, differing values): never auto-merged** — flagged in `transform_log` for validation to confirm as red; join claims↔exposure on `policy_id` for enrichment (fill missing `region` from policy record — logged as green transformation) |
| Tools | `loader.read_csv()`, mapping utils, `storage.upload_processed()` |
| Reads | Raw files, alias config |
| Modifies | Storage processed/, `dataset_versions`, `human_checkpoints` (CP-3: unmapped columns only), audit |
| Failure conditions | Required canonical column unresolvable (e.g., no `claim_amount` candidate) → BLOCKER listing affected metrics; conversion rejection rate >1% → warning |
| Retry policy | Deterministic, idempotent (output checksum dedupe) |
| Escalation | CP-3 yellow (non-blocking): unmapped column with fuzzy suggestions — Confirm mapping / Ignore |
| Example in | `claim_amt` column, `premium` as `"₹1,20,000"` |
| Example out | canonical `incurred_amount` numeric; transform_log records conversions, standardizations, 12 exact-duplicate removals |
| Interface | `agents/data_prep.py: async def run_data_prep(ctx)` |
| Tests | Alias mapping; fuzzy threshold; rupee parsing; exact-dupes removed with audit record; key collisions flagged not merged; missing required column → BLOCKER naming blocked metrics |

### 6.4 Validation Agent (deterministic)

| Field | Content |
|---|---|
| Purpose | "Can we trust this data?" — 4 levels per spec §8 |
| Inputs | `dataset_versions`, `reference_values` (system-of-record totals, expected LRs, assumptions), prior-period metrics (seeded Aug workflow) |
| Outputs | `validation_results` rows: check_id, category (structural/record/reconciliation/behavioral), severity INFO/WARNING/BLOCKER, status PASS/WARNING/BLOCKER, message, details JSONB (affected rows, source vs transformed totals) |
| Responsibilities | **L1 structural**: schema, types, row counts, mandatory fields, **period coverage** (% of event dates inside reporting period; <90% → BLOCKER, e.g., stale file chosen at CP-1). **L2 record**: missing values (policy per field: `region` fixable=warning-fixed, `incurred_amount` missing=blocker for financial metrics), invalid dates, negative premiums, key-collision duplicates, impossible relationships (`event_date > report_date`), post-dedup duplicate verification. **L3 reconciliation**: processed totals vs `reference_values` (tolerances [ID]: ≤0.5% PASS, 0.5–2.0% WARNING, >2.0% BLOCKER) and vs prior period; missing reference → WARNING "cannot verify". **L4 behavioral**: LR/volume shifts vs prior (portfolio LR ≥3pp → warning; volume ±30% → warning), concentration checks, rename detection (product label vanished vs prior → "possible rename; reference mapping required") |
| Tools | `analytics/quality.py` checks, reconciliation fn |
| Reads | Datasets, reference_values, prior metrics |
| Modifies | `validation_results`, CP-2 (blocker), audit |
| Failure conditions | Engine exception → FAILED |
| Retry policy | Deterministic; idempotent by check_id (upsert) |
| Escalation | Any BLOCKER → workflow BLOCKED → CP-2 with options: Accept Exception (rationale required) / Reject Data / Request Re-run |
| Example in | Premium sum ₹117.4M vs reference ₹120.0M (2.1% diff) |
| Example out | BLOCKER `recon_premium`: details `{source:120.0M, transformed:117.4M, diff_pct:2.1, threshold:2.0}` + CP-2 |
| Interface | `agents/validation.py: async def run_validation(ctx)` |
| Tests | Threshold boundaries (0.4/0.6/2.1%); region-missing auto-fix logged; outlier flagged "unusual but not proven invalid"; small-sample warning; stale-file period coverage BLOCKER |

### 6.5 Analysis Agent (deterministic)

| Field | Content |
|---|---|
| Purpose | Execute the configured metric catalog |
| Inputs | `dataset_versions`, prior completed workflow metrics (same portfolio → `prev_value`), `reference_values` (expected LR, assumptions, recon totals) |
| Outputs | `metrics` rows: metric_key, dimensions JSONB, period, value, prev_value, expected_value, delta_pp, unit, formula string, inputs JSONB (dataset_version ids + groupby spec), module_version, flags (small_sample, outlier) |
| Responsibilities | Compute portfolio/product/region/segment-level: loss_ratio, claim_frequency, claim_severity, lr_delta_pp, ave_variance_pp, deterioration_contribution_pct (OAT decomposition: `contribution_i = (LR_i,cur − LR_i,prev) × premium_weight_i,prev`, normalized to % of total movement); handle undefined safely (premium=0 → value NULL + `undefined_reason`, no infinity); attach small-sample flags (claims<20 or policies<10) |
| Tools | `analytics/metrics.py`, `analytics/decomposition.py` — pure pandas |
| Reads | Datasets, reference_values, prior workflow metrics |
| Modifies | `metrics` |
| Failure conditions | Missing expected baseline → metric row `unavailable` + reason (spec §11); zero exposure; engine exception |
| Retry policy | Deterministic, idempotent (upsert by natural key) |
| Escalation | None by itself; unavailable metrics surface in report "Open questions" |
| Example in | processed claims+premium+exposure for 2026-09 + Aug baselines |
| Example out | `{metric:loss_ratio, dims:{product:"Commercial",segment:"Construction",region:"South"}, period:"2026-09", value:0.781, prev:0.629, expected:0.640, delta_pp:+15.2}` |
| Interface | `agents/analysis.py: async def run_analysis(ctx)` |
| Tests | Zero-premium → NULL+reason; decomposition sums to 100%±0.01; AvE with missing baseline → unavailable; frequency uses exposure not premium |

### 6.6 Insight / Investigation Agent (LLM, tool loop)

| Field | Content |
|---|---|
| Purpose | "What is driving the movement?" — investigate drivers via deterministic tools, produce evidence-backed findings |
| Inputs | Portfolio-level metric deltas (trigger bundle), tool access to full metric matrix, validation context |
| Outputs | `findings` rows + `evidence` snapshots: title, narrative (evidence/hypothesis/conclusion separated), severity (high/med/low), confidence 0–1, evidence_ids (metric snapshots), possible_drivers[], alternatives[] (e.g., "large-loss volatility", "reporting timing"), `decision_question` for the actuary, human_review_required flag, correlation-causation caveats |
| Responsibilities | Plan drilldowns (product → region → segment → metric); call tools ≤6; decompose severity vs frequency contribution; state multiple drivers when no dominant one; refuse causation claims; if nothing material: "No material deviation identified" (spec §33). The insight **stage function deterministically computes** assumption variance (observed vs `reference_values.assumption` ≥5pp [ID]) and raises CP-4 — variance detection is not left to LLM judgment; the LLM interprets within it |
| Tools | §9 registry: `portfolio_summary`, `compare_periods`, `segment_breakdown`, `region_breakdown`, `drilldown`, `top_contributors`, `severity_vs_frequency`, `claim_outliers`, `decompose_contribution` |
| Reads | `metrics` (via tools only — no raw dataset access), `validation_results` (context) |
| Modifies | `findings`, `evidence` |
| Failure conditions | Invalid JSON ×2, tool error ×2, timeout 180s, schema mismatch |
| Retry policy | 3 LLM attempts (re-prompt with error appended); fallback: single-shot with pre-fetched bundle [ID]; then FAILED |
| Escalation | Conflicting evidence / low confidence (<0.6) → finding flagged `human_review_required` + yellow CP-5; assumption variance → red CP-4 |
| Example out | Finding: "Commercial Construction (South) is the largest contributor to portfolio deterioration" — severity high, confidence 0.9, evidence: 4 metric snapshots, alternatives: large-loss volatility, reporting delay, decision_question: "Does this warrant assumption review, pricing review, or continued monitoring?" |
| Interface | `agents/insight.py: async def run_insight(ctx)`; LLM tool-loop in `llm/client.py:run_tool_loop()` |
| Tests | FakeLLM canned tool sequence → findings reference real metric ids; invalid JSON → retry then FAILED; multi-driver case not forced to single cause; no-anomaly data → explicit no-deviation finding |

### 6.7 Knowledge / Research Agent (deterministic retrieval + 1 LLM call)

| Field | Content |
|---|---|
| Purpose | Retrieve approved context: prior reports, methodology, assumptions, definitions |
| Inputs | Findings (titles, segments, metric keys), `knowledge_documents` (seeded: Reserve Methodology v3.1 [current] + v3.0 [superseded_by v3.1], Monthly Review Aug 2026 incl. "monitor construction severity" decision, segment definitions), prior workflow's findings |
| Outputs | `evidence` rows (type=knowledge: doc id, excerpt, effective_date) linked to findings; methodology context enriching CP-4; `no_relevant_document` result when nothing matches |
| Responsibilities | Tag+keyword retrieval (`ILIKE` + tag match, ranked [ID]); detect doc conflicts/supersession (two same-type docs without supersession → "latest could not be established automatically" → human confirm); link prior-period monitoring findings to current findings ("repeat monitoring item"); one LLM call to frame the summary of retrieved docs — never to invent policy; if LLM fails → deterministic excerpt concatenation [ID] |
| Tools | `search_knowledge_base(query, tags, as_of)` (deterministic) |
| Reads | `knowledge_documents`, prior `findings`, `reference_values` |
| Modifies | `evidence`; contributes context to CP-4 (raised by insight stage) |
| Failure conditions | Retrieval empty (→ explicit no-doc result, continue); LLM summary fails → deterministic fallback |
| Retry policy | LLM 2 attempts then deterministic fallback |
| Escalation | Conflicting/superseded docs → yellow checkpoint for actuary confirmation |
| Example out | Evidence: "Monthly Review 2026-08 — Decision: monitor construction severity next periods (recorded by Demo Actuary)" → current finding tagged "repeat monitoring item" |
| Interface | `agents/knowledge.py: async def run_knowledge(ctx)` |
| Tests | Prior finding linked; version conflict surfaced; empty retrieval message; LLM failure → fallback path |

### 6.8 Reporting Agent (LLM prose, deterministic numbers)

| Field | Content |
|---|---|
| Purpose | Decision-ready draft report |
| Inputs | Metrics bundle (all rows), findings+evidence, validation exceptions (incl. accepted ones), human decisions so far (incl. CP-4 decision), knowledge citations |
| Outputs | `reports` row: version, status `draft`, sections JSONB: exec_summary (LLM prose), key_metrics (deterministic table data), findings, exceptions, decisions, open_questions (LLM), charts (deterministic chart specs for Recharts), citations |
| Responsibilities | One LLM call: executive summary + open questions only — prompt contains the numeric bundle; **numbers in prose must be copied from provided values only**; charts/metric tables assembled deterministically in `services/reports.py`; every finding listed with severity/confidence/evidence count; accepted exceptions listed in "Exceptions" with rationale |
| Tools | none (reads pre-assembled bundle); `services/reports.py:assemble_report()`, `generate_chart_spec` |
| Reads | metrics, findings, evidence, validation_results, human_decisions |
| Modifies | `reports` |
| Failure conditions | LLM timeout/invalid ×2 → FAILED; missing metric in bundle → section omitted + open question added |
| Retry policy | 3 attempts; regeneration on "Request Revision" creates version+1 (history kept; regen within REPORTING is an internal loop, not a state regression) |
| Escalation | Draft always requires human approval (CP-6) |
| Example out | Exec summary naming construction severity driver with exact figures matching metrics |
| Interface | `agents/reporting.py: async def run_reporting(ctx)` |
| Tests | Sections complete; revision → version 2, v1 preserved; missing metric handled |

### 6.9 QA / Audit Agent (deterministic)

| Field | Content |
|---|---|
| Purpose | Final consistency gate before human review |
| Inputs | Report, metrics, findings, validation_results, checkpoints, agent_runs |
| Outputs | `qa_result` JSONB on report: checks[] each PASS/FAIL with details; report status → `qa_passed` (pass) or stays `draft` + CP-7 (fail after regen) |
| Responsibilities | **Number check** (`services/qa_verify.py`): every numeral in LLM prose must match a metric value (or rounded form) within ±0.05pp — the anti-hallucination guard; required metrics present; every finding has ≥1 evidence ref; no unresolved red checkpoints; all warnings visible in report; all stages `succeeded`; decisions recorded where required; staleness check (metric `computed_at` older than latest dataset upload → mark stale, trigger analysis re-run) |
| Tools | `qa_verify` number-verify service |
| Reads | everything workflow-scoped |
| Modifies | `reports.qa_result/status`, CP-7, audit |
| Failure conditions | A check FAILs → report blocked; one auto-regeneration of prose (REPORTING v+1) attempted, re-QA; second failure → CP-7 red |
| Retry policy | Deterministic; 1 regeneration cycle |
| Escalation | QA failure shown to actuary with exact mismatch (spec §16 example); CP-7 choices: Request Revision (→ REPORTING) / Escalate (stays WAITING_FOR_HUMAN). No override-without-fix path |
| Example out | `FAIL: report "11.1%" vs metric 11.7% (loss_ratio Commercial)` |
| Interface | `agents/qa.py: async def run_qa(ctx)` |
| Tests | Injected mismatch caught; hallucinated number caught; rounding-tolerance pass; all-pass path; regeneration cycle |

---

## 7. Workflow State Machine

### 7.1 States

| State | Meaning | Exited by |
|---|---|---|
| CREATED | Workflow row exists; config chosen | Orchestrator → INPUT_WAIT |
| INPUT_WAIT | Waiting for required uploads (no checkpoint raised for simply-not-yet-uploaded) | Upload API (auto-start when required kinds present, only from INPUT_WAIT) → INGESTING; manual /start → INGESTING (409 if required kinds missing) |
| INGESTING | Intake + data prep running (sub-stage in `workflows.stage`) | PASS → VALIDATING; input exception → BLOCKED |
| VALIDATING | Validation agent running | No blockers → VALIDATED; blocker → BLOCKED |
| VALIDATED | Data trusted (warnings allowed, surfaced) | Orchestrator → ANALYZING |
| ANALYZING / ANALYZED | Metrics computing / done | Orchestrator |
| INVESTIGATING | Insight + Knowledge agents running (one `agent_runs` row each) | → INSIGHTS_READY |
| INSIGHTS_READY | Findings complete, assumption check done | → REPORTING; if CP-4 raised → WAITING_FOR_HUMAN, then REPORTING |
| REPORTING | Report drafting (internal version loop on regen) | → QA |
| QA | QA agent verifying | Pass → WAITING_FOR_HUMAN (CP-6); fail → REPORTING (1 auto-regen) → pass/fail; second fail → WAITING_FOR_HUMAN (CP-7) |
| WAITING_FOR_HUMAN | Hard human gate (CP-4 / CP-6 / CP-7 only) | Decisions API → APPROVED / REPORTING (revision) |
| APPROVED | Final approval decision recorded | Orchestrator finalize → COMPLETED |
| COMPLETED | Terminal, immutable; audit finalized | — (no exits) |
| BLOCKED | Data/validation condition prevents safe progress (CP-1 / CP-2) | Decision: re-enter paused stage (accept exception / select file / re-run); Reject Data → REJECTED |
| RETRYING | Transient failure backoff in-flight | → same stage state or FAILED |
| FAILED | 3 attempts exhausted; error visible; [Retry] available | Human retry → RETRYING (paused-error state, NOT terminal) |
| REJECTED | Human rejected data terminally | Terminal; new workflow with corrected files (linked via `supersedes_workflow_id`) |
| CANCELLED [ID] | Abandoned by human | Terminal |

Spec's remaining input-stage alternates map as follows: pre-start missing files → INPUT_WAIT; post-start gap (e.g., quarantine) → BLOCKED via CP-1. **Yellow checkpoints (CP-3, CP-5) never change workflow state** — they surface via the status endpoint and a side panel while the machine continues.

### 7.2 Rules

- **Single writer:** transitions only via `states.apply_transition()` with an `ALLOWED_TRANSITIONS` map + guard predicates; illegal transition raises (tested).
- **Actors:** Orchestrator owns all stage-state transitions; the **decisions API is the only path** out of `BLOCKED`/`WAITING_FOR_HUMAN`; retrying is human-triggered.
- **Every transition** writes `audit_events` (from_status, to_status, actor, reason).
- **Resume granularity is the (stage, agent) pair:** the run loop skips any pair with a `succeeded` `agent_runs` row; partial stage outputs are completed via upsert (analysis recomputes only missing metric keys).
- **Restart survival:** state lives in `workflows.status` + `workflows.stage`; nothing critical in memory. **Resume sweep** runs (a) at app startup, (b) on every `GET /status` call: workflow is stale iff `status ∈ ACTIVE ∧ locked_until < now() ∧ updated_at older than 90s` [ID] → acquire lease (conditional UPDATE on `locked_until`, 120s TTL) → resume from last incomplete (stage, agent). **Lease renewal:** `run_agent()` renews `locked_until = now()+120s` every 30s while a stage runs, so a 180s LLM stage can never be double-executed by a concurrent sweep. Duplicate execution is structurally impossible.
- **Failure flow:** agent error → attempt logged in `agent_runs`, `RETRYING` (5s/15s/45s), 3rd failure → `FAILED` with error surfaced in UI + [Retry]. Completed stages are never re-run on resume (no expensive rework; LLM results already committed as findings/metrics).
- **Loop guard:** same (stage, agent) re-entered 3× with no new artifacts → FAILED `loop_detected`; `resume_count > 5` → FAILED "stuck — surfaced on dashboard".

---

## 8. Database Schema

All tables UUID PKs (`gen_random_uuid()`), `created_at timestamptz default now()`. FKs cascade from `workflows`. Convention: JSONB for flexible payloads, numeric for money/ratios, immutable append-only tables (`evidence`, `audit_events`, `human_decisions`) never updated.

```text
users (seeded: Demo Actuary)
  │
  └─< workflows (created_by)
        │
        ├─< files                        (raw uploads, roles, quarantine)
        ├─< dataset_versions             (standardized CSVs → Storage)
        ├─< validation_results           (checks L1–L4)
        ├─< metrics                      (deterministic outputs)
        ├─< findings ──< evidence        (immutable snapshots; refs metrics/
        │                                 validation/knowledge/files)
        ├─< human_checkpoints            (CP-1…CP-7)
        ├─< human_decisions              (immutable; actor, rationale)
        ├─< reports                      (versioned; qa_result)
        ├─< agent_runs                   (per-stage checkpoints, retries, cost)
        └─< audit_events                 (append-only)

knowledge_documents (standalone; referenced by evidence type=knowledge)
reference_values     (standalone; recon totals, expected LR, assumptions,
                      historical LR series)
schema_migrations    (runner bookkeeping)
```

### 8.1 Tables (columns · types · keys · constraints)

**users** — demo actors. `id pk · email unique · name · role enum(actuary,admin,viewer) · created_at`. Seed: `demo.actuary@vortex.app` (role actuary).

**workflows** — one MPR run. `id pk · human_ref text unique (MPR-2026-09-002; auto-incremented suffix, multiples per portfolio+period allowed) · workflow_type default 'monthly_portfolio_review' · portfolio text · reporting_period text 'YYYY-MM' · status text check(states) · stage text · config jsonb (thresholds incl. validated dedup rule, expected files) · error jsonb · locked_until timestamptz null · resume_count int default 0 · supersedes_workflow_id uuid null · created_by fk users · created_at/updated_at/completed_at`. Index: `(status)`, `(reporting_period)`. `updated_at` doubles as heartbeat.

**files** — uploads. `id pk · workflow_id fk · kind enum(claims,premium,exposure,unknown) · filename · storage_path · size_bytes · checksum sha256 · mime · row_count int null · role enum(primary,duplicate,quarantined,superseded) · period_inferred text null · uploaded_by fk users · uploaded_at`. Unique `(workflow_id, filename)`; index `(workflow_id, kind)`.

**dataset_versions** — standardized data. `id pk · workflow_id fk · kind enum(claims,premium,exposure) · source_file_ids uuid[] · storage_path · row_count · column_map jsonb (raw→canonical) · transform_log jsonb · checksum · created_at`. Index `(workflow_id)`.

**validation_results** — checks. `id pk · workflow_id fk · check_id text · check_name · category enum(structural,record,reconciliation,behavioral) · severity enum(INFO,WARNING,BLOCKER) · status enum(PASS,WARNING,BLOCKER,ACCEPTED_EXCEPTION,RESOLVED) · message · details jsonb · affected_row_count int · resolved_by fk users null · resolved_at · resolution jsonb`. Unique `(workflow_id, check_id)`.

**metrics** — deterministic outputs. `id pk · workflow_id fk · metric_key text · dimensions jsonb · period text · value numeric null · prev_value · expected_value · delta_pp · unit · undefined_reason text null · flags jsonb (small_sample, outlier) · formula text · inputs jsonb · dataset_version_ids uuid[] · module_version · computed_at`. Index `(workflow_id, metric_key)`; natural key `(workflow_id, metric_key, dimensions, period)` (jsonb equality supported).

**evidence** — immutable snapshots. `id pk · workflow_id fk · finding_id fk findings null (set on link) · evidence_type enum(metric,validation,knowledge,file) · ref_id text (uuid-as-string of metric/check/doc/file) · snapshot jsonb (frozen copy: value, formula, inputs, or doc excerpt) · description · created_at`. **Never updated** — survives recomputation.

**findings** — AI observations. `id pk · workflow_id fk · agent text · agent_version text · model text (LLM model used) · title · narrative text · severity enum(high,medium,low) · confidence numeric(3,2) · possible_drivers jsonb · alternatives jsonb · correlation_caveat text null · human_review_required bool · decision_question text null · data_quality text null (validation summary at creation) · status enum(draft,accepted,rejected,overridden,monitoring,investigate_further) · links jsonb (prior finding ids) · created_at/updated_at`. Index `(workflow_id)`.

**human_checkpoints** — gates. `id pk · workflow_id fk · checkpoint_type enum(input_exception,validation_blocker,schema_mapping,assumption_variance,finding_review,final_approval,qa_failure) · severity enum(red,yellow) · blocking bool · title · context jsonb (what AI found, options) · options jsonb · status enum(pending,resolved,dismissed) · raised_at · resolved_at · resolved_by fk users`.

**human_decisions** — immutable. `id pk · workflow_id fk · checkpoint_id fk null · finding_id fk null · report_id fk null · decision enum(accept,reject,override,investigate_further,request_revision,approve,select_file,accept_exception,reject_data,request_rerun,monitor,no_change_required,confirm_mapping,ignore_column,review_assumption,escalate,comment) · rationale text (NOT NULL enforced by API for reject/override/accept_exception) · payload jsonb (e.g., chosen file_id, mapping, affected report version) · decided_by fk users · decided_at`.

**reports** — versioned deliverable. `id pk · workflow_id fk · version int · status enum(draft,qa_passed,approved,superseded) · sections jsonb · body_markdown text · qa_result jsonb · generated_at · approved_by fk users null · approved_at`. Unique `(workflow_id, version)`.

**agent_runs** — checkpoints/resume unit. `id pk · workflow_id fk · agent text · stage text · attempt int · status enum(running,succeeded,failed,timeout,skipped) · input_ref jsonb · output_ref jsonb (ids of created rows) · error text · prompt_hash text null (LLM cache key) · started_at/finished_at · updated_at (heartbeat) · duration_ms · llm_calls int · tokens_in/tokens_out int · cost_usd numeric(8,4) (tokens × config rates)`. Index `(workflow_id, stage, status)`.

**audit_events** — append-only. `id pk · workflow_id fk · actor_type enum(agent,human,system) · actor text ('validation_agent','Demo Actuary') · action text · from_status/to_status text null · entity_type · entity_id uuid null · summary text · details jsonb · created_at`. Index `(workflow_id, created_at)`.

**knowledge_documents** — seeded, standalone. `id pk · title · doc_type enum(methodology,prior_report,policy,definition) · version text · effective_date · superseded_by uuid null · content_text · tags text[] · created_at`.

**reference_values** — baselines. `id pk · period text · metric_key (recon_premium_total, recon_claims_total, expected_loss_ratio, expected_severity_trend, historical_loss_ratio …) · dimensions jsonb · value numeric · source enum(system_of_record,methodology)`. Unique `(period, metric_key, dimensions)`.

### 8.2 Key relationships & invariants

- 1 workflow → 1 current report version (`status != superseded`).
- A finding's evidence is snapshotted at creation → the chain is stable even after re-runs.
- `metrics.inputs.dataset_version_ids` → `dataset_versions.source_file_ids` → `files.storage_path` completes the traceability chain.
- ACCEPTED_EXCEPTION validation rows keep `resolution` (decision id + rationale) and appear in the report's Exceptions section.
- Spec §37 explainability fields map: Finding ID→`findings.id`, Workflow ID→`workflow_id`, Data sources→evidence(type=file)+dataset chain, Calculation IDs→evidence(type=metric)+`metrics.formula`/`module_version`, Agent→`agent`+`agent_version`, Model/version→`model`, Evidence→`evidence`, Confidence→`confidence`, Human decisions→`human_decisions`, Timestamp→`created_at`.

---

## 9. Tool Architecture

Registry in `analytics/tools.py`: `@tool(name, input_schema, output_schema)`; pure functions, no DB/LLM inside; errors returned as `{error, reason}` values, never raised to the LLM. The Insight Agent sees tool schemas via the OpenAI-compatible `tools` param. Callers restricted per agent contract (§6).

| Tool | Input → Output | Errors | Type | Callable by |
|---|---|---|---|---|
| `read_dataset` | kind → profile (rows, cols, dtypes, head) | unknown kind | Det | data_prep, validation |
| `profile_dataset` | kind → stats (nulls, distincts, ranges) | — | Det | data_prep, validation |
| `validate_schema` | kind → checks[] | — | Det | validation |
| `check_duplicates` | kind → {exact_n, key_collision_n, samples} | — | Det | data_prep, validation |
| `check_reconciliation` | kind → {source, transformed, diff_pct} | missing reference → error value | Det | validation |
| `calculate_loss_ratio` | groupby dims, period → metric rows (ids) | zero premium → undefined_reason | Det | analysis |
| `calculate_claim_frequency` | dims, period → rows | zero exposure → undefined_reason | Det | analysis |
| `calculate_claim_severity` | dims, period → rows | zero claims → undefined_reason | Det | analysis |
| `compare_periods` | metric, dims → {cur, prev, delta} | no prior → error value | Det | analysis, insight |
| `segment_breakdown` | dim, metric, filters → ranked rows | invalid dim | Det | insight |
| `region_breakdown` | metric, product → rows | — | Det | insight |
| `drilldown` | metric, filters → next-level rows | — | Det | insight |
| `top_contributors` | metric, level → contribution rows | — | Det | insight |
| `severity_vs_frequency` | segment → {severity_delta, frequency_delta, shares} | — | Det | insight |
| `claim_outliers` | segment, threshold → records | — | Det | insight, validation |
| `decompose_contribution` | metric, level → contributions summing 100% | — | Det | insight |
| `search_knowledge_base` | query, tags, as_of → docs[] with excerpts | none found → empty + flag | Det | knowledge |
| `generate_chart_spec` | chart type, data ref → Recharts-ready JSON | — | Det | reporting |

Deterministic tools return metric ids so the Insight Agent's findings can cite `evidence_ids` that resolve to stored rows.

---

## 10. API Contracts

**Progress mechanism: REST polling.** [ID] The frontend polls `GET /workflows/{id}/status` every 2s while the workflow is in an active state, and stops (with a banner) when `WAITING_FOR_HUMAN`/`BLOCKED`. Rationale: simplest; immune to Render idle connection drops; each poll doubles as a resume-sweep trigger. SSE deferred (P2).

Base URL: `https://<render-service>.onrender.com`. All bodies JSON unless noted. Errors: `{detail, code}`. Actor = seeded Demo Actuary (D2) on all mutating routes.

| Method & Path | Auth | Request | Success Response | Errors | Invokes |
|---|---|---|---|---|---|
| `GET /health` | none | — | `{status:"ok", db:"ok", version}` (Render liveness; LLM excluded so an LLM outage never triggers restarts) [ID] | 503 if db down | startup checks |
| `GET /health/deep` | none | — | `{db, storage, llm}` | — | deep probes |
| `GET /workflows` | none | `?status=&period=` | `{workflows:[{id, human_ref, period, status, stage, kpis, pending_checkpoints}]}` | — | list svc + latest metrics |
| `POST /workflows` | actor | `{reporting_period:"2026-09", portfolio:"General Insurance", demo?:bool}` → workflow `CREATED→INPUT_WAIT` (multiples per portfolio+period allowed; `human_ref` auto-increments) | 201 workflow | 400 bad period | orchestrator.create |
| `POST /workflows/{id}/upload` | actor | multipart: files[] (csv ≤10MB, ≤20k rows each) | 202 `{files:[{id,kind,filename,row_count}], status}` | 400 not csv / too big; 415 bad sniff; 409 wrong state (non-INPUT_WAIT / running) | intake classification; auto-start if complete → INGESTING |
| `POST /workflows/{id}/start` | actor | — | 202 `{status:"INGESTING"}` | 409 already running/terminal; **409 missing required files** | engine.run_workflow (background) |
| `GET /workflows/{id}/status` | none | — | `{status, stage, stage_statuses:[{stage,agent,state,attempt,duration}], pending_checkpoints, error, updated_at}` | 404 | engine status assembly + resume sweep |
| `GET /workflows/{id}/validation` | none | — | `{results:[{check_id,name,category,severity,status,message,details,resolution}]}` | 404 | validation_results |
| `GET /workflows/{id}/metrics` | none | `?metric_key=&group_by=&period=` `?series=loss_ratio` → `[{period,value,source}]` (reference history + current; powers trend charts) | `{metrics:[…], undefined:[{key, reason}]}` | 404 | metrics + reference_values |
| `GET /workflows/{id}/findings` | none | — | `{findings:[{id,title,severity,confidence,evidence_count,alternatives,status,human_review_required}]}` | 404 | findings+evidence |
| `GET /workflows/{id}/findings/{fid}` | none | — | full drill-down: finding + evidence[] each with snapshot + `chain:{metric→formula→inputs→dataset_version→files}` + linked knowledge + prior finding | 404 | evidence chain svc |
| `GET /workflows/{id}/files/{file_id}/download` | none | — | 302 to signed Storage URL | 404 | storage svc |
| `GET /workflows/{id}/checkpoints` | none | — | `{pending:[{id,type,severity,blocking,title,context,options}]}` | 404 | checkpoints svc |
| `POST /workflows/{id}/decisions` | actor | `{checkpoint_id? \| finding_id? \| report_id?, decision:<enum §8>, rationale?, payload?}` | 202 `{workflow_status, applied:[…]}` + triggers resume (frontend restarts polling) | 400 unknown target/illegal decision for type; **422 rationale required** (reject/override/accept_exception); 409 non-pending | checkpoints.apply + engine.resume |
| `GET /workflows/{id}/report` | none | `?version=` | `{report:{version,status,sections,qa_result,approved_by,approved_at}, history:[versions]}` | 404 | reports |
| `GET /workflows/{id}/audit-log` | none | `?limit=&after=` | `{events:[{ts, actor_type, actor, action, from→to, entity, summary}]}` | 404 | audit_events |
| `GET /workflows/{id}/agent-runs` | none | — | `{runs:[{agent, stage, attempt, status, duration_ms, llm_calls, tokens, cost_usd, error}]}` | 404 | agent_runs |
| `POST /workflows/{id}/resume` | actor | — (retry after FAILED) | 202 RETRYING | 409 not failed | engine |
| `GET /demo/instructions` | none | — | demo script + sample-file download links (signed URLs) | — | static |

Decision→state mapping (enforced server-side): `select_file` → re-enter INGESTING; `accept_exception` → re-enter paused stage (validation result → ACCEPTED_EXCEPTION); `reject_data` → REJECTED; `request_rerun` → re-run paused stage; `request_revision` (report, CP-6 or CP-7) → REPORTING regen v+1; `approve` (report) → APPROVED → COMPLETED; `monitor`/`no_change_required` (CP-4) → close CP, continue; `review_assumption`/`escalate` (CP-4) → close CP with flag, continue (the system records the decision to review — never a new value); `confirm_mapping`/`ignore_column` (CP-3) → applied to processed dataset (re-standardize); finding decisions (`accept/reject/override/monitor/comment`) → finding status update; `investigate_further` → re-run insight with focus payload (bounded: 1 extra round [ID]); CP-7 `escalate` → stays WAITING_FOR_HUMAN with escalated flag.

---

## 11. Frontend Architecture

Design rule: screens are organized around the **actuary's** workflow (status → evidence → decision), not agent internals. Agent names appear in the audit view and stage tracker only.

| Route / Screen | Purpose | User actions | API calls | DB data | Key components |
|---|---|---|---|---|---|
| `/` → `/dashboard` | Landing: portfolio at a glance | New Monthly Review · Start Guided Demo · open workflow | `GET /workflows` | workflows, latest metrics, checkpoints | `KpiCards` (LR, AvE, severity — the +13.1% severity card is the Construction/South segment headline, clarified by tooltip [ID]), `WorkflowList` (Aug completed / Sep live), status chips, `DemoLaunchCard` |
| `/workflows/new` | Create + upload | pick period, drop 3–4 CSVs (or "Use demo files" → same `POST /workflows {demo:true}` path), submit | `POST /workflows`, `POST .../upload`, `POST .../start` | — | `UploadZone` (drag-drop, client-side filename-heuristic kind preview [ID], validation messages), demo autofill |
| `/workflows/[id]` — **Overview tab** | Watch work happen | observe progress; open checkpoint banner | `GET /status` (poll 2s) | workflows, agent_runs, checkpoints | `StageTracker` (✓ Intake ✓ Prep ✓ Validation ● Analysis ○ … with per-stage timing + agent_run status), `CheckpointBanner`, `ChartsPanel` (Recharts: LR trend by period via `?series=`, LR by segment current-vs-prior bars, contribution bar, severity-vs-frequency), `KpiCards` |
| — **Validation tab** | Trust the data | inspect checks, drill affected records | `GET /validation` | validation_results | `ValidationTable` (severity-colored, expandable details incl. source vs transformed totals), suspicious-records drawer, INFO row for auto-applied dedup rule |
| — **Findings tab** | What moved & why | open finding, decide later | `GET /findings` | findings, evidence | `FindingsList` (severity icon 🔴🟠🟢 only, confidence, evidence count, alternatives, "repeat monitoring item" badge; small-sample surfaced as caveat, not a finding) |
| `/workflows/[id]/findings/[fid]` | Drill-down: Why did Vortex say this? | walk evidence chain; accept/reject/override/investigate/comment | `GET /findings/{fid}` | evidence, metrics, dataset_versions, files, knowledge | `FindingDetail` (narrative split evidence/hypothesis/conclusion), `EvidenceChain` breadcrumb: Finding→Evidence→Calculation (formula+inputs)→Aggregate table→Source file (download link), `DecisionDialog` |
| — **Checkpoint panel** (modal, auto-opens on blocking) | Human gates | choose option, add rationale (required for override/reject/accept_exception) | `GET /checkpoints`, `POST /decisions` | checkpoints, decisions | `CheckpointPanel` (red=blocking with workflow-paused badge; yellow=side notification), `DecisionDialog` with rationale validation (422 surfaced inline) |
| — **Report tab** | Review package | approve / request revision / add commentary | `GET /report`, `POST /decisions` | reports, metrics, findings, decisions | `ReportView` (exec summary, key metrics table, findings w/ links, exceptions incl. accepted-with-rationale, decisions log, open questions, charts), version switcher, QA badge (qa_passed), "Draft — Awaiting Actuary Approval" status |
| — **Audit tab** | Full traceability | browse events; open any entity | `GET /audit-log`, `GET /agent-runs` | audit_events, agent_runs | `AuditTimeline` (ts, actor, action, from→to state, entity link), `AgentRunTable` (attempts, retries, LLM cost) |

Polling hook: `useWorkflowPolling(id)` — active states → 2s interval; `WAITING_FOR_HUMAN`/`BLOCKED` → stop + banner "Action required"; `FAILED` → error card with [Retry]. After `POST /decisions`, `useDecision` restarts polling. "Waking backend…" interstitial when the API is cold (>2s latency) [ID].

---

## 12. Human-in-the-Loop Design

Risk tiers: **Green** (autonomous: standardization, deterministic calcs, chart/report generation, validated-rule dedup), **Yellow** (continue + prominent flag + optional decision), **Red** (hard pause; workflow cannot proceed without an authorized decision).

Mapping to spec §43's six checkpoints: ambiguous input→CP-1; material data issue→CP-2; methodology/assumption→CP-4; conflicting evidence→CP-5 (yellow flag + `human_review_required`); material professional conclusion→CP-4/CP-5 decisions; final approval→CP-6. (CP-3 = config decision, CP-7 = ops gate.)

| CP | Trigger (what AI found) | Tier / When | Actuary sees | Choices | Stored | Resume behavior |
|---|---|---|---|---|---|---|
| **CP-1 Input exception** | Duplicate submission (claims v1+v2) or missing kind post-start (e.g., quarantined file) | Red · Intake | "Two claims files found. Which is authoritative?" + file profiles (inferred periods, row counts, checksums) | Select v1 / Select v2 / Reject both | `human_checkpoints` red + `human_decisions(select_file, payload.file_id)` + audit; other file → `superseded` | Re-enter INGESTING with chosen primary file |
| **CP-2 Validation blocker** | Reconciliation mismatch >2.0% (seeded: premium −2.1%); key-collision duplicates; period coverage <90% | Red · Validation | Blocker details: source ₹120.0M vs transformed ₹117.4M, affected row sample, likely-cause list (actual deterioration / large-loss / timing / duplicate / scope change); for period coverage: "96% of dates outside 2026-09 — wrong file likely" (recovery: Reject Data → new workflow) | Accept Exception (rationale req.) / Reject Data / Request Re-run | decision + `validation_results.status=ACCEPTED_EXCEPTION` + rationale | Accept → VALIDATED with exception carried into report Exceptions; Reject Data → REJECTED; Re-run → VALIDATING |
| **CP-3 Ambiguous mapping** | Unmapped column w/ fuzzy candidates | Yellow · Data prep (non-blocking) | "Unmapped field `BizClass` — possible: Product / Segment / UW Class" | Confirm mapping / Ignore | decision rows + transform_log (re-standardize if confirmed) | Workflow continues; yellow never pauses the machine |
| **CP-4 Assumption variance** | Observed severity +13.1% vs configured assumption +5.0% (variance +8.1pp ≥ 5pp), tied to a finding; computed deterministically by the insight stage | Red · post-Insight (INSIGHTS_READY → WAITING_FOR_HUMAN) | Assumption alert: observed vs configured vs variance; relevant methodology doc (Reserve Methodology v3.1); **"AI does NOT recommend an assumption change"** | No Change Required / Investigate Further / Review Assumption / Escalate | decision + rationale + evidence link | Choice recorded → REPORTING; decision appears in report |
| **CP-5 Finding review** | Any high/medium finding; always for human_review_required or confidence <0.6 | Yellow · non-blocking | Finding + confidence + evidence + alternatives | Accept / Reject (rationale) / Override (rationale) / Monitor / Investigate Further / Add Comment | finding.status update + decision | Blocking: none; Investigate Further re-runs insight (bounded 1×) |
| **CP-6 Final approval** | Report qa_passed | Red · after QA | Final review checklist (✓data ✓calcs ✓findings traceable ✓exceptions reviewed ✓QA passed) + open decisions + report | Approve (optional comment) / Request Revision / Reject | `reports.status=approved`, decision, audit; then COMPLETED | Approve → COMPLETED (immutable audit finalize); Revision → REPORTING v+1 |
| **CP-7 QA failure** | Report prose fails number-consistency after 1 auto-regen | Red · after QA | Exact mismatch detail (calc value vs report value, location) | Request Revision (→ REPORTING v+1) / Escalate (stay WAITING_FOR_HUMAN) | decision + audit | No "approve anyway" path — QA integrity is non-negotiable |

Rationale enforcement: API returns 422 when `reject/override/accept_exception` lack rationale (≥20 chars) [ID]. UI shows rationale as required field with the spec's example framing ("Large-loss reserve release distorted the result"). Conflicting human decisions: later wins; both kept (append-only); UI warns "this supersedes an earlier decision". Abandoned workflows → CANCELLED.

---

## 13. Edge Cases & Failure Modes

Pattern per case: Problem → Detection → Owner → Automatic resolution → Human escalation/UI → DB/audit records → Workflow state.
**Responsible agents:** Intake — §13.1; Data prep — §13.2; Validation — §13.3; Analysis — §13.4; Insight — §13.5; Knowledge — §13.6; Reporting/QA — §13.7; Orchestrator — §13.8; Human flows — §13.9.

### 13.1 Intake

| Case | Detection | Auto | Escalation / UI | Rec | State |
|---|---|---|---|---|---|
| Missing file (pre-start) | required-kind count < expected | — | "Waiting for exposure data" — no false complete, no checkpoint | audit | INPUT_WAIT |
| Missing file (post-start, e.g., quarantine) | kind absent after start | — | CP-1 red | checkpoint | BLOCKED |
| Duplicate file | ≥2 files same kind | — | CP-1: select authoritative | decision + file roles | BLOCKED → INGESTING |
| Wrong file period | period stats of date cols ≠ workflow period (shown in CP-1 profiles) | — | CP-1 profiles; choosing stale v1 anyway → validation period-coverage BLOCKER (recovery: Reject Data → new workflow) | files.period_inferred | BLOCKED |
| Unsupported type / corrupted | extension + CSV sniff, parse attempt | quarantine file, keep others | "claims_x.csv quarantined: unreadable at row 41" | role=quarantined, audit | BLOCKED if kind now missing |
| Empty file | row_count=0 | — | Red: "claims file contains no records" | checkpoint | BLOCKED |
| Revised file (v2) | filename pattern + content diff | — | CP-1 (never silently choose) | as duplicate | BLOCKED |
| Unexpected extra file | unknown kind | retain as `unknown`, never used in calcs (spec) | Yellow: "Unexpected file retained, not used" | audit | continue |

### 13.2 Data

| Case | Detection | Auto | Esc / UI | Rec | State |
|---|---|---|---|---|---|
| Missing required column (incurred_amount) | alias+canonical resolution fails | stop dependent metrics | Red: "incurred_amount unresolvable — blocks loss ratio, severity" (lists affected metrics, spec §7) | checkpoint; metric rows `unavailable` | BLOCKED |
| Renamed column | alias table | map + log | none (green) | transform_log | continue |
| Unknown column | no canonical match | keep as unmapped, never used in calcs (spec) | Yellow CP-3 with fuzzy suggestions | transform_log, schema_drift flag | continue |
| Wrong type `"₹1,20,000"` | dtype sniff | safe parse (strip symbols); reject ambiguous values individually | rejected values listed; >1% rejection → warning | transform_log w/ per-value outcomes | continue |
| Invalid date | parse fail | null + count | warning w/ sample | validation_results | continue |
| Exact duplicate rows | full-row hash | **auto-removed per pre-approved validated rule** in config (spec §7 permits with rule) | informational toast + INFO validation row ("12 removed per validated rule") | transform_log + audit | continue |
| Key-collision duplicates | same claim_id, differing values | never auto-merge; flagged in transform_log | red via validation L2 → CP-2 | validation_results | BLOCKED |
| Missing values | per-field policy | region: fill via policy join (logged); incurred_amount: block financial metrics | warning/blocker per field (spec §9.1) | validation_results | continue / BLOCKED |
| Impossible values | domain rules (negative premium, event>report date) | flag rows | warning w/ affected records | validation_results | continue |
| Schema drift vs prior period | column-set diff vs Aug dataset_versions | flag new/removed | yellow "schema changed vs prior period" | audit | continue |
| Encoding problems | utf-8 → latin-1 fallback | transcode; count issues | warning if fallback used | transform_log | continue |

### 13.3 Validation

| Case | Auto | Esc / UI | State |
|---|---|---|---|
| Reconciliation mismatch | threshold class (≤0.5% pass / ≤2.0% warn / >2.0% block) | CP-2 w/ source vs transformed totals + likely-cause list (spec's 5 explanations) | BLOCKED if >2.0% |
| Unexplained outlier | p99 / mean+3σ detection | Yellow: "Claim C10291 ₹1.5cr — unusual but not proven invalid" (spec §9.2) | continue |
| Sudden volume change | ±30% policies vs prior | warning w/ breakdown | continue |
| Source disagreement | two reference values conflict | yellow "source disagreement" + both values | continue (flagged) |
| Small sample | claims<20 or policies<10 per segment | auto-tag metric `small_sample` | continue (insight must caveat) |
| Historical inconsistency | product label vanished vs prior (rename) | yellow "possible rename: Commercial SME → Commercial Small Business; reference mapping required" (spec §9.3) | continue |

### 13.4 Analysis

| Case | Auto | UI | State |
|---|---|---|---|
| Division by zero (premium=0, claims>0) | value NULL + `undefined_reason: "premium base is zero"` (spec §11) | "Undefined — premium base is zero" in metrics/report | continue |
| Missing expected baseline | metric `unavailable` + reason | listed in report Open Questions | continue |
| Zero exposure | frequency NULL + reason | as above | continue |
| Invalid segment (outside reference domains) | retain + flag, excluded from contribution ranking | yellow "unrecognized segment `X`" | continue |
| Model/calc failure | exception → agent FAILED | error + [Retry] | RETRYING/FAILED |

### 13.5 Insight

| Case | Auto | Esc / UI | State |
|---|---|---|---|
| Multiple material drivers | present all with shares; no forced single cause (spec §13) | finding lists drivers | continue |
| No dominant driver | explicit "movement distributed across segments" finding | — | continue |
| Correlation w/o causation | prompt rule + caveat field | "coincides with severe weather period; causation not established" | continue |
| Contradictory evidence | finding flagged low-confidence + human_review_required | yellow CP-5 "evidence conflict" | continue w/ flag |
| Unsupported conclusion (evidence ref invalid) | schema validation at write time rejects | agent retry | RETRYING/FAILED |
| Invalid LLM JSON ×2 / timeout | retries → fallback single-shot → FAILED | error + [Retry] | RETRYING/FAILED |

### 13.6 Knowledge

| Case | Auto | UI | State |
|---|---|---|---|
| No relevant doc | explicit `no_relevant_document` evidence | "No supporting internal documentation found" | continue |
| Conflicting/superseded docs | version compare; cannot auto-decide latest | yellow "documentation conflict v3.0 vs v3.1 — confirm" (spec §14) | continue w/ flag |
| Outdated doc | effective_date < period | "may be superseded" tag | continue |

### 13.7 Reporting / QA

| Case | Auto | Esc / UI | State |
|---|---|---|---|
| Missing metric in bundle | omit section + add Open Question | visible in report | continue |
| Stale metric (computed_at < latest dataset upload) | QA marks stale → orchestrator re-runs analysis stage | "metrics refreshed" notice | ANALYZING (re-run) |
| Report/metric number mismatch (11.1 vs 11.7) | QA FAIL → 1 auto-regeneration → re-QA | "REPORT CONSISTENCY FAILURE — calculation 11.7%, report 11.1%" (spec §16) | REPORTING (regen 1×) → WAITING_FOR_HUMAN (CP-7) on 2nd fail |
| Hallucinated number in prose | QA numeral extraction vs metric values (±0.05pp) | same as above | same |
| Unsupported narrative (no evidence ref) | QA evidence-completeness check | QA failure detail | same |

### 13.8 Orchestration

| Case | Auto | Esc / UI | State |
|---|---|---|---|
| Agent timeout | asyncio timeout (120s det / 180s LLM) → retry w/ backoff ×3 | attempts visible in agent-runs; then error + [Retry] | RETRYING→FAILED |
| Agent crash (process death) | heartbeat/lease staleness → resume sweep acquires free lease | seamless; audit shows attempt gap | same (stage, agent) re-run |
| Partial completion (8/10 metrics) | stage marked incomplete w/ missing list; upsert only missing metrics | "Analysis incomplete: frequency by region missing" | RETRYING |
| Duplicate execution | lease conditional-update + 30s renewal | prevented structurally | — |
| Stuck workflow | resume_count >5 | FAILED "stuck — surfaced on dashboard" | FAILED |
| Circular agent requests | same (stage,agent) 3× with no new artifacts | stopped + audit `loop_detected` | FAILED |
| Backend restart mid-run | all state in DB; sweep on startup & on status poll | judge sees progress continue | resumed |

### 13.9 Human

| Case | Handling | State |
|---|---|---|
| Rejection of finding | finding.status=rejected + rationale (spec §31) | continue |
| Override | stored: original finding, override, reason, ts, user, affected report version | continue |
| Request deeper investigation | insight re-run focused (≤1 extra round), new findings link prior | INVESTIGATING |
| Conflicting human decisions | later decision wins; both kept; UI warns "supersedes earlier decision" | — |
| Abandoned workflow | CANCELLED available; dashboard badge | CANCELLED |
| Approval after new data arrives | uploading to a running workflow is 409 → "start a new workflow for new data" (linked via supersedes) | — |

---

## 14. Prompt Architecture

Prompts live in `backend/app/prompts/*.md`, loaded by `llm/client.py:load_prompt(name)` (cached; interpolation via `{{placeholder}}`). No inline prompt strings in application code.

**`insight_system.md`** (role/system prompt) — enforced content:
- Role: actuarial investigation analyst. Investigate drivers of metric movements using ONLY provided tool results.
- **Hard constraints:** every number must come from tool output verbatim; every finding must cite `evidence_ids` from provided metric ids; distinguish evidence / hypothesis / conclusion; never assert causation ("coincides with", "associated with"); if no dominant driver, say so; if nothing material, output the no-deviation finding; confidence <0.6 → `human_review_required=true`.
- **Refusal/escalation rules:** never recommend an assumption value, reserve change, price change, or business action; instead set `decision_question` for the actuary (e.g., "Does this warrant assumption review?"). If data conflicts, mark conflict, do not conclude.
- Output: strict JSON matching `llm/schemas.py:InsightFindings` (pydantic-validated: `finding, severity(high|medium|low), confidence 0–1, evidence_ids[], narrative{evidence,hypothesis,conclusion}, possible_drivers[], alternatives[], correlation_caveat?, human_review_required, decision_question?`).

**`knowledge_system.md`** — summarize retrieved documents only; cite doc titles+versions+effective dates; never invent policy; empty retrieval → return `no_relevant_document`.

**`reporting_system.md`** — executive summary + open questions only; **copy numbers exactly from the provided metrics bundle** (displayed at 1 decimal); every finding mention must include its severity; no recommendations — frame as questions for actuarial judgment; accepted exceptions must appear in Exceptions.

**`schema_mapping_system.md`** — P2 future (not created in MVP; deterministic alias path is default).

Structured I/O: all LLM calls use JSON response format; tool loop uses the OpenAI-compatible `tools` param; outputs validated by pydantic → DB. Invalid JSON → one corrective re-prompt with the validation error, then failure (§6.6).

---

## 15. Sample Data

Generator: `scripts/generate_sample_data.py` (NumPy RNG seed 42), fictional insurer **"Meridian General Insurance"**, currency ₹, current period 2026-09 (Aug files emitted to `history/` for seeding only). Segments: products {Commercial, Motor, Health, Home} with Commercial segments {Construction, SME, Property, Marine Cargo}; regions {North, South, East, West}. Scale: **~6,000 policies, ~280 claims in Sep** (~12k rows total across files — fast, cheap, reviewable).

Files & canonical columns: `claims_2026_09.csv` (claim_id, policy_id, product, region, claim_type, event_date, report_date, status, incurred_amount); `premium_2026_09.csv` (policy_id, product, region, period, written_premium, earned_premium); `exposure_2026_09.csv` (policy_id, product, region, period, active_policies, earned_exposure_units). `claims_2026_09_v2.csv` = authoritative current claims; `claims_2026_09.csv` (v1) = stale Aug data relabeled → triggers CP-1.

**The 10 seeded scenarios (documented in `sample_data/SCENARIOS.md`):**

| # | Scenario | Where | Expected system behavior |
|---|---|---|---|
| 1 | Normal baselines | Motor, Health, Home | metrics computed; Health stable → green "no material deviation" finding |
| 2 | Loss-ratio increase | Commercial/Construction/South: segment LR 62.9%→78.1% (+15.2pp) | portfolio LR 63.1%→67.3% (+4.2pp, AvE +4.5pp); high-severity finding, 61% contribution |
| 3 | Severity-driven deterioration | ~15 large claims inflate Construction South severity +13.1% (frequency +2.4%) | Insight: "driven more by severity than frequency" |
| 4 | Concentrated segment | deterioration concentrated in South region | finding cites regional concentration (medium) |
| 5 | Duplicate records | 12 exact duplicate claim rows | auto-removed per validated rule — logged, audit row, INFO validation notice, UI toast |
| 6 | Missing value | region blank on ~15 claims | green auto-fix via policy join (logged) |
| 7 | Reconciliation mismatch | premium rows omitted → sum ₹117.4M vs reference ₹120.0M (−2.1%) | red CP-2 blocker |
| 8 | Outlier claim | ₹1.5cr Construction South claim (~50× average) | yellow "unusual but not proven invalid" (spec's ₹50cr example is illustrative-only; calibrated to portfolio scale [ID]) |
| 9 | Small sample | Marine Cargo: 3 policies, 4 claims, LR swings | small-sample caveat on its metrics + report note (not a finding) |
| 10 | Prior-period monitoring item | seeded Aug workflow finding + decision "monitor construction severity" | Knowledge Agent links it: "repeat monitoring item" |

Plus: assumption baseline in `reference_values` — expected severity trend for Construction +5.0% → observed +13.1% → variance +8.1pp → CP-4 assumption alert; expected portfolio LR 62.8%; Apr–Aug portfolio LR history (`historical_loss_ratio`) for trend charts.

**Exact judge-visible storyline:** two claims files → CP-1 (pick v2) → dedup toast → premium reconciliation blocker → CP-2 (accept w/ rationale) → analysis: LR 63.1%→67.3% → insight: Construction/South severity-driven deterioration, 61% contribution, outlier flagged, small-sample caveated, Health stable → knowledge: repeat monitoring item + assumption alert CP-4 (record "No change required — monitor") → report draft (all numbers from metrics) → QA pass → CP-6 approve → COMPLETED with full audit trail.

---

## 16. Demo Mode

**Demo = pre-staged inputs, real execution. Nothing is faked; the actual pipeline runs.**

- `POST /workflows {demo: true}` → creates `MPR-2026-09-00N` (next suffix), copies the four seeded CSVs from Storage `demo/` into `workflows/{id}/raw/` (identical to a manual upload; same `files` rows, same code path), then auto-starts. Both the dashboard "Start Guided Demo" card and the `/workflows/new` "Use demo files" toggle call this same API.
- Manual path remains: download sample CSVs from `/demo/instructions` (signed URLs) and upload by hand — identical outcome.
- `Start Guided Demo` card opens a step-by-step overlay (content from `docs/demo-script.md`) that follows the judge through CP-1 → CP-2 → CP-4 → CP-6.
- `scripts/reset_demo.py` (run before judging): cancels non-seeded workflows, re-seeds clean state (Aug workflow, knowledge, reference values, demo files), verifies `/health` + `/health/deep`.
- Failure resilience for the live demo: LLM cache (input-hash → `agent_runs.prompt_hash`, [P1]) replays identical results if inputs unchanged; `docs/runbook.md` covers cold-start wake-up and retry.

### 16.1 Cost Control

- **LLM used in exactly 3 places** (§6): Insight (1 tool-loop ≈ 2–3 calls with small payloads), Knowledge (1 small call), Reporting (1 call) — ~10–12k tokens per run, ≈ **$0.01/run** at mini-class pricing. Everything else is deterministic Python (free, instant, reproducible).
- **Small data** (~12k rows): trivial compute, sub-minute deterministic stages, negligible Storage.
- **Cache/reuse:** [P1] input-hash LLM cache in `agent_runs`; deterministic stages upsert (never recompute on resume); seed/history data reused across rehearsals via `reset_demo.py`.
- **Demo risk reduction:** one controlled scenario; rehearsals replay cache; rate limits bound accidental LLM spend (5 workflow-starts/min/IP).
- **Infra:** free tiers throughout (Vercel Hobby, Render free, Supabase free) — no paid services required for the MVP.

---

## 17. Error Recovery

- **Checkpoint persistence:** every stage commit = `agent_runs(succeeded)` + its outputs (dataset_versions / validation_results / metrics / findings+evidence / reports) written in one DB transaction. State never ahead of artifacts.
- **Resume logic:** run loop iterates the STAGE_REGISTRY and skips completed **(stage, agent)** pairs; partial stage outputs are completed via upsert.
- **Example:** Analysis succeeded, Insight failed ×3 → FAILED ("Insight Agent: LLM timeout after 3 attempts", visible in UI + agent-runs). Judge clicks [Retry] (or a status poll finds a free lease + stale heartbeat) → RETRYING → run loop skips intake/prep/validation/analysis (no recompute, no LLM cost) → re-executes Insight only. The running agent's 30s lease renewal guarantees the sweep never double-executes it.
- **Idempotency:** stage outputs keyed by natural keys (metrics upsert; findings replaced per re-run with link to prior; report versioning); upload paths namespaced per workflow; lease prevents concurrent double-run.
- **User notification:** FAILED state → status endpoint returns error object → UI error card with stage, message, attempts, [Retry]; audit records every attempt.

---

## 18. Auditability & Logging

Structured logging (JSON lines on stdout — Render captures): every log/event carries `workflow_id, agent, action, ts, input_ref, result/status, error, duration, llm_calls/tokens, actor`. All persisted events go through `services/audit.py:record_event()` — one code path, impossible to forget.

**"Why did Vortex make this finding?"** — one drill-down endpoint (`GET /findings/{fid}`) returns:

```text
Finding (title, severity, confidence, narrative, agent, version, model, ts)
  → Evidence[] (immutable snapshots: metric values w/ formula + inputs,
                validation checks, knowledge doc excerpts)
      → Metric (formula: loss_ratio = Σincurred/Σearned_premium,
                inputs: dataset_version_ids, groupby spec, module_version)
          → Dataset version (row_count, column_map, transform_log, checksum)
              → Source files (filename, checksum, storage_path → download)
```

Plus: `audit_events` timeline (who/what/when, every state transition with from→to, every human decision with rationale), `agent_runs` (attempts, retries, cost), and report version history — giving the spec's full Finding→Analysis→Calculation→Records→Source-file chain, rendered as breadcrumbs in the UI.

---

## 19. Security

| Area | Approach |
|---|---|
| Authentication | Public demo (D2): all mutations attributed to seeded `Demo Actuary` via `get_current_actor()` dependency — single seam to add real auth later; role field (`actuary`) stamped on every decision/audit row |
| API keys | `LLM_API_KEY`, `SUPABASE_SERVICE_KEY`, `DATABASE_URL` — **backend env only** (Render env vars). Frontend env: only `NEXT_PUBLIC_API_URL`. No key ever reaches the browser bundle |
| CORS | `CORS_ORIGINS` = Vercel prod URL + `http://localhost:3000` — nothing else |
| File validation | `.csv` extension + content sniff (text/csv), ≤10MB, ≤20k rows, sanitized filenames, per-workflow storage prefix (`workflows/{id}/…`) = upload isolation |
| Database access | Backend connects via pooled direct Postgres URL; Supabase service key used only for Storage; no DB credentials client-side |
| Rate limiting | In-memory token bucket middleware [ID]: 60 req/min/IP general; 10 uploads/min; 5 workflow-starts/min (LLM cost guard) |
| Input validation | Pydantic on every endpoint; decision enums server-enforced; rationale-required rules (422); SQL via SQLAlchemy bound params only |
| Audit | Append-only `audit_events`/`human_decisions`; no update/delete endpoints for them |

Known accepted risk (documented in README): the public demo URL allows anyone to mutate demo state — mitigated by rate limits, reset script, and a judging-day reset. [ID]

---

## 20. Local Development

```bash
# 0. Prereqs: python3.11+, node 20+, a Supabase project (D3)
git clone <repo> && cd vortex-actuaryos

# 1. Backend
cd backend && python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example .env          # fill: DATABASE_URL (Supabase pooled URL),
                                 # SUPABASE_URL/KEY, LLM_BASE_URL/API_KEY/MODEL,
                                 # LLM_PROVIDER, CORS_ORIGINS=http://localhost:3000

# 2. Database (shared Supabase project, D3)
python scripts/migrate.py        # idempotent SQL migrations
python scripts/generate_sample_data.py   # writes ../sample_data/*.csv (+ history/)
python scripts/seed.py           # users, knowledge, reference values,
                                 # Aug workflow, demo files → Storage

# 3. Run backend
uvicorn app.main:app --reload --port 8000   # http://localhost:8000/docs

# 4. Frontend (new terminal)
cd frontend && npm install && npx playwright install --with-deps
echo "NEXT_PUBLIC_API_URL=http://localhost:8000" >> .env.local
npm run dev                      # http://localhost:3000

# 5. Tests
cd backend && APP_DB_SCHEMA=test pytest   # isolated `test` schema in the same
                                 # Supabase project (migrated by conftest,
                                 # truncated after each run)
cd frontend && npx playwright test
```

`Makefile` wraps all of the above (`make setup migrate seed dev test reset-demo`). Dev/prod parity: same Supabase project (D3), same migrations, same Storage; only env URLs differ. `make reset-demo` before any rehearsal.

---

## 21. Deployment

```text
GitHub repo
 ├── Vercel  → Next.js (frontend/)      env: NEXT_PUBLIC_API_URL
 └── Render  → FastAPI (backend/)       env: DATABASE_URL (pooled), DIRECT_DATABASE_URL,
        │                                SUPABASE_URL, SUPABASE_SERVICE_KEY,
        │                                LLM_BASE_URL, LLM_API_KEY, LLM_MODEL,
        │                                LLM_PROVIDER, CORS_ORIGINS, APP_ENV
        ├── Supabase PostgreSQL          (migrations/seed applied once)
        ├── Supabase Storage (vortex-files)
        └── LLM API (OpenAI-compatible)
```

| Step | Exact commands / settings |
|---|---|
| Supabase | Create project → copy pooled `DATABASE_URL` (port 6543, pgbouncer) **and** direct URL (port 5432, for migrations) + service key; create bucket `vortex-files` (private) |
| Render | New Web Service, root `backend`; Build: `pip install -r requirements.txt`; Start: `python scripts/migrate.py && uvicorn app.main:app --host 0.0.0.0 --port $PORT`; Health check path: `/health`; env vars above; instance ≥512MB (free tier OK) |
| Seed prod | Locally with prod `.env`: `python scripts/seed.py` then `python scripts/reset_demo.py --verify` |
| Vercel | Import repo, root `frontend`, build `npm run build`, env `NEXT_PUBLIC_API_URL=https://<render>.onrender.com` |
| Keep-warm | [P1] GitHub Actions scheduled workflow (`keepalive.yml`, every 10 min) curls backend `/health` during demo hours — Vercel Hobby crons are too infrequent (once/day) for this |
| CORS | Backend `CORS_ORIGINS=https://<app>.vercel.app` (+ localhost) |
| Migrations | Plain SQL files, idempotent, tracked in `schema_migrations`; `migrate.py` runs on every Render start **over the direct connection** (DDL fails through the transaction pooler [ID]; app uses the pooled URL with `prepared_statement_cache_size=0` [ID]) |
| Cold starts | Primary: all state in DB — Render waking mid-run is harmless (resume sweep). Secondary: GHA keep-warm ping. UI "waking backend…" interstitial for the first request |
| Persistence rule | Zero local-filesystem dependence: files only in Supabase Storage, state only in Postgres; Render disk treated as ephemeral |
| Post-deploy smoke | `curl /health` → create demo workflow → full judge flow via Playwright against the prod URL (`docs/runbook.md` checklist) |

---

## 22. Testing Strategy

**Unit (pytest, deterministic — the majority):**
- `test_metrics.py`: zero-premium → NULL+reason; contribution decomposition sums to 100%±0.01; frequency from exposure; AvE sign conventions
- `test_decomposition.py`: single-dominant vs multi-driver vs no-driver synthetic frames
- `test_validation_rules.py`: recon thresholds 0.4/0.6/2.1%; region auto-fix; outlier p99; small-sample tagging; period-coverage boundary
- `test_mapping.py`: alias hits, fuzzy ≥0.85, rupee parsing, rejection of ambiguous values
- `test_states.py`: every legal transition; illegal raises; decision→state map
- `test_intake.py`: duplicate/missing/quarantine classification
- `test_qa_numbers.py`: 11.1 vs 11.7 caught; rounding tolerance pass; hallucinated numeral caught

**Agent tests (`LLM_PROVIDER=fake` — canned JSON/tool sequences, no network):** insight happy path cites real metric ids; invalid-JSON retry → FAILED; multi-driver not forced single-cause; no-anomaly → explicit finding; knowledge fallback when LLM fails; reporting revision versions.

**Workflow integration (test schema, FakeLLM + fake Storage):**
1. Full run on sample data → COMPLETED; checkpoints CP-1/2/4/6 raised & resolved; QA passed
2. Missing exposure upload → BLOCKED, no false complete
3. Validation blocker unaccepted → stays BLOCKED (cannot reach ANALYZING)
4. Insight failure injection → FAILED after 3 attempts; resume re-executes only insight (assert analysis `agent_runs` not re-run)
5. Human override flow → finding rejected w/ rationale; report shows override; audit sequence correct
6. Kill-and-resume simulation (expire lease + stale heartbeat) → sweep resumes correctly
7. No-anomaly dataset → explicit "no material deviation" finding, workflow completes

**Frontend:** Vitest+RTL for UploadZone, DecisionDialog rationale validation, StageTracker states; **Playwright e2e `judge-flow.spec.ts`** = exact judge flow (§25) against local stack (`LLM_PROVIDER=fake` for determinism); re-run manually against prod URL pre-demo (real LLM).

**CI (GitHub Actions):** backend job (ephemeral Postgres container + identical migrations — not shared Supabase, so CI never touches demo data — pytest, ruff) + frontend job (build, vitest, [P1] playwright against dev servers). Local dev uses the `test` schema in shared Supabase; CI uses a container; both run identical migrations.

---

## 23. Implementation Phases

Order rationale: schema + sample data first (they define contracts), then the deterministic spine (intake→analytics), then orchestrator (needed to string stages), then LLM agents, then human-loop, then frontend, then demo/deploy. Each phase ends deployable/verifiable.

| # | Phase | Objective (files · tasks) | Depends on | Acceptance criteria |
|---|---|---|---|---|
| 1 | **Repo & tooling** (P0) | Monorepo per §5; `.env.example`, Makefile, CI skeleton, ruff/pytest/tsconfig, README quickstart; **create Supabase project + `vortex-files` bucket (manual, per runbook)** | — | `make setup` installs both sides; CI green on empty tests; Supabase reachable |
| 2 | **Schema & migrations** (P0) | `migrations/001_init.sql` (all §8 tables + indexes + checks), `002_seed_static.sql` (users, knowledge docs, reference values incl. history series); `db.py`, `models/` | 1 | `migrate.py` idempotent re-run no-op (incl. `APP_DB_SCHEMA=test`); ER matches §8 |
| 3 | **Sample data** (P0) | `generate_sample_data.py` (seed 42) → 4 CSVs + `history/`; `SCENARIOS.md`; all 10 scenarios verifiable by inspection | — | §15 numbers reproduce exactly (LR 63.1→67.3, −2.1% recon, 12 dupes, 15 missing regions) |
| 4 | **Backend skeleton** (P0) | `main.py`, `config.py`, storage wrapper, LLM client (JSON+tools+retries+fake provider), `auth/actor.py`, ratelimit, audit service, `/health` + `/health/deep`; first Render deploy | 1,2 | `/health` live on Render; CORS locked; audit writes work |
| 5 | **Seed + Aug workflow** (P0) | `seed.py`: demo files→Storage `demo/`, history files→`history/`, reference values, knowledge docs, **Aug-2026 workflow built with real deterministic stages** (scripted insight/report content only) + decisions + audit + report | 2,3,4 | Dashboard data exists; Aug metrics+findings+report present; reset script restores state |
| 6 | **Intake + upload API** (P0) | routers/workflows (create/upload/start/status/resume/files-download), intake agent (kinds, dupes, quarantine, CP-1), `files` lifecycle, auto-start rules | 4,5 | Upload 4 files → CP-1 raised w/ profiles; quarantine path works |
| 7 | **Data prep** (P0) | `mappings/aliases.json`, mapping/fuzzy, rupee/date parsing, dedup rule, key-collision flags, policy-join region fix, processed CSV→Storage, `dataset_versions`, CP-3 | 6 | Canonical datasets produced; transform_log complete; blocker on missing incurred_amount |
| 8 | **Validation** (P0) | L1–L4 checks incl. period coverage, reconciliation vs reference_values, severity classification, CP-2 | 7 | Seeded −2.1% → BLOCKER + CP-2; warnings auto-continue; stale-file coverage check works |
| 9 | **Analytics engine** (P0) | `metrics.py`, `decomposition.py`, `quality.py`; metric catalog; undefined-reason handling; `?series=` support | 7 | All §6.5 metrics on sample data; unit tests green |
| 10 | **Orchestrator** (P0) | states.py, engine.py (loop, lease+renewal, heartbeat, retries, loop guard), stages registry, agent_runs lifecycle, resume sweep on startup+status | 6–9 | Full deterministic run INGESTING→QA-ready works; kill-mid-run resumes; no double-run under concurrent sweeps |
| 11 | **Insight + Knowledge** (P0) | tool registry, insight tool-loop + pydantic findings + evidence snapshots + deterministic CP-4, knowledge retrieval + prior-period link; prompts v1; LLM cache [P1] | 10 | Findings match §15 storyline (61% contribution, severity>frequency); evidence ids resolve; FakeLLM tests pass |
| 12 | **Reporting + QA** (P0) | report assembly (deterministic sections + LLM prose), chart specs, QA number-verify + completeness + staleness, regen cycle, CP-7 | 11 | Report draft on sample run; injected mismatch caught; QA pass path |
| 13 | **Human loop API** (P0) | decisions router (all §10 enums), checkpoint apply/resume, rationale enforcement, finding decisions, report approval → COMPLETED | 12 | Every decision→state mapping tested; audit complete |
| 14 | **Frontend core** (P0) | scaffold, dashboard, new/upload, workflow detail (StageTracker, polling, validation tab, checkpoint banner/panel, decisions) | 13 | Judge can drive CP-1/CP-2 from browser; polling shows live progress |
| 15 | **Frontend depth** (P0) | findings tab + drill-down page (EvidenceChain), report tab, audit tab, charts (trend via `?series=`), KPI cards, guided-demo overlay | 14 | Full §25 flow completable in browser |
| 16 | **Demo, tests, deploy** (P0) | Playwright judge-flow, e2e workflow tests, prod deploy (Vercel+Render+seed+reset), `docs/`, GHA keep-warm [P1], rehearsal ×2 | 15 | §26 Definition of Done checklist passes on the public URL |

Contingency fallback: P1 items cut first — LLM cache, keep-warm ping, Playwright-in-CI, guided overlay, chart polish; Knowledge Agent falls back to pure deterministic excerpts. Core demo flow is protected.

---

## 24. Acceptance Criteria (Definition of Done)

The prototype is done when **all** of the following pass on the **public URL**:

1. Judge opens URL → dashboard with Aug (completed) + ability to create Sep review.
2. "Start Guided Demo" (or manual upload of the 4 CSVs) → workflow starts.
3. Intake raises CP-1 (two claims files) → judge selects v2 → workflow resumes automatically.
4. Data prep standardizes (visible transform log; dedup-rule toast + audit row for the 12 removed rows); validation raises CP-2 (−2.1% premium reconciliation) → judge accepts with rationale → resumes.
5. Analysis computes LR 63.1%→67.3% (+4.2pp), AvE +4.5pp, severity/frequency by segment — **all numbers from the deterministic engine**.
6. Insight Agent (visible investigating via agent-runs) produces the construction/South severity finding w/ 61% contribution, confidence, alternatives, outlier & small-sample caveats, "repeat monitoring item" link to August.
7. CP-4 assumption alert appears (observed +13.1% vs assumed +5.0%, variance +8.1pp) → judge records "No change required — monitor".
8. Report draft generated (exec summary, metrics, findings, exceptions incl. the accepted one w/ rationale, decisions, open questions, charts) → QA passes.
9. Judge opens finding drill-down → full chain Finding→Evidence→Calculation→Dataset→File download works.
10. Judge approves report (CP-6) → COMPLETED.
11. Audit tab shows the complete timeline incl. every human decision with rationale.
12. Failure demo: [Retry] on a FAILED workflow resumes from the failed stage only (verifiable via agent-runs).
13. `GET /health` green; state survives a Render restart mid-run.

---

## 25. Judge Demonstration Flow (exact script)

| Step | Action | Expected UI state |
|---|---|---|
| 1 | Open URL | Dashboard: "Monthly Portfolio Review — September 2026" hero; Aug workflow card (completed, "monitor construction severity" decision visible); KPI cards; **[Start Guided Demo]** |
| 2 | Click Start Guided Demo | New-workflow page: period 2026-09 pre-filled; demo files listed w/ kinds; [Launch] |
| 3 | Launch | Workflow detail: StageTracker "● Intake"; red CP-1 banner — "Two claims files: claims_2026_09.csv (stale, Aug data) vs claims_2026_09_v2.csv (current)" with profiles + [Select v2] |
| 4 | Select v2 | Banner clears; toast "12 exact duplicate rows removed per validated rule — see Validation (INFO) & Audit"; tracker: ✓Intake ✓Prep ●Validation — transform summary (rows, type conversions, region auto-fix); then red CP-2: "Premium total ₹117.4M vs system-of-record ₹120.0M (−2.1%)" + affected rows + options |
| 5 | Accept Exception + rationale ("Known endorsement processing lag — documented") | Validation tab shows check `ACCEPTED_EXCEPTION` w/ rationale; tracker: ✓Validation ●Analysis |
| 6 | Wait (~30–60s, live) | Overview tab: KPIs fill (LR 67.3% vs 63.1%, AvE +4.5pp, Construction/South severity +13.1%); charts render; agent-runs visible in audit tab |
| 7 | Auto-pause CP-4 (investigation already complete) | Assumption alert panel: observed severity +13.1% vs configured +5.0% (variance +8.1pp); methodology link "Reserve Methodology v3.1"; explicit "**AI does NOT recommend an assumption change**"; options |
| 8 | Choose "No Change Required — monitor" (+comment) | Decision recorded; tracker: ✓Insights ●Reporting |
| 9 | Findings tab | 🔴 Commercial construction deterioration (confidence 0.9, 4 evidence, alternatives: large-loss volatility / reporting timing; **repeat monitoring item — linked to Aug**); 🟠 South concentration; 🟠 outlier ₹1.5cr "unusual, not proven invalid"; 🟢 Health stable (no material deviation); Marine Cargo metrics carry small-sample caveats |
| 10 | Click the red finding | Drill-down: narrative split evidence/hypothesis/conclusion; EvidenceChain breadcrumbs → formula (`loss_ratio = Σincurred/Σearned_premium`) → aggregate table → source file download |
| 11 | (Optional) Reject the outlier finding w/ rationale | Finding marked rejected w/ rationale; audit row |
| 12 | Report tab | "Draft — Awaiting Actuary Approval", QA badge ✓; exec summary whose every number matches metrics; Exceptions section shows the accepted reconciliation w/ rationale; Decisions section lists CP-4; Open questions listed |
| 13 | Approve (CP-6) | Final-review checklist all ✓; [Approve] → status **COMPLETED**; report locked |
| 14 | Audit tab | Full timeline: every agent action, every transition, both human decisions w/ rationale, timestamps, LLM cost per agent run |
| 15 | (Optional) Backend restarted mid-run earlier — show resume | agent-runs show the gap + "resumed from Analysis" — demonstrates crash-safety |

---

## 26. Future Extensions (post-prototype)

Real auth (Supabase Auth) + multi-user RBAC; SSE live progress; Excel export & scheduled monthly runs; embedding-based knowledge search; LLM-assisted schema mapping with human confirmation; assumption change workflow with versioned methodology registry; more workflows (reserving, pricing, regulatory); multi-tenant portfolios; admin console (loop detection, stuck workflows); audit export.

---

## 27. Self-Review

**Architecture review** — Implementable? Yes: every agent is a stage function with a DB checkpoint; no exotic infrastructure. Deterministic/LLM separation? Enforced twice: tools are pure pandas, and QA re-verifies every LLM-written number against metrics. Restarts? State+artifacts in Postgres/Storage only; lease + 30s renewal + resume sweep (sweep can never double-execute a running stage). Human checkpoints explicit? Seven, each with stored context and server-enforced decision enums. Traceability? Immutable evidence snapshots guarantee the chain survives re-runs; spec §37 fields all mapped (§8.2). Judge-usable? §25 rehearsed via Playwright.

**Complexity review — deliberate simplifications:** no Celery/Temporal (in-process async run loop + DB lease suffices); Orchestrator is pure Python, not an LLM; QA is deterministic; Knowledge retrieval is tag/keyword, not embeddings; polling, not SSE; one workflow type; monorepo; validated-rule dedup auto-applied with audit rather than a blocking decision (avoids an ordering hazard). Each replacement of a heavier mechanism is labeled [ID] above. Nothing remaining requires infrastructure beyond the three free-tier services.

**Demo review** — "Could I deploy this and demonstrate live?" Yes: seeded scenario reproduces exact numbers; LLM cache + reset script de-risk rehearsals; GHA keep-warm + wake interstitial handle cold starts; every red checkpoint has a scripted resolution; failure paths ([Retry], resume) are themselves demoable strengths.

**Residual items requiring input (none blocking, defaults chosen):**
1. LLM vendor + model to provision (any OpenAI-compatible endpoint + a gpt-4o-mini-class model; `LLM_BASE_URL`/`LLM_MODEL` when ready).
2. Supabase project + Render/Vercel accounts/URLs (needed at Phase 4/16).
3. Confirm the public URL name (e.g., `vortex-actuaryos.vercel.app`).
4. Confirm ₹/INR display and the "Meridian General Insurance" fictional branding.
5. Confirm the GHA keep-warm ping (P1) is enabled for judging day.
