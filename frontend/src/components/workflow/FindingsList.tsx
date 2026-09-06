"use client";

import Link from "next/link";
import { Badge, Card, EmptyState, Spinner } from "@/components/ui/primitives";
import type { FindingSummary } from "@/lib/api";

const SEV_ICON: Record<string, string> = { high: "🔴", medium: "🟠", low: "🟢" };

export function severityIcon(sev: string): string {
  return SEV_ICON[sev] ?? "⚪";
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
    return <EmptyState message="No findings yet — they appear after investigation." />;
  return (
    <div className="grid gap-3">
      {findings.map((f) => (
        <Link key={f.id} href={`/workflows/${workflowId}/findings/${f.id}`}>
          <Card className="p-4 transition-colors hover:border-slate-400">
            <div className="flex items-start justify-between gap-3">
              <div className="font-medium">
                {severityIcon(f.severity)} {f.title}
              </div>
              <Badge
                tone={
                  f.status === "draft"
                    ? "slate"
                    : f.status === "rejected"
                      ? "red"
                      : "green"
                }
              >
                {f.status.replaceAll("_", " ")}
              </Badge>
            </div>
            <div className="mt-1 text-sm text-slate-600">
              confidence {f.confidence.toFixed(2)} · {f.evidence_count} evidence
              {f.alternatives.length > 0 &&
                ` · alternatives: ${f.alternatives.slice(0, 2).join("; ")}`}
            </div>
            <div className="mt-1 flex flex-wrap gap-1">
              {f.human_review_required && (
                <Badge tone="yellow">needs human review</Badge>
              )}
              {f.decision_question && (
                <span className="text-xs text-slate-500">{f.decision_question}</span>
              )}
            </div>
          </Card>
        </Link>
      ))}
    </div>
  );
}
