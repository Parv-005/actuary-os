"""Storage abstraction (§4/§19): Supabase Storage backend + local/memory fallbacks
for offline dev and tests. Zero local-filesystem dependence in production."""
from __future__ import annotations

import httpx

from app.config import settings


class StorageError(Exception):
    pass


class BaseStorage:
    async def upload_bytes(self, path: str, data: bytes, content_type: str = "text/csv") -> None:
        raise NotImplementedError

    async def download_bytes(self, path: str) -> bytes:
        raise NotImplementedError

    async def signed_url(self, path: str, expires_s: int = 3600) -> str:
        raise NotImplementedError

    async def health(self) -> bool:
        probe = "_health/probe.txt"
        try:
            await self.upload_bytes(probe, b"ok", "text/plain")
            return (await self.download_bytes(probe)) == b"ok"
        except Exception:
            return False


class SupabaseStorage(BaseStorage):
    """Thin httpx wrapper over Supabase Storage REST — no heavy SDK chain."""

    def __init__(self, base_url: str, service_key: str, bucket: str) -> None:
        self.base = base_url.rstrip("/")
        self.bucket = bucket
        self._http = httpx.AsyncClient(
            base_url=self.base,
            headers={"Authorization": f"Bearer {service_key}", "apikey": service_key},
            timeout=30,
        )
        self._bucket_checked = False

    async def _ensure_bucket(self) -> None:
        if self._bucket_checked:
            return
        r = await self._http.post("/storage/v1/bucket", json={"name": self.bucket, "public": False})
        if r.status_code not in (200, 201, 400, 409):  # 400/409 = already exists
            raise StorageError(f"bucket create failed: {r.status_code} {r.text[:200]}")
        self._bucket_checked = True

    async def upload_bytes(self, path: str, data: bytes, content_type: str = "text/csv") -> None:
        await self._ensure_bucket()
        r = await self._http.post(
            f"/storage/v1/object/{self.bucket}/{path}",
            content=data,
            headers={"Content-Type": content_type, "x-upsert": "true"},
        )
        if r.status_code not in (200, 201):
            raise StorageError(f"upload failed: {r.status_code} {r.text[:200]}")

    async def download_bytes(self, path: str) -> bytes:
        r = await self._http.get(f"/storage/v1/object/{self.bucket}/{path}")
        if r.status_code != 200:
            raise StorageError(f"download failed: {r.status_code}")
        return r.content

    async def signed_url(self, path: str, expires_s: int = 3600) -> str:
        r = await self._http.post(
            f"/storage/v1/object/sign/{self.bucket}/{path}", json={"expiresIn": expires_s}
        )
        if r.status_code != 200:
            raise StorageError(f"sign failed: {r.status_code}")
        signed = r.json().get("signedURL") or r.json().get("signedUrl")
        return f"{self.base}/storage/v1{signed}"


class LocalStorage(BaseStorage):
    """Filesystem backend for offline dev (bucket = directory)."""

    def __init__(self, root: str, bucket: str) -> None:
        from pathlib import Path

        self.dir = Path(root) / bucket
        self.dir.mkdir(parents=True, exist_ok=True)

    def _p(self, path: str):
        p = (self.dir / path).resolve()
        if not str(p).startswith(str(self.dir.resolve())):
            raise StorageError("path traversal")
        return p

    async def upload_bytes(self, path: str, data: bytes, content_type: str = "text/csv") -> None:
        p = self._p(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    async def download_bytes(self, path: str) -> bytes:
        p = self._p(path)
        if not p.exists():
            raise StorageError(f"not found: {path}")
        return p.read_bytes()

    async def signed_url(self, path: str, expires_s: int = 3600) -> str:
        return f"local://{self.bucket}/{path}"


class MemoryStorage(BaseStorage):
    """In-memory backend for tests."""

    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}

    async def upload_bytes(self, path: str, data: bytes, content_type: str = "text/csv") -> None:
        self.data[path] = data

    async def download_bytes(self, path: str) -> bytes:
        if path not in self.data:
            raise StorageError(f"not found: {path}")
        return self.data[path]

    async def signed_url(self, path: str, expires_s: int = 3600) -> str:
        return f"memory://{path}"


def get_storage() -> BaseStorage:
    backend = settings.storage_backend
    if backend == "supabase":
        return SupabaseStorage(
            settings.supabase_url, settings.supabase_service_key, settings.supabase_bucket
        )
    if backend == "local":
        return LocalStorage(settings.storage_local_root, settings.supabase_bucket)
    if backend == "memory":
        return MemoryStorage()
    raise StorageError(f"unknown storage backend: {backend}")
