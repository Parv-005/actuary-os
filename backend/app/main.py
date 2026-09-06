"""Vortex ActuaryOS — FastAPI app: CORS (locked), rate limiting, routers,
startup resume sweep (§7.2: sweep runs at app startup and on every status poll)."""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth.ratelimit import RateLimitMiddleware
from app.config import settings
from app.routers import decisions, health, validation, workflows
from app.utils.logging import json_log


@asynccontextmanager
async def lifespan(app: FastAPI):
    json_log("startup", env=settings.app_env, version=settings.api_version)
    try:
        from app.orchestrator.engine import resume_stale_workflows

        await resume_stale_workflows()
    except ImportError:
        json_log("resume_sweep", detail="orchestrator not wired yet (Phase 10)")
    except Exception as e:  # never block startup on sweep failure
        json_log("resume_sweep_error", error=str(e)[:300])
    yield
    json_log("shutdown")


def create_app() -> FastAPI:
    app = FastAPI(title="Vortex ActuaryOS", version=settings.api_version, lifespan=lifespan)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    app.include_router(workflows.router)
    app.include_router(validation.router)
    app.include_router(decisions.router)
    return app


app = create_app()
