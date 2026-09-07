"use client";

import { Fragment, useState } from "react";
import { Badge, Card, EmptyState, Spinner } from "@/components/ui/primitives";
import type { ValidationResult } from "@/lib/api";

function statusTone(status: string): "red" | "yellow" | "green" | "slate" {
  if (status === "BLOCKER") return "red";
  if (status === "WARNING") return "yellow";
  if (status === "PASS") return "green";
  if (status === "ACCEPTED_EXCEPTION") return "slate";
  return "slate";
}

function fmtVal(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (typeof v === "number") {
    if (Number.isInteger(v)) return v.toLocaleString();
    return String(Math.round(v * 100) / 100);
  }
  if (Array.isArray(v)) return v.length <= 4 ? v.join(", ") : `${v.length} items`;
  return String(v);
}

function DetailGrid({ details }: { details: Record<string, unknown> }) {
  const entries = Object.entries(details ?? {});
  if (!entries.length) return <p className="text-xs text-slate-400">No extra inputs recorded.</p>;
  // coverage-style detail gets a progress bar
  const pct = typeof details.pct === "number" ? details.pct : null;
  const inP = typeof details.in_period === "number" ? details.in_period : null;
  const total = typeof details.total === "number" ? details.total : null;
  return (
    <div className="space-y-2.5">
      {pct != null && total != null && (
        <div>
          <div className="flex items-baseline justify-between text-xs">
            <span className="font-bold uppercase tracking-wide text-slate-500">Coverage in period</span>
            <span className="font-black tabular-nums text-slate-800">
              {pct.toFixed(1)}%{inP != null && total != null && ` (${inP}/${total})`}
            </span>
          </div>
          <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-slate-200">
            <div
              className={`h-full rounded-full ${pct >= 90 ? "bg-emerald-500" : pct >= 50 ? "bg-amber-500" : "bg-red-500"}`}
              style={{ width: `${Math.min(100, Math.max(0, pct))}%` }}
            />
          </div>
        </div>
      )}
      {(details as any).row_counts && typeof (details as any).row_counts === "object" && (
        <div className="flex flex-wrap gap-1.5">
          {Object.entries((details as any).row_counts as Record<string, unknown>).map(([k, v]) => (
            <span key={k} className="rounded-lg bg-white px-2.5 py-1 text-xs ring-1 ring-inset ring-slate-200">
              <strong className="font-bold text-slate-800">{k}</strong>{" "}
              <span className="tabular-nums text-slate-500">{fmtVal(v)} rows</span>
            </span>
          ))}
        </div>
      )}
      <dl className="grid gap-1.5 sm:grid-cols-2">
        {entries
          .filter(([k]) => !["pct", "in_period", "total", "row_counts", "period", "blocked"].includes(k))
          .slice(0, 8)
          .map(([k, v]) => (
            <div key={k} className="rounded-lg bg-white px-2.5 py-1.5 ring-1 ring-inset ring-slate-200/70">
              <dt className="text-[10px] font-bold uppercase tracking-wide text-slate-400">
                {k.replaceAll("_", " ")}
              </dt>
              <dd className="mt-0.5 break-words text-[13px] font-semibold text-slate-700">{fmtVal(v)}</dd>
            </div>
          ))}
      </dl>
    </div>
  );
}

export function ValidationTable({
  results,
  loading,
}: {
  results: ValidationResult[] | null;
  loading: boolean;
}) {
  const [open, setOpen] = useState<string | null>(null);
  const [filter, setFilter] = useState<"ALL" | "BLOCKER" | "WARNING" | "PASS" | "ACCEPTED_EXCEPTION">("ALL");
  if (loading) return <Spinner label="Loading validation checks…" />;
  if (!results || !results.length)
    return (
      <EmptyState
        icon="☑"
        title="No validation results yet"
        message="The validation agent runs L1–L4 checks after data prep — schema, records, reconciliation and behaviour."
      />
    );
  const counts = results.reduce<Record<string, number>>((acc, r) => {
    acc[r.status] = (acc[r.status] ?? 0) + 1;
    return acc;
  }, {});
  const total = results.length;
  const passPct = Math.round(((counts.PASS ?? 0) / total) * 100);
  const visible = filter === "ALL" ? results : results.filter((r) => r.status === filter);
  return (
    <div className="space-y-3">
      {/* summary strip */}
      <Card className="flex flex-wrap items-center gap-4 p-4">
        <div
          className="flex h-16 w-16 shrink-0 items-center justify-center rounded-full"
          style={{ background: `conic-gradient(#10b981 ${passPct}%, #e6ebf2 ${passPct}% 100%)` }}
        >
          <div className="flex h-12 w-12 flex-col items-center justify-center rounded-full bg-white">
            <span className="text-sm font-black tabular-nums text-slate-900">{passPct}%</span>
          </div>
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-bold text-slate-900">
            {counts.PASS ?? 0} of {total} checks pass
            {(counts.BLOCKER ?? 0) > 0 && (
              <span className="text-red-700"> · {counts.BLOCKER} blocking</span>
            )}
            {(counts.WARNING ?? 0) > 0 && (
              <span className="text-amber-700"> · {counts.WARNING} warnings</span>
            )}
          </p>
          <p className="mt-0.5 text-xs text-slate-500">
            Blockers pause the pipeline until you accept them as disclosed exceptions or re-run.
            Warnings flow through and are cited in the report.
          </p>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {(["ALL", "BLOCKER", "WARNING", "PASS", "ACCEPTED_EXCEPTION"] as const).map((s) => (
            <button
              key={s}
              onClick={() => setFilter(s)}
              aria-pressed={filter === s}
              className={`rounded-full px-2.5 py-1 text-xs font-bold transition-all ${
                filter === s ? "bg-ink-900 text-white" : "bg-slate-100 text-slate-500 hover:bg-slate-200"
              }`}
            >
              {s === "ALL" ? `All (${total})` : `${counts[s] ?? 0} × ${s.replaceAll("_", " ")}`}
            </button>
          ))}
        </div>
      </Card>

      <Card className="overflow-x-auto">
        <table className="data-table min-w-[640px]">
          <thead>
            <tr>
              <th>Check</th>
              <th>Category</th>
              <th>Status</th>
              <th>What it means</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((r) => (
              <Fragment key={r.check_id}>
                <tr
                  className="cursor-pointer transition-colors hover:bg-blue-50/40"
                  onClick={() => setOpen(open === r.check_id ? null : r.check_id)}
                >
                  <td className="font-semibold text-slate-800">
                    <span className="mr-1.5 inline-block text-[10px] text-slate-400 transition-transform">
                      {open === r.check_id ? "▼" : "▶"}
                    </span>
                    {r.name || r.check_id}
                    {(r.affected_row_count ?? 0) > 0 && (
                      <span className="ml-2 text-[11px] font-medium tabular-nums text-slate-400">
                        {(r.affected_row_count ?? 0).toLocaleString()} rows
                      </span>
                    )}
                  </td>
                  <td className="text-slate-500">{r.category}</td>
                  <td>
                    <Badge tone={statusTone(r.status)} dot={r.status !== "PASS"}>
                      {r.status.replaceAll("_", " ")}
                    </Badge>
                  </td>
                  <td className="max-w-md text-slate-700">{r.message}</td>
                </tr>
                {open === r.check_id && (
                  <tr className="bg-slate-50/70">
                    <td colSpan={4} className="px-4 py-3">
                      <DetailGrid details={r.details ?? {}} />
                      {r.resolution && (
                        <p className="mt-2 rounded-lg bg-emerald-50 px-3 py-2 text-xs font-medium text-emerald-800 ring-1 ring-inset ring-emerald-200">
                          ✓ Resolved — {(r.resolution as any).rationale ?? "accepted as exception"}
                          {(r.resolution as any).decided_by ? ` · by ${(r.resolution as any).decided_by}` : ""}
                        </p>
                      )}
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </Card>
      <p className="text-xs text-slate-400">
        Click any row for thresholds, coverage and the recorded resolution.
      </p>
    </div>
  );
}
