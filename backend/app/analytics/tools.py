"""Deterministic insight tools (§9): @tool registry over an in-memory snapshot.

Pure functions — no DB, no LLM inside. The insight agent loads metric +
validation rows once, binds them into a snapshot, and hands the LLM an
executor closure. Errors are returned as {error, reason} values, never
raised. Every result row carries the stored metric id so findings can cite
evidence_ids that resolve to real rows.

NOTE on §9 coverage: search_knowledge_base is invoked directly by the
knowledge agent via app.services.knowledge (single deterministic call, no
LLM tool loop per §6.7), so it lives in services, not this LLM registry.
generate_chart_spec belongs to the reporting agent (Phase 12).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

DIM_KEYS = ("product", "segment", "region")
KNOWN_METRICS = ("loss_ratio", "claim_frequency", "claim_severity",
                 "ave_variance", "deterioration_contribution")
MAX_TOOL_CALLS = 6  # §6.6: the insight agent calls tools at most 6 times


@dataclass
class ToolDef:
    name: str
    description: str
    parameters: dict
    func: Callable[[dict, dict], dict]


TOOL_REGISTRY: dict[str, ToolDef] = {}


def tool(name: str, description: str, parameters: dict):
    """Register a deterministic tool. Func signature: fn(args, snapshot)."""

    def _wrap(func: Callable[[dict, dict], dict]) -> Callable[[dict, dict], dict]:
        TOOL_REGISTRY[name] = ToolDef(name=name, description=description,
                                      parameters=parameters, func=func)
        return func

    return _wrap


def openai_tool_schemas() -> list[dict]:
    return [{"type": "function",
             "function": {"name": t.name, "description": t.description,
                          "parameters": t.parameters}}
            for t in TOOL_REGISTRY.values()]


def _row(m: dict) -> dict:
    """Lean metric projection for tool output (numbers verbatim + id)."""
    return {"metric_id": m["id"], "metric_key": m["metric_key"],
            "dimensions": m["dimensions"], "period": m["period"],
            "value": m["value"], "prev_value": m["prev_value"],
            "delta_pp": m["delta_pp"], "unit": m["unit"],
            "flags": m.get("flags", {})}


def _dims_from_args(args: dict) -> dict:
    return {k: args[k] for k in DIM_KEYS if args.get(k)}


def _find(metrics: list[dict], key: str, dims: dict) -> dict | None:
    for m in metrics:
        if m["metric_key"] == key and (m["dimensions"] or {}) == dims:
            return m
    return None


def _err(code: str, reason: str) -> dict:
    return {"error": code, "reason": reason}


@tool("portfolio_summary", "Portfolio-level loss ratio, frequency, severity "
      "and actual-vs-expected variance with metric ids.",
      {"type": "object", "properties": {}})
def _portfolio_summary(args: dict, snap: dict) -> dict:
    out = {}
    for key in ("loss_ratio", "claim_frequency", "claim_severity",
                "ave_variance"):
        m = _find(snap["metrics"], key, {})
        out[key] = _row(m) if m and m["value"] is not None else None
    out["period"] = snap["period"]
    return out


@tool("compare_periods", "Current vs prior value and delta for one metric cell.",
      {"type": "object",
       "properties": {"metric": {"type": "string"},
                      "product": {"type": "string"},
                      "segment": {"type": "string"},
                      "region": {"type": "string"}},
       "required": ["metric"]})
def _compare_periods(args: dict, snap: dict) -> dict:
    if args.get("metric") not in KNOWN_METRICS:
        return _err("unknown_metric",
                    f"metric must be one of {list(KNOWN_METRICS)}")
    m = _find(snap["metrics"], args["metric"], _dims_from_args(args))
    if m is None:
        return _err("no_such_cell", "no stored metric for those dimensions")
    return {"cur": m["value"], "prev": m["prev_value"],
            "delta_pp": m["delta_pp"], "metric_id": m["id"],
            "undefined_reason": m["undefined_reason"]}


@tool("segment_breakdown", "Metric rows at a dimension level, ranked by value.",
      {"type": "object",
       "properties": {"metric": {"type": "string"},
                      "group_by": {"type": "array", "items": {"type": "string"}},
                      "product": {"type": "string"}},
       "required": ["metric", "group_by"]})
def _segment_breakdown(args: dict, snap: dict) -> dict:
    if args.get("metric") not in KNOWN_METRICS:
        return _err("unknown_metric",
                    f"metric must be one of {list(KNOWN_METRICS)}")
    group_by = args.get("group_by") or []
    if not group_by or any(g not in DIM_KEYS for g in group_by):
        return _err("invalid_dim", "group_by must list product/segment/region")
    rows = []
    for m in snap["metrics"]:
        if m["metric_key"] != args["metric"] or m["value"] is None:
            continue
        dims = m["dimensions"] or {}
        if set(dims) != set(group_by):
            continue
        if args.get("product") and dims.get("product") != args["product"]:
            continue
        rows.append(_row(m))
    rows.sort(key=lambda r: (r["value"] is None, -(r["value"] or 0)))
    return {"rows": rows}


@tool("region_breakdown", "Loss-ratio rows by region, optionally for one product.",
      {"type": "object",
       "properties": {"metric": {"type": "string"},
                      "product": {"type": "string"}},
       "required": ["metric"]})
def _region_breakdown(args: dict, snap: dict) -> dict:
    return _segment_breakdown({"metric": args.get("metric"),
                               "group_by": ["region"],
                               "product": args.get("product")}, snap)


@tool("drilldown", "Next level down: no filter gives products; product "
      "gives segments; product+segment gives regions.",
      {"type": "object",
       "properties": {"metric": {"type": "string"},
                      "product": {"type": "string"},
                      "segment": {"type": "string"}},
       "required": ["metric"]})
def _drilldown(args: dict, snap: dict) -> dict:
    if args.get("metric") not in KNOWN_METRICS:
        return _err("unknown_metric",
                    f"metric must be one of {list(KNOWN_METRICS)}")
    if not args.get("product"):
        level: list[str] = ["product"]
    elif not args.get("segment"):
        level = ["product", "segment"]
    else:
        level = ["product", "segment", "region"]
    sub = {"metric": args["metric"], "group_by": level}
    if args.get("product"):
        sub["product"] = args["product"]
    res = _segment_breakdown(sub, snap)
    if "rows" in res and args.get("segment") and len(level) == 3:
        res["rows"] = [r for r in res["rows"]
                       if r["dimensions"].get("segment") == args["segment"]]
    return res


@tool("top_contributors", "Largest deterioration contributors with shares.",
      {"type": "object",
       "properties": {"limit": {"type": "integer"}}})
def _top_contributors(args: dict, snap: dict) -> dict:
    rows = [_row(m) for m in snap["metrics"]
            if m["metric_key"] == "deterioration_contribution"
            and m["value"] is not None]
    rows.sort(key=lambda r: -r["value"])
    return {"rows": rows[:args.get("limit", 5) or 5]}


@tool("severity_vs_frequency", "Whether a segment move is severity-driven "
      "or frequency-driven, plus its contribution share.",
      {"type": "object",
       "properties": {"product": {"type": "string"},
                      "segment": {"type": "string"},
                      "region": {"type": "string"}},
       "required": ["product", "segment"]})
def _severity_vs_frequency(args: dict, snap: dict) -> dict:
    dims = _dims_from_args(args)
    sev = _find(snap["metrics"], "claim_severity", dims)
    freq = _find(snap["metrics"], "claim_frequency", dims)
    if sev is None or freq is None:
        return _err("no_such_cell", "no severity/frequency for those dimensions")
    contrib = _find(snap["metrics"], "deterioration_contribution", dims)
    return {"severity_delta": sev["delta_pp"],
            "frequency_delta": freq["delta_pp"],
            "severity_metric_id": sev["id"],
            "frequency_metric_id": freq["id"],
            "contribution_share": contrib["value"] if contrib else None,
            "contribution_metric_id": contrib["id"] if contrib else None}


@tool("claim_outliers", "Flagged large claims from validation with cell context.",
      {"type": "object",
       "properties": {"segment": {"type": "string"}}})
def _claim_outliers(args: dict, snap: dict) -> dict:
    checks = [c for c in snap.get("validation", [])
              if c["check_id"] == "behav_outlier_claims"]
    if not checks:
        return {"flagged": [], "note": "no outlier scan available"}
    det = checks[0].get("details", {})
    flagged = det.get("flagged", [])
    if args.get("segment"):
        flagged = [f for f in flagged
                   if (f.get("cell") or {}).get("segment") == args["segment"]]
    return {"threshold": det.get("threshold"),
            "flagged": flagged[:5],
            "flagged_total": det.get("flagged_total", len(flagged)),
            "note": "unusual but not proven invalid"}


@tool("decompose_contribution", "Contribution shares aggregated to a level.",
      {"type": "object",
       "properties": {"level": {"type": "array",
                                "items": {"type": "string"}}},
       "required": ["level"]})
def _decompose_contribution(args: dict, snap: dict) -> dict:
    level = args.get("level") or []
    if not level or any(g not in DIM_KEYS for g in level):
        return _err("invalid_dim", "level must list product/segment/region")
    agg: dict[tuple, float] = {}
    ids: dict[tuple, list] = {}
    for m in snap["metrics"]:
        if m["metric_key"] != "deterioration_contribution" \
                or m["value"] is None:
            continue
        dims = m["dimensions"] or {}
        if not all(k in dims for k in level):
            continue
        cell = tuple(dims[k] for k in level)
        agg[cell] = agg.get(cell, 0.0) + m["value"]
        ids.setdefault(cell, []).append(m["id"])
    rows = [{"dimensions": dict(zip(level, cell, strict=True)),
             "contribution_share": round(v, 2), "metric_ids": ids[cell]}
            for cell, v in sorted(agg.items(), key=lambda kv: -kv[1])]
    return {"rows": rows,
            "total": round(sum(agg.values()), 2)}


@dataclass
class BoundTools:
    schemas: list[dict] = field(default_factory=list)
    calls: int = 0

    async def executor(self, name: str, args: dict) -> dict:
        self.calls += 1
        if self.calls > MAX_TOOL_CALLS:
            return _err("tool_budget_exceeded",
                        f"max {MAX_TOOL_CALLS} tool calls per investigation")
        tooldef = TOOL_REGISTRY.get(name)
        if tooldef is None:
            return _err("unknown_tool", f"no tool named {name}")
        try:
            return tooldef.func(args or {}, self.snapshot)
        except Exception as e:  # noqa: BLE001 — tools never raise to the LLM
            return _err("tool_failed", str(e)[:300])

    snapshot: dict = field(default_factory=dict)


def bind_tools(metrics: list[dict], validation: list[dict],
               period: str) -> BoundTools:
    """Bind a snapshot; returns schemas + stateful executor (call budget)."""
    bound = BoundTools(schemas=openai_tool_schemas(),
                       snapshot={"metrics": metrics,
                                 "validation": validation,
                                 "period": period})
    return bound
