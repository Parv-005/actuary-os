"""UUID helpers."""
import uuid


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()


def uuid_str(value: uuid.UUID | str | None) -> str | None:
    return str(value) if value is not None else None
