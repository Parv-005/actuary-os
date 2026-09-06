"""QA number verification (§6.9): every numeral in LLM prose must match a
bundle value (or rounded form) within tolerance — the anti-hallucination
guard. Pure functions; the pool comes from the deterministic numbers
bundle (services/reports.py), so prose may only cite provided values.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# percent-scale absolute tolerance (±0.05pp); money uses relative tolerance
TOLERANCE_PP = 0.05
TOLERANCE_MONEY_REL = 0.005
_EPS = 1e-9

_DATE_RE = re.compile(r"\b\d{4}[-/]\d{1,2}([-/]\d{1,2})?\b")
_VERSION_RE = re.compile(r"\bv\d+\.\d+(?:\.\d+)*", re.IGNORECASE)
_CP_RE = re.compile(r"\bCP-\d+\b", re.IGNORECASE)
_MONEY_RE = re.compile(
    r"(?:₹|Rs\.?|INR)\s*([\d,]+(?:\.\d+)?)\s*"
    r"(crores?|cr?s?|lakhs?|L|millions?|mn|M|K)?",
    re.IGNORECASE,
)
_PERCENT_RE = re.compile(r"([+-]?\d[\d,]*\.?\d*)\s*(%|pp|percent)")
_PLAIN_RE = re.compile(r"[+-]?\d[\d,]*\.?\d*")

_SCALE = {"crore": 10_000_000, "crores": 10_000_000, "cr": 10_000_000,
          "crs": 10_000_000, "lakh": 100_000, "lakhs": 100_000, "l": 100_000,
          "million": 1_000_000, "millions": 1_000_000, "mn": 1_000_000,
          "m": 1_000_000, "k": 1_000}


@dataclass
class Numeral:
    raw: str
    value: float
    kind: str  # percent | money | plain


def _parse_num(text: str) -> float | None:
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


def extract_numerals(text: str) -> list[Numeral]:
    """Numerals in prose, minus dates (2026-09), versions (v3.1) and CP refs.
    Four-digit years (1900-2100) are skipped — they are labels, not values.
    """
    t = _DATE_RE.sub(" ", text)
    t = _VERSION_RE.sub(" ", t)
    t = _CP_RE.sub(" ", t)
    out: list[Numeral] = []

    def _blank(m: re.Match) -> str:
        return " " * (m.end() - m.start())

    t_money = _MONEY_RE.sub(_blank, t)
    for m in _MONEY_RE.finditer(t):
        amount = _parse_num(m.group(1))
        if amount is None:
            continue
        suffix = (m.group(2) or "").lower()
        out.append(Numeral(raw=m.group(0).strip(), value=amount * _SCALE.get(
            suffix, 1), kind="money"))
    t_pct = _PERCENT_RE.sub(_blank, t_money)
    for m in _PERCENT_RE.finditer(t_money):
        v = _parse_num(m.group(1))
        if v is not None:
            out.append(Numeral(raw=m.group(0).strip(), value=v,
                               kind="percent"))
    for m in _PLAIN_RE.finditer(t_pct):
        v = _parse_num(m.group(0))
        if v is None:
            continue
        if v.is_integer() and 1900 <= int(v) <= 2100:
            continue  # a year, not a value
        out.append(Numeral(raw=m.group(0).strip(), value=v, kind="plain"))
    return out


def _matches(numeral: Numeral, candidate: float) -> bool:
    if numeral.kind == "money":
        if candidate == 0:
            return numeral.value == 0
        return abs(numeral.value - candidate) / abs(candidate) \
            <= TOLERANCE_MONEY_REL + _EPS
    return abs(numeral.value - candidate) <= TOLERANCE_PP + _EPS


def verify_prose(texts: list[str], pool: list[float]) -> list[dict]:
    """Every numeral must match pool raw or ×100 (ratio displayed as %).

    Returns mismatch dicts; empty means consistent. Each mismatch names the
    nearest pool value for the exact-mismatch detail (§16 example).
    """
    pool = [float(v) for v in pool]
    mismatches = []
    for text in texts:
        for num in extract_numerals(text):
            candidates = [(c, abs(num.value - c)) for c in pool]
            candidates += [(c * 100, abs(num.value - c * 100)) for c in pool]
            if any(_matches(num, c) for c, _ in candidates):
                continue
            nearest, dist = min(candidates, key=lambda t: t[1])
            mismatches.append({
                "prose": num.raw,
                "nearest": round(nearest, 4),
                "diff": round(dist, 4),
                "message": f"report \"{num.raw}\" matches no bundle value "
                           f"(nearest {round(nearest, 4)}, diff "
                           f"{round(dist, 4)})",
            })
    return mismatches
