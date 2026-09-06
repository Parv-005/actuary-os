"use client";

import { Badge, Card, EmptyState, Spinner } from "@/components/ui/primitives";
import type { AgentRunRow, AuditEvent } from "@/lib/api";

export function AuditTimeline({
  events,
  loading,
}: {
  events: AuditEvent[] | null;
  loading: boolean;
}) {
  if (loading) return <Spinner label="Loading audit timeline…" />;
  if (!events || !events.length)
    return <EmptyState message="No audit events yet." />;
  return (
    <Card className="p-4">
      <ol className="space-y-2 text-sm">
        {events.map((e) => (
          <li key={e.id} className="flex gap-3 border-b border-slate-100 pb-2 last:border-0">
            <span className="w-44 shrink-0 text-xs text-slate-500">
              {e.ts ? new Date(e.ts).toLocaleString() : "—"}
            </span>
            <div>
              <span className="font-medium">{e.action.replaceAll("_", " ")}</span>{" "}
              <span className="text-slate-500">
                · {e.actor_type}:{e.actor}
              </span>
              {e.from_status && e.to_status && (
                <span className="ml-1 text-slate-600">
                  {e.from_status} → {e.to_status}
                </span>
              )}
              {e.summary && <div className="text-slate-700">{e.summary}</div>}
            </div>
          </li>
        ))}
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
  if (!runs || !runs.length) return <EmptyState message="No agent runs yet." />;
  const totalCost = runs.reduce((a, r) => a + (r.cost_usd ?? 0), 0);
  return (
    <Card className="overflow-hidden">
      <div className="border-b border-slate-100 p-3 text-xs text-slate-500">
        {runs.length} runs · {runs.reduce((a, r) => a + r.llm_calls, 0)} LLM calls · $
        {totalCost.toFixed(4)} total
      </div>
      <table className="w-full text-left text-sm">
        <thead className="bg-slate-50 text-xs uppercase text-slate-500">
          <tr>
            <th className="px-4 py-2">Stage / agent</th>
            <th className="px-4 py-2">Attempt</th>
            <th className="px-4 py-2">Status</th>
            <th className="px-4 py-2">Duration</th>
            <th className="px-4 py-2">LLM (tok)</th>
            <th className="px-4 py-2">Error</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.id} className="border-t border-slate-100">
              <td className="px-4 py-2 font-medium">
                {r.stage} <span className="font-normal text-slate-500">· {r.agent}</span>
              </td>
              <td className="px-4 py-2">{r.attempt}</td>
              <td className="px-4 py-2">
                <Badge
                  tone={
                    r.status === "succeeded"
                      ? "green"
                      : r.status === "failed"
                        ? "red"
                        : "blue"
                  }
                >
                  {r.status}
                </Badge>
              </td>
              <td className="px-4 py-2">{(r.duration_ms / 1000).toFixed(1)}s</td>
              <td className="px-4 py-2">
                {r.llm_calls} ({r.tokens_in + r.tokens_out})
              </td>
              <td className="max-w-xs truncate px-4 py-2 text-xs text-red-700">
                {r.error ?? ""}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}
