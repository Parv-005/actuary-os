# Deployment runbook (Phase 16)

Target shape (§3): Next.js on Vercel → FastAPI on Render → Supabase
Postgres + Supabase Storage → OpenAI-compatible LLM API.

## 0. Provision (manual, once)

1. Supabase project → copy the pooled (`:6543`) + direct (`:5432`) URLs,
   service-role key; create bucket `vortex-files` (private).
2. LLM: any OpenAI-compatible endpoint + mini-class model.
3. Vercel + Render accounts.

## 1. Backend (Render web service)

- Root: `backend/`; build: `pip install -r requirements.txt`;
  start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
- Env:
  - `DATABASE_URL` (pooled 6543) / `DIRECT_DATABASE_URL` (direct 5432)
  - `APP_DB_SCHEMA=public`
  - `STORAGE_BACKEND=supabase`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`,
    `SUPABASE_BUCKET=vortex-files`
  - `LLM_PROVIDER=openai_compatible`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`
  - **`ENGINE_BACKGROUND=1`** (required: sync endpoint workers have no event
    loop; without it launched workflows never advance — see `engine.launch`)
  - `CORS_ORIGINS=https://<vercel-app>.vercel.app`
  - `APP_ENV=prod`
- Release command (before first deploy + after migration changes):
  `python scripts/migrate.py && python scripts/seed.py`
  (`003_static_ensure.sql` repairs envs where `002` was marked applied
  before its data section was final.)
- Keep-warm (P1): scheduled GitHub Action pinging `/health` every 10 min
  during judging windows; the UI already handles cold starts with a
  "Waking backend…" notice.

## 2. Frontend (Vercel)

- Root: `frontend/`; framework preset Next.js.
- Env: `NEXT_PUBLIC_API_URL=https://<render-service>.onrender.com`.
- `npm run build` must pass (lint + types); `npm test` (vitest) in CI.

## 3. Judging-day reset

```bash
cd backend
APP_DB_SCHEMA=public .venv/bin/python scripts/reset_demo.py \
  --api-url https://<render-service>.onrender.com
```

## 4. Local rehearsal (what CI/e2e do)

```bash
# terminal 1 — backend (needs local PG + ENGINE_BACKGROUND=1 in backend/.env)
cd backend && .venv/bin/uvicorn app.main:app --port 8100
# terminal 2 — e2e
cd frontend && PLAYWRIGHT_API_URL=http://localhost:8100 npx playwright test
```

## 5. Definition-of-Done checklist (public URL)

§24 items 1–13: dashboard + guided demo → CP-1 → CP-2 → deterministic
metrics (67.3%, +4.2pp, +13.1% severity) → finding w/ 61% contribution +
prior link → CP-4 monitor → QA-passed draft → drill-down chain →
CP-6 approval → COMPLETED → audit timeline → FAILED retry → `/health`
green + restart survival.
