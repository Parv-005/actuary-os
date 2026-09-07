"use client";

import { useMemo, useState } from "react";
import {
  Badge,
  Button,
  Modal,
  Spinner,
} from "@/components/ui/primitives";
import { useDecision } from "@/hooks/useDecision";
import type { PendingCheckpoint } from "@/lib/api";

const RATIONALE_REQUIRED = new Set(["reject", "override", "accept_exception"]);

/* ------------------------------------------------------------------ */
/* metadata: checkpoint types -> judge-friendly framing                */
/* ------------------------------------------------------------------ */

const TYPE_META: Record<
  string,
  { code: string; icon: string; label: string; explainer: string; tone: "red" | "yellow" | "blue" }
> = {
  input_exception: {
    code: "CP-1",
    icon: "⧉",
    label: "Input exception",
    explainer:
      "Intake refused to guess. Two files claim the same slot, or a required file is missing — you pick the authoritative source.",
    tone: "red",
  },
  validation_blocker: {
    code: "CP-2",
    icon: "☑",
    label: "Validation blocker",
    explainer:
      "A data check failed hard. Accept it as a disclosed exception (it lands in the report), re-run the check, or reject the data.",
    tone: "red",
  },
  schema_mapping: {
    code: "CP-3",
    icon: "⟷",
    label: "Column mapping",
    explainer:
      "Data prep found columns it doesn't recognise. Confirm where they belong, or ignore them — the pipeline re-standardises automatically.",
    tone: "yellow",
  },
  assumption_variance: {
    code: "CP-4",
    icon: "◈",
    label: "Assumption variance",
    explainer:
      "Observed severity moved materially vs the configured trend. The AI never changes an assumption — you decide whether to monitor, review or investigate.",
    tone: "red",
  },
  finding_review: {
    code: "CP-5",
    icon: "◉",
    label: "Finding review",
    explainer:
      "An AI observation needs a second pair of eyes. Accept, monitor, or send it back for one more investigation round.",
    tone: "yellow",
  },
  final_approval: {
    code: "CP-6",
    icon: "§",
    label: "Final approval",
    explainer:
      "QA verified every number in the draft. Nothing is final until you sign it — approve, ask for a revision, or reject.",
    tone: "blue",
  },
  qa_failure: {
    code: "CP-7",
    icon: "⚠",
    label: "QA failure",
    explainer:
      "Number-verify caught a mismatch between the report and the frozen metrics. Request a regeneration or escalate.",
    tone: "red",
  },
};

function typeMeta(type: string) {
  return (
    TYPE_META[type] ?? {
      code: type,
      icon: "•",
      label: type.replaceAll("_", " "),
      explainer: "Your decision is recorded and resumes the pipeline.",
      tone: "yellow" as const,
    }
  );
}

/* ------------------------------------------------------------------ */
/* metadata: decisions -> what they do                                  */
/* ------------------------------------------------------------------ */

const DECISION_META: Record<string, { icon: string; blurb: string; next: string }> = {
  select_file: {
    icon: "✓",
    blurb: "Use this file as the single source of truth.",
    next: "Pipeline re-runs intake with your file as authoritative.",
  },
  accept_exception: {
    icon: "✓",
    blurb: "Accept and disclose — carried into the report exceptions.",
    next: "Validation completes; the exception is cited in the draft.",
  },
  approve: {
    icon: "✓",
    blurb: "Sign off the QA-verified draft.",
    next: "Report locks, workflow completes with full audit trail.",
  },
  no_change_required: {
    icon: "✓",
    blurb: "Assumption stands — keep monitoring.",
    next: "Pipeline continues into reporting.",
  },
  monitor: {
    icon: "◷",
    blurb: "Keep watching, no action now.",
    next: "Recorded as monitoring; pipeline continues.",
  },
  accept: {
    icon: "✓",
    blurb: "Finding stands as written.",
    next: "Recorded; report keeps the finding.",
  },
  confirm_mapping: {
    icon: "⟷",
    blurb: "Apply this column mapping.",
    next: "Datasets re-standardise and downstream re-runs.",
  },
  ignore_column: {
    icon: "–",
    blurb: "Leave the column out of calculations.",
    next: "Pipeline continues without it.",
  },
  request_rerun: {
    icon: "↻",
    blurb: "Re-run the failed stage.",
    next: "Only that stage re-executes — nothing else is lost.",
  },
  request_revision: {
    icon: "✎",
    blurb: "Send the draft back.",
    next: "Reporting regenerates v+1, then QA re-verifies.",
  },
  investigate_further: {
    icon: "🔍",
    blurb: "One bounded extra investigation round.",
    next: "Insight re-runs once with your focus note attached.",
  },
  review_assumption: {
    icon: "◈",
    blurb: "Flag the assumption for review.",
    next: "Recorded with your note; pipeline continues to reporting.",
  },
  reject_data: {
    icon: "✕",
    blurb: "Kill this run — the data can't be trusted.",
    next: "Workflow moves to REJECTED. Start a fresh review.",
  },
  reject: {
    icon: "✕",
    blurb: "Reject this finding or draft.",
    next: "Recorded with your rationale; pipeline parks or closes.",
  },
  override: {
    icon: "⚡",
    blurb: "Override with your judgement.",
    next: "Recorded as overridden with your rationale.",
  },
  escalate: {
    icon: "⇧",
    blurb: "Escalate to a senior reviewer.",
    next: "Stays parked and flagged as escalated.",
  },
  comment: {
    icon: "💬",
    blurb: "Add a note without changing state.",
    next: "Nothing moves — the note joins the audit trail.",
  },
};

function decisionMeta(d: string) {
  return (
    DECISION_META[d] ?? {
      icon: "→",
      blurb: "",
      next: "Your decision is recorded and the pipeline resumes.",
    }
  );
}

function prettyDecision(d: string): string {
  return d.replaceAll("_", " ");
}

/* ------------------------------------------------------------------ */
/* small formatting helpers (no raw JSON dumps)                        */
/* ------------------------------------------------------------------ */

function fmtVal(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (typeof v === "number") {
    if (Number.isInteger(v)) return v.toLocaleString();
    return String(Math.round(v * 100) / 100);
  }
  return String(v);
}

function ProfileTable({ profiles, workflowPeriod }: { profiles: any[]; workflowPeriod?: string }) {
  if (!profiles?.length) return null;
  return (
    <div className="overflow-x-auto rounded-xl ring-1 ring-inset ring-slate-200">
      <table className="data-table min-w-[560px]">
        <thead>
          <tr>
            <th>File</th>
            <th>Rows</th>
            <th>Period</th>
            <th>Checksum</th>
            <th>Verdict</th>
          </tr>
        </thead>
        <tbody>
          {profiles.map((p: any, i: number) => {
            const stale = p.stale ?? (workflowPeriod && p.period_inferred !== workflowPeriod);
            return (
              <tr key={i}>
                <td className="font-mono text-xs font-semibold text-slate-800">{p.filename}</td>
                <td className="tabular-nums">{p.row_count?.toLocaleString?.() ?? fmtVal(p.row_count)}</td>
                <td className="font-mono text-xs">
                  {p.period_inferred ?? "—"}
                  {p.period_inferred && workflowPeriod && p.period_inferred !== workflowPeriod && (
                    <span className="ml-1.5 rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] font-bold text-amber-800">
                      stale
                    </span>
                  )}
                </td>
                <td className="font-mono text-[11px] text-slate-400">{p.checksum ?? "—"}</td>
                <td>
                  {stale ? (
                    <Badge tone="yellow">wrong period?</Badge>
                  ) : (
                    <Badge tone="green">matches {workflowPeriod ?? "period"}</Badge>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function KeyGrid({ obj }: { obj: Record<string, unknown> }) {
  return (
    <dl className="grid gap-2 sm:grid-cols-2">
      {Object.entries(obj).map(([k, v]) => (
        <div key={k} className="rounded-xl bg-white px-3 py-2 ring-1 ring-inset ring-slate-200/70">
          <dt className="text-[11px] font-bold uppercase tracking-wide text-slate-400">
            {k.replaceAll("_", " ")}
          </dt>
          <dd className="mt-0.5 text-sm font-semibold tabular-nums text-slate-800">{fmtVal(v)}</dd>
        </div>
      ))}
    </dl>
  );
}

/* ------------------------------------------------------------------ */
/* per-type evidence rendering                                         */
/* ------------------------------------------------------------------ */

function CheckpointEvidence({ checkpoint }: { checkpoint: PendingCheckpoint }) {
  const ctx = (checkpoint.context ?? {}) as Record<string, any>;
  const type = checkpoint.type;

  if (type === "input_exception") {
    const profiles = ctx.profiles as any[] | undefined;
    return (
      <div className="space-y-3">
        <p className="text-sm leading-relaxed text-slate-600">{typeMeta(type).explainer}</p>
        {ctx.kind && (
          <p className="text-sm">
            <Badge tone="blue">{ctx.kind}</Badge>{" "}
            <span className="text-slate-500">
              · workflow period <strong className="font-mono">{ctx.workflow_period}</strong>
            </span>
          </p>
        )}
        {profiles && <ProfileTable profiles={profiles} workflowPeriod={ctx.workflow_period} />}
        {ctx.missing && (
          <div className="rounded-xl bg-red-50 p-3 ring-1 ring-inset ring-red-200">
            <p className="text-sm font-bold text-red-800">
              Missing: {(ctx.missing as string[]).join(", ")}
            </p>
            <p className="mt-0.5 text-xs text-red-700">
              {ctx.quarantined ? `${ctx.quarantined} file(s) were quarantined as unreadable. ` : ""}
              Re-upload the missing extract, or reject the run.
            </p>
          </div>
        )}
        {ctx.quarantined != null && ctx.quarantined > 0 && !ctx.missing && (
          <p className="text-xs text-slate-500">
            {ctx.quarantined} file(s) quarantined as unreadable — the rest parsed cleanly.
          </p>
        )}
      </div>
    );
  }

  if (type === "validation_blocker") {
    const blockers = (ctx.blockers as any[]) ?? [];
    const causes = (ctx.likely_causes as string[]) ?? [];
    return (
      <div className="space-y-3">
        <p className="text-sm leading-relaxed text-slate-600">{typeMeta(type).explainer}</p>
        {ctx.period && (
          <p className="text-xs text-slate-500">
            Period <strong className="font-mono text-slate-700">{ctx.period}</strong>
            {ctx.stage ? (
              <>
                {" "}· raised in <strong>{ctx.stage}</strong>
              </>
            ) : null}
            {ctx.missing_kinds && (
              <>
                {" "}· missing <strong>{(ctx.missing_kinds as string[]).join(", ")}</strong>
              </>
            )}
          </p>
        )}
        {ctx.missing_columns && (
          <div className="rounded-xl bg-red-50 p-3 ring-1 ring-inset ring-red-200">
            <p className="text-sm font-bold text-red-800">
              Unresolvable columns: {(ctx.missing_columns as string[]).join(", ")}
            </p>
            {ctx.affected_metrics && (
              <p className="mt-1 text-xs text-red-700">
                Blocks: {(ctx.affected_metrics as string[]).join(", ")}
              </p>
            )}
          </div>
        )}
        {blockers.length > 0 && (
          <div className="space-y-2">
            {blockers.map((b: any, i: number) => (
              <div
                key={i}
                className="rounded-xl border-l-4 border-l-red-400 bg-white p-3 ring-1 ring-inset ring-slate-200"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs font-bold text-slate-800">
                    {b.check_id}
                  </code>
                  <span className="text-sm font-semibold text-slate-800">{b.message}</span>
                </div>
                {b.details && typeof b.details === "object" && (
                  <div className="mt-2">
                    <KeyGrid obj={flattenDetails(b.details)} />
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
        {causes.length > 0 && (
          <div className="rounded-xl bg-slate-50 p-3 ring-1 ring-inset ring-slate-200/70">
            <p className="text-[11px] font-bold uppercase tracking-wide text-slate-500">
              Likely causes to rule out
            </p>
            <ul className="mt-1.5 list-disc space-y-0.5 pl-5 text-[13px] leading-relaxed text-slate-700">
              {causes.map((c, i) => (
                <li key={i}>{c}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
    );
  }

  if (type === "schema_mapping") {
    const unmapped = (ctx.unmapped as any[]) ?? [];
    return (
      <div className="space-y-3">
        <p className="text-sm leading-relaxed text-slate-600">{typeMeta(type).explainer}</p>
        {unmapped.length > 0 && (
          <div className="overflow-x-auto rounded-xl ring-1 ring-inset ring-slate-200">
            <table className="data-table min-w-[480px]">
              <thead>
                <tr>
                  <th>Unknown column</th>
                  <th>Dataset</th>
                  <th>Suggested mapping</th>
                </tr>
              </thead>
              <tbody>
                {unmapped.map((u: any, i: number) => (
                  <tr key={i}>
                    <td className="font-mono text-xs font-bold text-slate-800">{u.column}</td>
                    <td className="text-slate-500">{u.kind}</td>
                    <td className="font-mono text-xs text-emerald-700">
                      {u.suggestions?.[0] ?? "— no confident match —"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    );
  }

  if (type === "assumption_variance") {
    const variances = (ctx.variances as any[]) ?? [];
    const method = ctx.methodology as any;
    return (
      <div className="space-y-3">
        <p className="text-sm leading-relaxed text-slate-600">{typeMeta(type).explainer}</p>
        {variances.map((v: any, i: number) => (
          <div key={i} className="rounded-xl bg-white p-3.5 ring-1 ring-inset ring-slate-200">
            <div className="flex flex-wrap items-baseline gap-2">
              <span className="text-sm font-bold text-slate-900">
                {[v.product, v.segment, v.region].filter(Boolean).join(" · ") || "Portfolio"}
              </span>
              <Badge tone="red">+{v.variance_pp}pp over trend</Badge>
            </div>
            <div className="mt-2.5 grid grid-cols-3 gap-2 text-center">
              {[
                { l: "Observed", v: `${v.observed_delta_pct >= 0 ? "+" : ""}${v.observed_delta_pct}%`, c: "text-red-700 bg-red-50" },
                { l: "Expected", v: `+${v.expected_trend_pct}%`, c: "text-slate-600 bg-slate-100" },
                { l: "Variance", v: `+${v.variance_pp}pp`, c: "text-red-800 bg-red-100" },
              ].map((s) => (
                <div key={s.l} className={`rounded-lg px-2 py-2 ${s.c}`}>
                  <div className="text-[10px] font-bold uppercase tracking-wide opacity-70">{s.l}</div>
                  <div className="text-sm font-black tabular-nums">{s.v}</div>
                </div>
              ))}
            </div>
          </div>
        ))}
        {method && (
          <div className="flex items-start gap-2.5 rounded-xl bg-amber-50/70 p-3 ring-1 ring-inset ring-amber-200/70">
            <span className="text-lg">📖</span>
            <div className="text-[13px]">
              <span className="font-bold text-slate-800">{method.title}</span>{" "}
              <span className="text-slate-500">({method.version})</span>
              {method.excerpt && (
                <p className="mt-1 line-clamp-3 leading-relaxed text-slate-600">{method.excerpt}</p>
              )}
            </div>
          </div>
        )}
        {ctx.disclaimer && (
          <p className="border-l-2 border-slate-200 pl-3 text-[13px] italic text-slate-500">
            {ctx.disclaimer}
          </p>
        )}
      </div>
    );
  }

  if (type === "finding_review") {
    const conf = typeof ctx.confidence === "number" ? ctx.confidence : null;
    return (
      <div className="space-y-3">
        <p className="text-sm leading-relaxed text-slate-600">{typeMeta(type).explainer}</p>
        <div className="flex items-center gap-3 rounded-xl bg-slate-50 p-3 ring-1 ring-inset ring-slate-200/70">
          <div className="min-w-0 flex-1">
            <div className="flex items-baseline justify-between text-xs">
              <span className="font-bold uppercase tracking-wide text-slate-500">Model confidence</span>
              <span className="font-black tabular-nums text-slate-800">
                {conf != null ? conf.toFixed(2) : "—"}
              </span>
            </div>
            <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-slate-200">
              <div
                className={`h-full rounded-full ${conf != null && conf < 0.6 ? "bg-amber-500" : "bg-emerald-500"}`}
                style={{ width: `${Math.round((conf ?? 0) * 100)}%` }}
              />
            </div>
          </div>
        </div>
        {ctx.reason && <p className="text-[13px] text-slate-600">Flagged because: {String(ctx.reason)}</p>}
      </div>
    );
  }

  if (type === "final_approval") {
    const checklist = (ctx.checklist ?? {}) as Record<string, boolean>;
    const open = (ctx.open_checkpoints as any[]) ?? [];
    return (
      <div className="space-y-3">
        <p className="text-sm leading-relaxed text-slate-600">{typeMeta(type).explainer}</p>
        <div className="rounded-xl bg-emerald-50/60 p-3 ring-1 ring-inset ring-emerald-200/70">
          <p className="text-[11px] font-bold uppercase tracking-wide text-emerald-800">
            QA verification — report v{String(ctx.version ?? "?")}
          </p>
          <ul className="mt-2 grid gap-1.5 sm:grid-cols-2">
            {Object.entries(checklist).map(([k, v]) => (
              <li key={k} className="flex items-center gap-2 text-[13px] font-medium text-slate-700">
                <span
                  className={`flex h-5 w-5 items-center justify-center rounded-full text-[11px] font-black ${
                    v ? "bg-emerald-500 text-white" : "bg-slate-200 text-slate-500"
                  }`}
                >
                  {v ? "✓" : "·"}
                </span>
                {k.replaceAll("_", " ")}
              </li>
            ))}
          </ul>
        </div>
        {open.length > 0 ? (
          <div className="rounded-xl bg-amber-50 p-3 ring-1 ring-inset ring-amber-200/70">
            <p className="text-[11px] font-bold uppercase tracking-wide text-amber-800">
              Still open when QA passed ({open.length})
            </p>
            <ul className="mt-1.5 space-y-1 text-[13px] text-slate-700">
              {open.map((o: any, i: number) => (
                <li key={i}>
                  <strong>{o.type?.replaceAll("_", " ")}</strong> — {o.title}
                </li>
              ))}
            </ul>
          </div>
        ) : (
          <p className="flex items-center gap-1.5 text-[13px] font-medium text-emerald-700">
            <span>✓</span> No open checkpoints — clean sign-off.
          </p>
        )}
      </div>
    );
  }

  if (type === "qa_failure") {
    const failed = (ctx.failed_checks as any[]) ?? [];
    const mismatches = (ctx.mismatches as any[]) ?? [];
    return (
      <div className="space-y-3">
        <p className="text-sm leading-relaxed text-slate-600">{typeMeta(type).explainer}</p>
        {failed.length > 0 && (
          <div className="space-y-2">
            {failed.map((f: any, i: number) => (
              <div key={i} className="rounded-xl border-l-4 border-l-red-400 bg-white p-3 ring-1 ring-inset ring-slate-200">
                <p className="text-sm font-bold text-slate-900">{f.name ?? f.check_id ?? `Failed check ${i + 1}`}</p>
                {f.message && <p className="mt-0.5 text-[13px] text-slate-600">{f.message}</p>}
              </div>
            ))}
          </div>
        )}
        {mismatches.length > 0 && (
          <div className="overflow-x-auto rounded-xl ring-1 ring-inset ring-slate-200">
            <table className="data-table min-w-[480px]">
              <thead>
                <tr>
                  <th>Metric</th>
                  <th>Report says</th>
                  <th>System says</th>
                </tr>
              </thead>
              <tbody>
                {mismatches.slice(0, 8).map((m: any, i: number) => (
                  <tr key={i}>
                    <td className="font-mono text-xs">{m.metric_key ?? m.metric ?? `row ${i + 1}`}</td>
                    <td className="tabular-nums text-red-700">{fmtVal(m.report_value ?? m.reported)}</td>
                    <td className="tabular-nums text-emerald-700">{fmtVal(m.actual_value ?? m.expected)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {ctx.regen_error && (
          <p className="rounded-lg bg-red-50 px-3 py-2 text-[13px] font-medium text-red-700 ring-1 ring-inset ring-red-200">
            Regeneration error: {String(ctx.regen_error)}
          </p>
        )}
      </div>
    );
  }

  // fallback: tidy key grid, never a JSON wall
  const entries = Object.entries(ctx ?? {});
  if (!entries.length)
    return <p className="text-sm text-slate-500">No extra detail — decide to resume the pipeline.</p>;
  return (
    <div className="space-y-3">
      <p className="text-sm leading-relaxed text-slate-600">{typeMeta(type).explainer}</p>
      <KeyGrid obj={Object.fromEntries(entries.map(([k, v]) => [k, typeof v === "object" ? JSON.stringify(v).slice(0, 120) : v]))} />
    </div>
  );
}

function flattenDetails(details: Record<string, any>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(details)) {
    if (v !== null && typeof v === "object" && !Array.isArray(v)) {
      for (const [k2, v2] of Object.entries(v as Record<string, unknown>)) {
        if (typeof v2 !== "object") out[`${k} ${k2}`] = v2;
      }
      if (!Object.keys(out).length) out[k] = JSON.stringify(v).slice(0, 80);
    } else if (Array.isArray(v)) {
      out[k] = v.length <= 5 ? v.join(", ") : `${v.length} items`;
    } else {
      out[k] = v;
    }
  }
  return out;
}

/* ------------------------------------------------------------------ */
/* decision form: card picker + consequence preview                    */
/* ------------------------------------------------------------------ */

export function DecisionForm({
  workflowId,
  checkpoint,
  onApplied,
  onDone,
}: {
  workflowId: string;
  checkpoint: PendingCheckpoint;
  onApplied: () => void;
  onDone: () => void;
}) {
  const distinctDecisions = useMemo(
    () => Array.from(new Set(checkpoint.options.map((o) => o.decision))),
    [checkpoint.options]
  );
  const [decision, setDecision] = useState(distinctDecisions[0] ?? "");
  const [rationale, setRationale] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);
  const { submit, submitting, error } = useDecision(workflowId, onApplied);
  const needsRationale =
    RATIONALE_REQUIRED.has(decision) || decision === "review_assumption";

  const decisionOptions = checkpoint.options.filter((o) => o.decision === decision);
  const [optionIdx, setOptionIdx] = useState(0);
  const activeIdx = Math.min(optionIdx, Math.max(decisionOptions.length - 1, 0));
  const chosenOption = decisionOptions[activeIdx];
  const meta = decisionMeta(decision);

  const optionLabel = (d: string) =>
    checkpoint.options.find((o) => o.decision === d)?.label ?? prettyDecision(d);

  async function handleSubmit() {
    setLocalError(null);
    if (needsRationale && rationale.trim().length < 20) {
      setLocalError("Add a rationale of at least 20 characters — the audit trail needs it.");
      return;
    }
    const parsed: Record<string, unknown> = { ...(chosenOption?.payload ?? {}) };
    try {
      await submit({
        checkpoint_id: checkpoint.id,
        decision,
        rationale,
        payload: parsed,
      });
      onDone();
    } catch {
      /* error shown inline */
    }
  }

  return (
    <div className="space-y-4">
      <div>
        <span className="field-label">Your decision</span>
        <div className="grid gap-2">
          {distinctDecisions.map((d) => {
            const m = decisionMeta(d);
            const active = d === decision;
            const requiresNote = RATIONALE_REQUIRED.has(d);
            return (
              <button
                key={d}
                type="button"
                onClick={() => {
                  setDecision(d);
                  setOptionIdx(0);
                }}
                aria-pressed={active}
                className={`flex items-start gap-3 rounded-xl border p-3 text-left transition-all ${
                  active
                    ? "border-blue-600 bg-blue-50/70 ring-1 ring-blue-600/30"
                    : "border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50"
                }`}
              >
                <span
                  className={`mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-sm font-black ${
                    active ? "bg-blue-600 text-white" : "bg-slate-100 text-slate-500"
                  }`}
                >
                  {m.icon}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="flex flex-wrap items-center gap-2 text-sm font-bold capitalize text-slate-900">
                    {optionLabel(d)}
                    {requiresNote && (
                      <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-amber-800">
                        needs note
                      </span>
                    )}
                  </span>
                  <span className="mt-0.5 block text-xs leading-relaxed text-slate-500">{m.blurb}</span>
                </span>
                <span
                  className={`mt-1 h-4 w-4 shrink-0 rounded-full border-2 ${
                    active ? "border-blue-600 bg-blue-600 ring-2 ring-blue-200" : "border-slate-300"
                  }`}
                />
              </button>
            );
          })}
        </div>
      </div>

      {decisionOptions.length > 1 && (
        <div className="space-y-2">
          <span className="field-label">Which one? ({decisionOptions.length} options)</span>
          {decision === "select_file" ? (
            <FileChoiceCards
              options={decisionOptions}
              activeIdx={activeIdx}
              onPick={setOptionIdx}
              context={checkpoint.context}
            />
          ) : (
            decisionOptions.map((o, i) => (
              <label
                key={i}
                className={`flex cursor-pointer items-center gap-3 rounded-xl border px-3 py-2.5 text-sm transition-all ${
                  i === activeIdx
                    ? "border-blue-600 bg-blue-50/60 ring-1 ring-blue-600/30"
                    : "border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50"
                }`}
              >
                <input
                  type="radio"
                  name="cp-option"
                  className="h-4 w-4 accent-blue-700"
                  checked={i === activeIdx}
                  onChange={() => setOptionIdx(i)}
                />
                <span className="font-medium text-slate-800">{o.label}</span>
              </label>
            ))
          )}
        </div>
      )}

      <div className="flex items-start gap-2 rounded-xl bg-slate-50 p-3 ring-1 ring-inset ring-slate-200/70">
        <span className="text-sm">→</span>
        <p className="text-[13px] leading-relaxed text-slate-600">
          <strong className="text-slate-800">What happens next: </strong>
          {meta.next}
        </p>
      </div>

      <div>
        <div className="flex items-baseline justify-between">
          <label className="field-label" htmlFor="cp-rationale">
            Rationale {needsRationale ? "(required, ≥20 chars)" : "(optional, recommended)"}
          </label>
          <span
            className={`text-xs tabular-nums ${
              rationale.trim().length >= 20 ? "font-bold text-emerald-600" : "text-slate-400"
            }`}
          >
            {rationale.trim().length}/20
          </span>
        </div>
        <textarea
          id="cp-rationale"
          value={rationale}
          onChange={(e) => setRationale(e.target.value)}
          rows={3}
          className="field-input resize-y"
          placeholder="e.g. v2 matches the September close; v1 is the August extract re-sent by mistake."
        />
      </div>
      {(localError || error) && (
        <p
          className="rounded-lg bg-red-50 px-3 py-2 text-sm font-medium text-red-700 ring-1 ring-inset ring-red-200"
          role="alert"
        >
          {localError ?? error}
        </p>
      )}
      <div className="flex justify-end gap-2 pt-1">
        <Button variant="secondary" onClick={onDone}>
          Cancel
        </Button>
        <Button onClick={handleSubmit} disabled={submitting}>
          {submitting ? (
            <>
              <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-white" />
              Submitting…
            </>
          ) : (
            `Confirm — ${prettyDecision(decision)}`
          )}
        </Button>
      </div>
    </div>
  );
}

function FileChoiceCards({
  options,
  activeIdx,
  onPick,
  context,
}: {
  options: { label: string; payload?: Record<string, unknown> }[];
  activeIdx: number;
  onPick: (i: number) => void;
  context: Record<string, unknown>;
}) {
  const profiles = ((context as any)?.profiles as any[]) ?? [];
  const byId = new Map(profiles.map((p) => [String(p.file_id), p]));
  return (
    <div className="grid gap-2">
      {options.map((o, i) => {
        const fid = String((o.payload as any)?.file_id ?? "");
        const p = byId.get(fid);
        const active = i === activeIdx;
        return (
          <button
            key={i}
            type="button"
            onClick={() => onPick(i)}
            aria-pressed={active}
            className={`rounded-xl border p-3 text-left transition-all ${
              active
                ? "border-blue-600 bg-blue-50/70 ring-1 ring-blue-600/30"
                : "border-slate-200 bg-white hover:border-slate-300"
            }`}
          >
            <span className="flex items-center justify-between gap-2">
              <span className="truncate font-mono text-[13px] font-bold text-slate-900">
                {p?.filename ?? o.label}
              </span>
              <span
                className={`h-4 w-4 shrink-0 rounded-full border-2 ${
                  active ? "border-blue-600 bg-blue-600 ring-2 ring-blue-200" : "border-slate-300"
                }`}
              />
            </span>
            {p && (
              <span className="mt-1.5 flex flex-wrap gap-x-4 gap-y-0.5 text-xs text-slate-500">
                <span>
                  <strong className="tabular-nums text-slate-700">{p.row_count?.toLocaleString?.() ?? p.row_count}</strong> rows
                </span>
                <span>
                  period <strong className="font-mono text-slate-700">{p.period_inferred ?? "—"}</strong>
                </span>
                <span className="font-mono text-slate-400">⛨ {String(p.checksum ?? "").slice(0, 12)}</span>
                {p.stale ? (
                  <span className="font-bold text-amber-700">⚠ looks stale</span>
                ) : (
                  <span className="font-bold text-emerald-700">✓ period matches</span>
                )}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* banner + modal                                                      */
/* ------------------------------------------------------------------ */

export function CheckpointBanner({
  workflowId,
  checkpoints,
  onApplied,
}: {
  workflowId: string;
  checkpoints: PendingCheckpoint[];
  onApplied: () => void;
}) {
  const [openId, setOpenId] = useState<string | null>(() => {
    const blocking = checkpoints.find((c) => c.blocking);
    return blocking ? blocking.id : null;
  });
  if (!checkpoints.length) return null;
  const open = checkpoints.find((c) => c.id === openId) ?? null;
  const openIdx = open ? checkpoints.findIndex((c) => c.id === open.id) : -1;
  return (
    <div className="space-y-2.5">
      <div className="flex items-center justify-between">
        <span className="text-xs font-bold uppercase tracking-[0.08em] text-slate-500">
          {checkpoints.filter((c) => c.blocking).length > 0
            ? `✋ ${checkpoints.length} decision${checkpoints.length > 1 ? "s" : ""} needed — pipeline paused`
            : `${checkpoints.length} suggestion${checkpoints.length > 1 ? "s" : ""} for you`}
        </span>
      </div>
      {checkpoints.map((c, i) => {
        const m = typeMeta(c.type);
        return (
          <div
            key={c.id}
            className={`animate-banner-in flex items-center justify-between gap-4 overflow-hidden rounded-xl border bg-white p-4 shadow-card ${
              c.blocking ? "border-red-200 border-l-4 border-l-red-500" : "border-slate-200 border-l-4 border-l-amber-400"
            }`}
          >
            <div className="flex min-w-0 items-start gap-3">
              <span
                aria-hidden="true"
                className={`mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-xl text-lg font-black ${
                  c.blocking ? "bg-red-600 text-white" : "bg-amber-400 text-white"
                }`}
              >
                {m.icon}
              </span>
              <div className="min-w-0 text-sm">
                <div className="flex flex-wrap items-center gap-1.5">
                  <Badge tone={c.blocking ? "red" : "yellow"} dot>
                    {m.code}
                  </Badge>
                  <span className="text-[11px] font-bold uppercase tracking-wide text-slate-400">
                    {m.label} · {i + 1} of {checkpoints.length}
                  </span>
                </div>
                <div className="mt-1 text-[15px] font-bold tracking-tight text-slate-900">
                  {c.title}
                </div>
                <p className="mt-0.5 line-clamp-1 text-xs text-slate-500">
                  {c.blocking
                    ? "Blocking — decide to resume the pipeline."
                    : "Advisory — the pipeline continues either way."}
                </p>
              </div>
            </div>
            <Button
              variant={c.blocking ? "primary" : "secondary"}
              onClick={() => setOpenId(c.id)}
            >
              Review →
            </Button>
          </div>
        );
      })}
      {open && (
        <Modal
          title={`${typeMeta(open.type).code} · ${open.title}`}
          subtitle={
            open.blocking
              ? `Blocking ${typeMeta(open.type).label.toLowerCase()} — your decision resumes the pipeline (${openIdx + 1} of ${checkpoints.length}).`
              : `Advisory ${typeMeta(open.type).label.toLowerCase()} — deciding is optional but recorded.`
          }
          onClose={() => setOpenId(null)}
          wide
        >
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-1.5">
              <Badge tone={open.blocking ? "red" : "yellow"} dot>
                {open.blocking ? "Action required — workflow paused" : "Review suggested"}
              </Badge>
              <Badge tone="slate">{typeMeta(open.type).label}</Badge>
              <Badge tone="slate">{open.severity}</Badge>
            </div>
            <section>
              <h3 className="mb-2 text-[11px] font-bold uppercase tracking-[0.08em] text-slate-500">
                What the agent found
              </h3>
              <CheckpointEvidence checkpoint={open} />
            </section>
            <section className="border-t border-slate-100 pt-4">
              <h3 className="mb-3 text-[11px] font-bold uppercase tracking-[0.08em] text-slate-500">
                Your review
              </h3>
              <DecisionForm
                workflowId={workflowId}
                checkpoint={open}
                onApplied={onApplied}
                onDone={() => setOpenId(null)}
              />
            </section>
          </div>
        </Modal>
      )}
    </div>
  );
}

/** Polling-aware checkpoint list for tabs that need the raw data. */
export function CheckpointListState({ loading }: { loading: boolean }) {
  if (loading) return <Spinner label="Checking for decisions…" />;
  return null;
}
