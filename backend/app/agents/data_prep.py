"""Data Prep Agent (§6.3, deterministic): raw -> canonical datasets.

- column mapping (alias table + overrides; fuzzy logged; unmapped -> CP-3 yellow)
- safe type parsing (rupee/commas; ambiguous values rejected individually)
- exact full-row duplicates auto-removed per pre-approved validated rule
- key collisions never merged — flagged for validation (Phase 8) to confirm red
- claims enriched: missing region filled via policy (exposure) join — green
- required canonical column unresolvable -> red blocker naming affected metrics
"""
from __future__ import annotations

from sqlalchemy import select

from app.agents.base import StageResult
from app.agents.context import WorkflowContext
from app.mappings import resolve_columns
from app.models import File
from app.orchestrator import states
from app.services import checkpoints
from app.services.audit import record_event
from app.services.datasets import (
    AFFECTED_METRICS,
    CANONICAL_COLUMNS,
    REJECTION_WARN_RATE,
    audit_dataset,
    enrich_claims_region,
    find_key_collisions,
    read_csv_bytes,
    standardize_frame,
    write_processed,
)

PROCESS_ORDER = ("premium", "exposure", "claims")
COLLISION_KEYS = {
    "claims": (["claim_id"], ["incurred_amount"]),
    "premium": (["policy_id", "period"], ["written_premium", "earned_premium"]),
    "exposure": (["policy_id", "period"], ["active_policies", "earned_exposure_units"]),
}


def _primary_files(session, workflow_id) -> dict[str, File]:
    files = session.execute(
        select(File).where(File.workflow_id == workflow_id)
    ).scalars().all()
    by_kind: dict[str, File] = {}
    for f in files:
        if f.role == "primary" and f.kind in CANONICAL_COLUMNS:
            by_kind.setdefault(f.kind, f)
    return by_kind


async def run_data_prep(ctx: WorkflowContext) -> StageResult:
    session, wf = ctx.session, ctx.workflow
    overrides = (wf.config or {}).get("column_overrides", {})
    ignored = (wf.config or {}).get("ignored_columns", [])
    primaries = _primary_files(session, wf.id)

    missing_files = [k for k in CANONICAL_COLUMNS if k not in primaries]
    if missing_files:
        cp = checkpoints.raise_checkpoint(
            session, wf, checkpoint_type="validation_blocker", severity="red",
            blocking=True,
            title=f"No primary file for: {', '.join(missing_files)}",
            context={"stage": "data_prep", "missing_kinds": missing_files},
            options=[{"decision": "accept_exception", "label": "Accept exception"},
                     {"decision": "request_rerun", "label": "Re-run data prep"},
                     {"decision": "reject_data", "label": "Reject data"}],
        )
        states.apply_transition(session, wf, states.BLOCKED, actor_type="agent",
                                actor="data_prep_agent", reason="missing primary file")
        return StageResult(status="BLOCKER", checkpoint_id=str(cp.id))

    datasets_out: list[dict] = []
    unmapped_all: list[dict] = []
    warnings: list[str] = []
    exposure_df = None
    final_columns: dict[str, set] = {}

    for kind in PROCESS_ORDER:
        f = primaries[kind]
        raw = read_csv_bytes(await ctx.storage.download_bytes(f.storage_path))
        mapping = resolve_columns(list(raw.columns), kind, overrides, ignored)
        df, log, missing_required = standardize_frame(
            raw, kind, mapping.raw_to_canonical, mapping.ignored
        )
        for r_col, can in mapping.fuzzy.items():
            log.setdefault("fuzzy_mapped", {})[r_col] = can
        if mapping.unmapped:
            for col, sugg in mapping.unmapped.items():
                unmapped_all.append({"column": col, "kind": kind, "suggestions": sugg})
            log["unmapped_columns"] = mapping.unmapped

        # conversion rejection-rate warning (>1%)
        for tc in log["type_conversions"]:
            total = tc["converted"] + tc["rejected"]
            if total and tc["rejected"] / total > REJECTION_WARN_RATE:
                warnings.append(f"{kind}.{tc['column']}: "
                                f"{tc['rejected']}/{total} values rejected (>1%)")

        # key collisions: never merged — flagged for validation (§13.2)
        key_cols, value_cols = COLLISION_KEYS[kind]
        if not missing_required:
            collisions = find_key_collisions(df, key_cols, value_cols)
            log["key_collisions"] = {"keys": key_cols, **collisions}

        if kind == "claims" and exposure_df is not None and "region" not in missing_required:
            df, fix = enrich_claims_region(df, exposure_df)
            log["region_fix"] = fix

        source_ids = [f.id]
        dv = await write_processed(session, wf, ctx.storage, kind, df, source_ids, log)
        audit_dataset(session, wf, kind, dv, log, warnings)
        datasets_out.append({"kind": kind, "dataset_version_id": str(dv.id),
                             "row_count": dv.row_count, "storage_path": dv.storage_path})
        final_columns[kind] = set(df.columns)
        if kind == "exposure":
            exposure_df = df

    # Required canonical column unresolvable -> red blocker naming metrics (§13.2)
    blockers = _missing_required(final_columns)
    if blockers:
        affected = sorted({m for col in blockers for m in AFFECTED_METRICS.get(col, [])})
        cp = checkpoints.raise_checkpoint(
            session, wf, checkpoint_type="validation_blocker", severity="red",
            blocking=True,
            title=(f"Required column(s) unresolvable: {', '.join(blockers)} — "
                   f"blocks {', '.join(affected)}"),
            context={"stage": "data_prep", "missing_columns": blockers,
                     "affected_metrics": affected},
            options=[{"decision": "accept_exception", "label": "Accept exception"},
                     {"decision": "request_rerun", "label": "Re-run data prep"},
                     {"decision": "reject_data", "label": "Reject data"}],
        )
        states.apply_transition(session, wf, states.BLOCKED, actor_type="agent",
                                actor="data_prep_agent",
                                reason=f"required columns unresolvable: {blockers}")
        return StageResult(status="BLOCKER", checkpoint_id=str(cp.id),
                           outputs={"missing_columns": blockers})

    if unmapped_all:
        options = [{"decision": "confirm_mapping",
                    "label": f"Map '{u['column']}' to '{u['suggestions'][0]}'"
                    if u["suggestions"] else f"Confirm mapping for '{u['column']}'",
                    "payload": {"column": u["column"], "kind": u["kind"],
                                "canonical": u["suggestions"][0] if u["suggestions"] else None}}
                   for u in unmapped_all]
        options.append({"decision": "ignore_column", "label": "Ignore (keep as unmapped)",
                        "payload": {"columns": [u["column"] for u in unmapped_all]}})
        checkpoints.raise_checkpoint(
            session, wf, checkpoint_type="schema_mapping", severity="yellow",
            blocking=False,
            title=f"Unmapped field(s): {', '.join(u['column'] for u in unmapped_all)}",
            context={"unmapped": unmapped_all}, options=options,
        )

    status = "WARNING" if warnings else "PASS"
    record_event(
        session, workflow_id=wf.id, actor_type="agent", actor="data_prep_agent",
        action="data_prep_complete", entity_type="workflow", entity_id=wf.id,
        summary=f"processed {len(datasets_out)} datasets, {len(warnings)} warnings",
        details={"datasets": datasets_out, "warnings": warnings},
    )
    return StageResult(status=status, outputs={"datasets": datasets_out,
                                               "warnings": warnings})


def _missing_required(final_columns: dict[str, set]) -> list[str]:
    """Canonical columns absent from the actual processed datasets."""
    missing: set[str] = set()
    for kind, canons in CANONICAL_COLUMNS.items():
        have = final_columns.get(kind, set())
        missing.update(c for c in canons if c not in have)
    return sorted(missing)
