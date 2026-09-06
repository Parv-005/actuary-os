"""Column mapping: exact alias -> canonical, difflib fuzzy >= 0.85 auto-map,
below threshold -> unmapped (surfaced as CP-3). Human overrides in
workflow.config['column_overrides'] win over everything."""
from __future__ import annotations

import difflib
import json
from dataclasses import dataclass, field
from pathlib import Path

ALIASES_PATH = Path(__file__).resolve().parent / "aliases.json"
FUZZY_THRESHOLD = 0.85
SUGGESTION_THRESHOLD = 0.6

_ALIASES: dict | None = None


def load_aliases() -> dict:
    global _ALIASES
    if _ALIASES is None:
        _ALIASES = json.loads(ALIASES_PATH.read_text())
    return _ALIASES


@dataclass
class ColumnMapping:
    kind: str
    mapped: dict[str, str] = field(default_factory=dict)      # raw -> canonical (exact/override)
    fuzzy: dict[str, str] = field(default_factory=dict)       # raw -> canonical (auto fuzzy)
    unmapped: dict[str, list[str]] = field(default_factory=dict)  # raw -> suggestions
    ignored: list[str] = field(default_factory=list)

    @property
    def raw_to_canonical(self) -> dict[str, str]:
        return {**self.mapped, **self.fuzzy}


def resolve_columns(
    raw_columns: list[str],
    kind: str,
    overrides: dict[str, str] | None = None,
    ignored: list[str] | None = None,
) -> ColumnMapping:
    spec = load_aliases()[kind]
    canonical = spec["canonical"]
    alias_to_canonical: dict[str, str] = {}
    for can, aliases in spec["aliases"].items():
        alias_to_canonical[can.lower()] = can
        for a in aliases:
            alias_to_canonical[a.lower()] = can
    targets = sorted(set(canonical) | set(alias_to_canonical))

    result = ColumnMapping(kind=kind)
    used: set[str] = set()
    for raw in raw_columns:
        low = raw.strip().lower()
        if ignored and raw in ignored:
            result.ignored.append(raw)
            continue
        if overrides and raw in overrides:
            result.mapped[raw] = overrides[raw]
            used.add(overrides[raw])
            continue
        if low in canonical or raw in canonical:
            can = raw if raw in canonical else alias_to_canonical[low]
            if can in used:
                result.unmapped[raw] = [f"{can} already mapped"]
                continue
            result.mapped[raw] = can
            used.add(can)
            continue
        if low in alias_to_canonical:
            can = alias_to_canonical[low]
            if can in used:
                result.unmapped[raw] = [f"{can} already mapped"]
                continue
            result.mapped[raw] = can
            used.add(can)
            continue
        match = difflib.get_close_matches(low, [t.lower() for t in targets],
                                          n=1, cutoff=FUZZY_THRESHOLD)
        if match:
            target_low = match[0]
            can = next((c for c in canonical if c.lower() == target_low),
                       alias_to_canonical.get(target_low, target_low))
            if can in used:
                result.unmapped[raw] = [f"{can} already mapped"]
                continue
            result.fuzzy[raw] = can
            used.add(can)
            continue
        suggestions = difflib.get_close_matches(
            low, [t.lower() for t in targets], n=3, cutoff=SUGGESTION_THRESHOLD
        )
        result.unmapped[raw] = [
            next((c for c in canonical if c.lower() == s), s) for s in suggestions
        ]
    return result
