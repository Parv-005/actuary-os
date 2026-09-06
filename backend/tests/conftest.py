"""Test bootstrap: isolated `test` schema (D3), migrations applied, tables truncated
between tests; fake LLM + memory storage via env."""
import os
import sys
from pathlib import Path

os.environ["APP_DB_SCHEMA"] = "test"
os.environ.setdefault("LLM_PROVIDER", "fake")
os.environ.setdefault("STORAGE_BACKEND", "memory")
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db import SessionLocal, engine  # noqa: E402
from app.models import Base  # noqa: E402


def _run_migrations() -> None:
    from scripts.migrate import main as migrate_main

    migrate_main()


@pytest.fixture(scope="session", autouse=True)
def migrations():
    _run_migrations()


@pytest.fixture(autouse=True)
def clean_db(migrations):
    _truncate()
    yield
    _truncate()


def _truncate() -> None:
    skip = {"schema_migrations"}
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            if table.name in skip:
                continue
            conn.execute(text(f'TRUNCATE TABLE "{table.name}" CASCADE'))


@pytest.fixture
def db_session():
    with SessionLocal() as s:
        yield s
