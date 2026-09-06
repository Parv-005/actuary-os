"""Time helpers — all timestamps are timezone-aware UTC."""
from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime | None = None) -> str:
    return (dt or utcnow()).isoformat()
