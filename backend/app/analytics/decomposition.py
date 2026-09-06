"""OAT contribution decomposition (§6.5): pure functions — no DB, no LLM.

contribution_i = (LR_i,cur - LR_i,prev) x premium_weight_i,prev,
normalized to % of total portfolio movement.
"""
from __future__ import annotations

ZERO_MOVEMENT_EPS = 1e-12


def oat_contributions(
    cur_lr: dict[tuple, float | None],
    prev_lr: dict[tuple, float | None],
    weights_prev: dict[tuple, float] | None,
    total_movement: float | None,
) -> dict[tuple, float]:
    """Returns {cell: contribution_pct} for cells with complete inputs.

    Empty dict when there is nothing to decompose (no prior, no movement,
    or no weights) — the caller emits an `unavailable` metric row instead.
    Cells missing cur/prev LR are skipped (never zero-filled: that would
    fabricate a movement).
    """
    if total_movement is None or abs(total_movement) < ZERO_MOVEMENT_EPS:
        return {}
    if not weights_prev:
        return {}
    out: dict[tuple, float] = {}
    for cell, lr_c in cur_lr.items():
        lr_p = prev_lr.get(cell) if prev_lr else None
        w = weights_prev.get(cell)
        if lr_c is None or lr_p is None or w is None:
            continue
        out[cell] = (lr_c - lr_p) * w / total_movement * 100.0
    return out
