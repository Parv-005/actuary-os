# Vortex ActuaryOS

Human-in-the-loop multi-agent system for the Monthly Portfolio Review.
See `Vortex_ActuaryOS_Implementation_Plan.md` (source of truth for build order)
and `Vortex_ActuaryOS_Multi_Agent_System_Specification.md` (product spec).

## Quickstart (local dev, shared Supabase project per D3)

```bash
make setup        # venv + npm install
cp .env.example backend/.env   # fill DATABASE_URL, SUPABASE_*, LLM_*
make migrate      # idempotent SQL migrations (respects APP_DB_SCHEMA)
make seed         # sample data + Aug workflow + demo files -> Storage
make dev          # uvicorn :8000  (/docs)
# new terminal:
cd frontend && npm run dev   # :3000
```

## Layout

- `backend/` — FastAPI + orchestrator + agents + deterministic analytics
- `frontend/` — Next.js 14 dashboard (REST polling, 2s)
- `sample_data/` — generated CSVs (RNG seed 42) + SCENARIOS.md
- `docs/` — architecture, api, runbook, demo-script
- `backend/migrations/` — plain idempotent SQL + `scripts/migrate.py`

## Decisions (locked)

- LLM: OpenAI-compatible endpoint (`LLM_BASE_URL/API_KEY/MODEL`), `LLM_PROVIDER=fake` for CI/tests
- Auth: public demo, seeded Demo Actuary (`get_current_actor()` seam)
- DB: shared Supabase project dev+prod; isolated `test` schema for tests
- Currency: INR (Rs.)
