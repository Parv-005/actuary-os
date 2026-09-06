"""GET /health (Render liveness — DB only, LLM excluded so an LLM outage never
triggers restarts [ID]) and GET /health/deep (db + storage + llm probes)."""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.config import settings
from app.db import health_check
from app.llm.client import get_llm_client
from app.storage.supabase import get_storage

router = APIRouter(tags=["health"])


def _db_ok() -> bool:
    try:
        return health_check()
    except Exception:
        return False


@router.get("/health")
def health() -> JSONResponse:
    db = _db_ok()
    body = {"status": "ok" if db else "degraded", "db": "ok" if db else "down",
            "version": settings.api_version}
    return JSONResponse(status_code=200 if db else 503, content=body)


@router.get("/health/deep")
async def health_deep() -> dict:
    db = _db_ok()
    storage = await get_storage().health()
    llm = await get_llm_client().ping()
    return {"db": db, "storage": storage, "llm": llm}
