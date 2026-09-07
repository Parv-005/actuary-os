"use client";

import { Badge, Card, EmptyState, Spinner } from "@/components/ui/primitives";
import type { AgentRunRow, AuditEvent } from "@/lib/api";

function eventTone(action: string): "slate" | "red" | "green" | "blue" {
  if (action.startsWith("decision_")) return "blue";
  if (action.startsWith("checkpoint_")) return "red";
  if (action === "qa_passed" || action === "report_approved" || action === "workflow_completed")
    return "green";
  if (action.includes("failed")) return "red";
  return "slate";
}

const DOT_COLOR: Record<string, string> = {
  slate: "bg-slate-300",
  red: "bg-red-500",
  green: "bg-emerald-500",
  blue: "bg-blue-500",
};

export function AuditTimeline({
  events,
  loading,
}: {
  events: AuditEvent[] | null;
  loading: boolean;
}) {
  if (loading) return <Spinner label="Loading audit timeline…" />;
  if (!events || !events.length)
    return (
      <EmptyState
        icon="≣"
        title="No audit events yet"
        message="Every agent action, transition and human decision lands here with actor and timestamp."
      />
    );
  return (
    <Card className="p-4 sm:p-5">
      <ol className="relative space-y-0 border-l-2 border-slate-100 pl-0">
        {events.map((e) => {
          const tone = eventTone(e.action);
          return (
            <li key={e.id} className="relative pb-5 pl-6 last:pb-0">
              <span
                aria-hidden="true"
                className={`absolute -left-[7px] top-1 h-3 w-3 rounded-full ring-4 ring-white ${DOT_COLOR[tone]}`}
              />
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                <span className="text-sm font-bold tracking-tight text-slate-800">
                  {e.action.replaceAll("_", " ")}
                </span>
                <span className="text-xs text-slate-400">
                  {e.actor_type}:{e.actor}
                </span>
                <span className="ml-auto text-xs tabular-nums text-slate-400">
                  {e.ts ? new Date(e.ts).toLocaleString() : "—"}
                </span>
              </div>
              <div className="mt-0.5 flex flex-wrap items-center gap-2 text-[13px]">
                {e.from_status && e.to_status && (
                  <span className="inline-flex items-center gap-1 font-mono text-xs">
                    <Badge tone="slate">{e.from_status}</Badge>
                    <span className="text-slate-400">→</span>
                    <Badge tone="slate">{e.to_status}</Badge>
                  </span>
                )}
                {e.summary && <span className="text-slate-600">{e.summary}</span>}
              </div>
            </li>
          );
        })}
      </ol>
    </Card>
  );
}

export function AgentRunTable({
  runs,
  loading,
}: {
  runs: AgentRunRow[] | null;
  loading: boolean;
}) {
  if (loading) return <Spinner label="Loading agent runs…" />;
  if (!runs || !runs.length)
    return (
      <EmptyState
        icon="⚙"
        title="No agent runs yet"
        message="Per-stage attempts, durations and LLM spend appear here."
      />
    );
  const totalCost = runs.reduce((a, r) => a + (r.cost_usd ?? 0), 0);
  return (
    <Card className="overflow-hidden">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-slate-100 bg-slate-50/60 px-4 py-2.5 text-xs text-slate-500">
        <span><strong className="tabular-nums text-slate-700">{runs.length}</strong> runs</span>
        <span>
          <strong className="tabular-nums text-slate-700">
            {runs.reduce((a, r) => a + r.llm_calls, 0)}
          </strong>{" "}
          LLM calls
        </span>
        <span>
          <strong className="tabular-nums text-slate-700">${totalCost.toFixed(4)}</strong> total
        </span>
      </div>
      <div className="overflow-x-auto">
        <table className="data-table min-w-[680px]">
          <thead>
            <tr>
              <th>Stage / agent</th>
              <th>Try</th>
              <th>Status</th>
              <th>Duration</th>
              <th>LLM (tok)</th>
              <th>Error</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((r) => (
              <tr key={r.id} className="transition-colors hover:bg-blue-50/40">
                <td className="font-semibold text-slate-800">
                  {r.stage}{" "}
                  <span className="font-normal text-slate-400">· {r.agent}</span>
                </td>
                <td className="tabular-nums text-slate-500">{r.attempt}</td>
                <td>
                  <Badge
                    tone={
                      r.status === "succeeded"
                        ? "green"
                        : r.status === "failed"
                          ? "red"
                          : "blue"
                    }
                    dot={r.status !== "succeeded"}
                  >
                    {r.status}
                  </Badge>
                </td>
                <td className="tabular-nums text-slate-600">
                  {(r.duration_ms / 1000).toFixed(1)}s
                </td>
                <td className="tabular-nums text-slate-600">
                  {r.llm_calls} <span className="text-slate-400">({r.tokens_in + r.tokens_out})</span>
                </td>
                <td className="max-w-xs truncate text-xs text-red-700" title={r.error ?? ""}>
                  {r.error ?? <span className="text-slate-300">—</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
