"use client";

import Link from "next/link";
import { Badge, Card, EmptyState, Spinner } from "@/components/ui/primitives";
import type { FindingSummary } from "@/lib/api";

const SEV_STYLE: Record<string, { icon: string; rail: string; chip: "red" | "yellow" | "green" }> = {
  high: { icon: "▲", rail: "bg-red-500", chip: "red" },
  medium: { icon: "◆", rail: "bg-amber-400", chip: "yellow" },
  low: { icon: "●", rail: "bg-emerald-500", chip: "green" },
};

const SEV_ICON: Record<string, string> = { high: "🔴", medium: "🟠", low: "🟢" };

export function severityIcon(sev: string): string {
  return SEV_ICON[sev] ?? "⚪";
}

function statusTone(status: string): "slate" | "red" | "green" {
  if (status === "rejected") return "red";
  if (status === "draft") return "slate";
  return "green";
}

export function FindingsList({
  workflowId,
  findings,
  loading,
}: {
  workflowId: string;
  findings: FindingSummary[] | null;
  loading: boolean;
}) {
  if (loading) return <Spinner label="Loading findings…" />;
  if (!findings || !findings.length)
    return (
      <EmptyState
        icon="◉"
        title="No findings yet"
        message="They appear here once investigation completes — each one links to its full evidence chain."
      />
    );
  return (
    <div className="grid gap-3">
      {findings.map((f) => {
        const sev = SEV_STYLE[f.severity] ?? SEV_STYLE.low;
        return (
          <Link key={f.id} href={`/workflows/${workflowId}/findings/${f.id}`} className="block">
            <Card className="card-lift flex gap-4 overflow-hidden p-0">
              <div className={`w-1.5 shrink-0 ${sev.rail}`} aria-hidden="true" />
              <div className="min-w-0 flex-1 p-4 sm:p-5">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge tone={sev.chip} dot>
                        {f.severity}
                      </Badge>
                      <span className="text-[15px] font-bold tracking-tight text-slate-900">
                        {f.title}
                      </span>
                    </div>
                    <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px] text-slate-500">
                      <span>
                        confidence{" "}
                        <strong className="tabular-nums text-slate-700">
                          {f.confidence.toFixed(2)}
                        </strong>
                      </span>
                      <span aria-hidden="true">·</span>
                      <span>
                        <strong className="tabular-nums text-slate-700">
                          {f.evidence_count}
                        </strong>{" "}
                        evidence
                      </span>
                      {f.human_review_required && (
                        <>
                          <span aria-hidden="true">·</span>
                          <Badge tone="yellow">needs human review</Badge>
                        </>
                      )}
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <Badge tone={statusTone(f.status)}>{f.status.replaceAll("_", " ")}</Badge>
                    <span className="text-slate-300 transition-transform">→</span>
                  </div>
                </div>
                {f.alternatives.length > 0 && (
                  <p className="mt-2 truncate text-[13px] text-slate-500">
                    Alternatives ruled out: {f.alternatives.slice(0, 2).join("; ")}
                  </p>
                )}
                {f.decision_question && (
                  <p className="mt-1 text-[13px] font-medium text-blue-800">
                    ? {f.decision_question}
                  </p>
                )}
              </div>
            </Card>
          </Link>
        );
      })}
    </div>
  );
}
