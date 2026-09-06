"""Datasets service (§6.3): raw CSVs -> canonical standardized datasets ->
Storage processed/ -> dataset_versions rows. Pure transformation logic; the
data_prep agent orchestrates it."""
from __future__ import annotations

import hashlib
import io
import re
from datetime import datetime

import pandas as pd

from app.models import DatasetVersion
from app.services.audit import record_event

CANONICAL_COLUMNS: dict[str, list[str]] = {
    "claims": ["claim_id", "policy_id", "product", "segment", "region", "claim_type",
               "event_date", "report_date", "status", "incurred_amount"],
    "premium": ["policy_id", "product", "segment", "region", "period",
                "written_premium", "earned_premium"],
    "exposure": ["policy_id", "product", "segment", "region", "period",
                 "active_policies", "earned_exposure_units"],
}

COLUMN_TYPES: dict[str, str] = {
    "incurred_amount": "money", "written_premium": "money", "earned_premium": "money",
    "active_policies": "int", "earned_exposure_units": "float",
    "event_date": "date", "report_date": "date", "period": "period",
}

CATEGORICAL = ("product", "segment", "region", "claim_type", "status")

# canonical column -> what breaks without it (§13.2 blocker must name metrics)
AFFECTED_METRICS: dict[str, list[str]] = {
    "incurred_amount": ["loss_ratio", "claim_severity", "deterioration_contribution"],
    "earned_premium": ["loss_ratio", "ave_variance"],
    "written_premium": ["premium_reporting"],
    "earned_exposure_units": ["claim_frequency"],
    "active_policies": ["volume_checks"],
    "event_date": ["period_coverage", "frequency_trends"],
    "report_date": ["reporting_lag"],
    "policy_id": ["enrichment_joins", "frequency"],
    "claim_id": ["deduplication", "key_collision_checks"],
    "period": ["period_filters", "trend_series"],
}

REJECTION_WARN_RATE = 0.01  # >1% rejected values -> warning

_MONEY_STRIP = re.compile(r"[₹]|rs\.?|inr|[,\s]", re.IGNORECASE)
_MONEY_KEEP = re.compile(r"^-?\d+(\.\d+)?$")
_PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%d %b %Y")


def parse_money(raw: str) -> tuple[float | None, str]:
    """Returns (value, state) with state in ok|missing|rejected."""
    s = (raw or "").strip()
    if s == "":
        return None, "missing"
    cleaned = _MONEY_STRIP.sub("", s)
    if not _MONEY_KEEP.match(cleaned):
        return None, "rejected"  # ambiguous: "N/A", "1.2.3", "tbd", ...
    return float(cleaned), "ok"


def parse_number(raw: str, as_int: bool = False) -> tuple[float | None, str]:
    v, state = parse_money(raw)
    if state != "ok":
        return v, state
    return (float(int(round(v))), "ok") if as_int else (v, "ok")


def parse_date(raw: str) -> tuple[str | None, str]:
    s = (raw or "").strip()
    if s == "":
        return None, "missing"
    for fmt in _DATE_FORMATS:
        try:
            d = datetime.strptime(s, fmt).date()
            return d.isoformat(), "ok"
        except ValueError:
            continue
    return None, "rejected"


def parse_period(raw: str) -> tuple[str | None, str]:
    s = (raw or "").strip()
    if s == "":
        return None, "missing"
    if _PERIOD_RE.match(s):
        return s, "ok"
    d, state = parse_date(s)
    if state == "ok":
        return d[:7], "ok"
    return None, "rejected"


def _standardize_categorical(v: str) -> str:
    return re.sub(r"\s+", " ", (v or "").strip()).title()


def read_csv_bytes(data: bytes) -> pd.DataFrame:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    return pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)


def convert_column(series: pd.Series, spec: str) -> tuple[pd.Series, dict]:
    """Convert one column per type spec. Returns (series, stats)."""
    parser = {"money": parse_money, "int": lambda v: parse_number(v, as_int=True),
              "float": parse_number, "date": parse_date, "period": parse_period}[spec]
    out: list = []
    stats = {"column": series.name, "converted": 0, "missing": 0,
             "rejected": 0, "rejected_samples": []}
    for v in series:
        val, state = parser(v)
        out.append(val)
        if state == "ok":
            stats["converted"] += 1
        elif state == "missing":
            stats["missing"] += 1
        else:
            stats["rejected"] += 1
            if len(stats["rejected_samples"]) < 5:
                stats["rejected_samples"].append(str(v)[:40])
    return pd.Series(out, index=series.index, name=series.name), stats


def find_key_collisions(df: pd.DataFrame, key_cols: list[str],
                        value_cols: list[str]) -> dict:
    if df.empty or any(c not in df.columns for c in key_cols + value_cols):
        return {"count": 0, "samples": []}
    sub = df[key_cols + value_cols].drop_duplicates()
    dup_keys = sub.duplicated(subset=key_cols, keep=False)
    collisions = sub[dup_keys]
    samples = []
    for key, grp in list(collisions.groupby(key_cols))[:3]:
        samples.append({"key": list(key) if isinstance(key, tuple) else [key],
                        "variants": grp[value_cols].to_dict("records")})
    return {"count": int(dup_keys.sum()), "samples": samples}


def standardize_frame(
    df_raw: pd.DataFrame,
    kind: str,
    raw_to_canonical: dict[str, str],
    ignored: list[str],
) -> tuple[pd.DataFrame, dict, list[str]]:
    """Returns (standardized_df, transform_log_entries, missing_required_columns)."""
    log: dict = {
        "column_map": {raw: raw_to_canonical.get(raw) for raw in df_raw.columns},
        "type_conversions": [], "categorical_standardized": [],
        "exact_duplicates_removed": {"count": 0, "rule": "pre_approved_validated_rule"},
    }
    df = df_raw.rename(columns=raw_to_canonical)
    if ignored:
        log["ignored_columns"] = list(ignored)

    missing_required = [c for c in CANONICAL_COLUMNS[kind] if c not in df.columns]

    for col in list(df.columns):
        spec = COLUMN_TYPES.get(col)
        if spec:
            df[col], stats = convert_column(df[col], spec)
            log["type_conversions"].append(stats)
        elif col in CATEGORICAL:
            mapped = df[col].map(_standardize_categorical)
            changed = int((mapped != df[col]).sum())
            if changed:
                log["categorical_standardized"].append({"column": col, "changed": changed})
            df[col] = mapped

    before = len(df)
    df = df.drop_duplicates(keep="first").reset_index(drop=True)
    log["exact_duplicates_removed"]["count"] = before - len(df)

    # order: canonical first, then passthrough (unmapped) columns by raw name
    tail = [c for c in df.columns if c not in CANONICAL_COLUMNS[kind]]
    ordered = [c for c in CANONICAL_COLUMNS[kind] if c in df.columns] + sorted(tail)
    df = df[ordered]
    return df, log, missing_required


def enrich_claims_region(claims: pd.DataFrame, exposure: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Fill missing claim region from the policy (exposure) record — green fix."""
    filled = 0
    samples: list[str] = []
    if exposure.empty or "policy_id" not in exposure.columns:
        return claims, {"filled": 0, "sample_claim_ids": []}
    region_by_policy = (
        exposure[exposure["region"].astype(str).str.strip() != ""]
        .drop_duplicates("policy_id").set_index("policy_id")["region"].to_dict()
    )
    if "region" not in claims.columns:
        claims = claims.assign(region="")
    mask = claims["region"].astype(str).str.strip() == ""
    if mask.any() and region_by_policy:
        for idx, row in claims[mask].iterrows():
            region = region_by_policy.get(row["policy_id"])
            if region:
                claims.at[idx, "region"] = region
                filled += 1
                if len(samples) < 5:
                    samples.append(str(row["claim_id"]))
    return claims, {"filled": filled, "sample_claim_ids": samples}


async def write_processed(
    session, wf, storage, kind: str, df: pd.DataFrame,
    source_file_ids: list, extra_log: dict | None = None,
) -> DatasetVersion:
    """Upload processed CSV + create dataset_version row. Returns the row."""
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    data = buf.getvalue().encode()
    path = f"workflows/{wf.id}/processed/{kind}.csv"
    await storage.upload_bytes(path, data)

    log = extra_log or {}
    dv = DatasetVersion(
        workflow_id=wf.id, kind=kind, source_file_ids=source_file_ids,
        storage_path=path, row_count=len(df),
        column_map=log.get("column_map", {}), transform_log=log,
        checksum=hashlib.sha256(data).hexdigest(),
    )
    session.add(dv)
    session.flush()
    return dv


def audit_dataset(session, wf, kind: str, dv: DatasetVersion, log: dict,
                  warnings: list[str]) -> None:
    record_event(
        session, workflow_id=wf.id, actor_type="agent", actor="data_prep_agent",
        action="dataset_standardized", entity_type="dataset_version", entity_id=dv.id,
        summary=(f"{kind}: {dv.row_count} rows, "
                 f"{log.get('exact_duplicates_removed', {}).get('count', 0)} dupes removed"),
        details={"transform_log": log, "warnings": warnings},
    )
