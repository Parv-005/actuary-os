.PHONY: setup migrate seed reset-demo dev test lint backend-test frontend-test

setup:
	bash scripts/setup.sh

migrate:
	cd backend && .venv/bin/python scripts/migrate.py

seed:
	cd backend && .venv/bin/python scripts/generate_sample_data.py && .venv/bin/python scripts/seed.py

reset-demo:
	cd backend && .venv/bin/python scripts/reset_demo.py

dev:
	cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000

test: backend-test

backend-test:
	cd backend && APP_DB_SCHEMA=test .venv/bin/pytest -q

lint:
	cd backend && .venv/bin/ruff check app scripts tests
