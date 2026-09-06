"""Engine/session. Pooled URL; disables prepared-statement cache for pgbouncer."""
from collections.abc import Iterator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

connect_args: dict = {}
url = settings.database_url
if "6543" in url or "pgbouncer" in url:
    connect_args = {"prepare_threshold": None, "options": "-c search_path=public"}

engine = create_engine(url, pool_pre_ping=True, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)


@event.listens_for(engine, "connect")
def _set_search_path(dbapi_conn, _rec):  # type: ignore[no-untyped-def]
    schema = settings.app_db_schema
    if schema != "public":
        # SET is transactional in Postgres: commit it or the pool's reset
        # ROLLBACK reverts the search_path after the first checkout.
        cur = dbapi_conn.cursor()
        cur.execute(f'SET search_path TO "{schema}", public')
        dbapi_conn.commit()
        cur.close()


def get_session() -> Iterator[Session]:
    with SessionLocal() as s:
        yield s


def health_check() -> bool:
    with SessionLocal() as s:
        s.execute(text("SELECT 1"))
    return True
