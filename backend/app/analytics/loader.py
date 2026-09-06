"""Processed-dataset loader (§5): Storage -> DataFrame with a checksum-keyed
cache so repeated reads within/across requests never re-parse CSVs."""
from __future__ import annotations

import pandas as pd

from app.services.datasets import read_csv_bytes

_cache: dict[tuple[str, str], pd.DataFrame] = {}


async def load_processed(storage, path: str, checksum: str = "") -> pd.DataFrame:
    """Download + parse a processed CSV. Returns a copy (callers may mutate)."""
    if checksum:
        hit = _cache.get((path, checksum))
        if hit is not None:
            return hit.copy()
    data = await storage.download_bytes(path)
    df = read_csv_bytes(data)
    if checksum:
        _cache[(path, checksum)] = df
    return df.copy()


def clear_cache() -> None:
    _cache.clear()
