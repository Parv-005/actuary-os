"""In-memory token-bucket rate limiting (§19): 60 req/min/IP general,
10 uploads/min, 5 workflow-starts/min (LLM cost guard)."""
import time

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.config import settings

DEFAULT_LIMITS: dict[str, tuple[int, float]] = {
    "default": (60, 60.0),
    # polled reads (§10: 2s status polling per open tab) get headroom;
    # the LLM-cost-guard buckets below stay strict
    "reads": (300, 60.0),
    "uploads": (10, 60.0),
    "starts": (5, 60.0),
}


class _Bucket:
    __slots__ = ("capacity", "tokens", "ts")

    def __init__(self, capacity: int, per_seconds: float) -> None:
        self.capacity = capacity
        self.tokens = float(capacity)
        self.ts = time.monotonic()

    def take(self, rate_per_sec: float) -> bool:
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.ts) * rate_per_sec)
        self.ts = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, limits: dict[str, tuple[int, float]] | None = None,
                 enabled: bool | None = None) -> None:
        super().__init__(app)
        self.limits = limits or DEFAULT_LIMITS
        self.enabled = settings.rate_limit_enabled if enabled is None else enabled
        self.buckets: dict[tuple[str, str], _Bucket] = {}

    @staticmethod
    def bucket_name(path: str, method: str) -> str:
        if path.endswith("/upload"):
            return "uploads"
        if path.endswith("/start") or (path.rstrip("/") == "/workflows" and method == "POST"):
            return "starts"
        if method == "GET":
            return "reads"
        return "default"

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint):
        if not self.enabled:
            return await call_next(request)
        ip = request.client.host if request.client else "?"
        name = self.bucket_name(request.url.path, request.method)
        capacity, per_seconds = self.limits.get(name, self.limits["default"])
        bucket = self.buckets.setdefault((ip, name), _Bucket(capacity, per_seconds))
        if not bucket.take(capacity / per_seconds):
            return JSONResponse(
                status_code=429,
                content={"detail": "rate limit exceeded", "code": "rate_limited"},
            )
        return await call_next(request)
