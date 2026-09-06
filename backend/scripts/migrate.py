"""Idempotent migration runner. Respects APP_DB_SCHEMA; run over DIRECT pg URL.

Usage: python scripts/migrate.py
"""
import sys
from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def get_url() -> str:
    # One source of truth: pydantic-settings (env vars > backend/.env)
    from app.config import settings

    url = settings.direct_database_url or settings.database_url
    if not url:
        print("DIRECT_DATABASE_URL / DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)
    # SQLAlchemy-style URL -> psycopg URL
    return url.replace("postgresql+psycopg://", "postgresql://")


def main() -> None:
    from app.config import settings

    schema = settings.app_db_schema
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        print("No migrations found")
        return
    url = get_url()
    with psycopg.connect(url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            cur.execute(
                f"""CREATE TABLE IF NOT EXISTS "{schema}".schema_migrations (
                        version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"""
            )
            cur.execute(f"SET search_path TO \"{schema}\", public")
            for f in files:
                cur.execute("SELECT 1 FROM schema_migrations WHERE version=%s", (f.name,))
                if cur.fetchone():
                    print(f"SKIP {f.name} (already applied)")
                    continue
                print(f"APPLY {f.name}")
                cur.execute(f.read_text())
                cur.execute(
                    "INSERT INTO schema_migrations (version) VALUES (%s)", (f.name,)
                )
    print(f"Migrations up to date (schema={schema})")


if __name__ == "__main__":
    main()
