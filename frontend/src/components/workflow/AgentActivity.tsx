"use client";

import { useEffect, useState } from "react";
import { Card, LivePill } from "@/components/ui/primitives";
import { stageLabel, type AgentRunRow, type AuditEvent, type WorkflowStatus } from "@/lib/api";
import { Badge } from "@/components/ui/primitives";

/* Human-readable "what is it doing" per stage — derived from the §5 agent specs. */
const STAGE_INTEL: Record<string, { icon: string; doing: string; outputs: string }> = {
  intake: {
    icon: "⧉",
    doing: "Reading every CSV — parsing headers, inferring periods, sniffing duplicates.",
    outputs: "File profiles + quarantine list, or a CP-1 checkpoint if two files collide.",
  },
  data_prep: {
    icon: "⟷",
    doing: "Mapping columns to canonical names, parsing amounts, dropping exact duplicates.",
    outputs: "Canonical datasets + transform log, or a CP-3 mapping question.",
  },
  validation: {
    icon: "☑",
    doing: "Running L1–L4 checks: schema, record sanity, reconciliation vs system of record.",
    outputs: "Pass/warn/block table, or a CP-2 blocker with likely causes.",
  },
  analysis: {
    icon: "📊",
    doing: "Aggregating loss ratio, frequency, severity across portfolio → segment → region.",
    outputs: "Frozen metrics + prior-period deltas for every cell.",
  },
  insight: {
    icon: "🔍",
    doing: "Hunting what moved and why — ranking segments, testing drivers, drafting the finding.",
    outputs: "One ranked finding + CP-4 assumption check if severity drifted.",
  },
  knowledge: {
    icon: "📖",
    doing: "Cross-checking the finding against methodology docs and prior reviews.",
    outputs: "Citations + repeat-item linkage.",
  },
  reporting: {
    icon: "§",
    doing: "Drafting the executive summary, key metrics, findings and exceptions.",
    outputs: "A new versioned draft for QA to verify.",
  },
  qa: {
    icon: "✓",
    doing: "Re-computing every number in the draft from frozen metrics — no trust, just verify.",
    outputs: "Pass → CP-6 approval ask. Fail → CP-7 with the exact mismatches.",
  },
};

function timeAgo(iso: string | null): string {
  if (!iso) return "—";
  const s = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 5) return "just now";
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s ago`;
  return `${Math.floor(s / 3600)}h ago`;
}

function elapsedSince(iso: string | null, now: number): string {
  if (!iso) return "—";
  const s = Math.max(0, Math.floor((now - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
}

function humanAction(action: string): { icon: string; text: string } {
  if (action.startsWith("transition_")) {
    const parts = action.replace("transition_", "").split("_to_");
    return { icon: "→", text: `moved ${parts[0]?.replaceAll("_", " ")} → ${parts[1]?.replaceAll("_", " ")}` };
  }
  const map: Record<string, string> = {
    intake_complete: "classified the CSVs",
    file_quarantined: "quarantined an unreadable file",
    stale_period_detected: "spotted a stale-period file",
    data_prep_complete: "standardised the datasets",
    validation_complete: "finished validation checks",
    validation_blocked: "blocked on a validation check",
    checkpoint_raised: "asked for a human decision",
    checkpoint_resolved: "got a human decision",
    qa_passed: "passed QA number-verify",
    report_approved: "got its report approved",
    workflow_completed: "completed the review",
  };
  for (const [k, v] of Object.entries(map)) {
    if (action === k || action.startsWith(k)) return { icon: k.includes("blocked") || k.includes("quarantined") ? "⚠" : "✓", text: v };
  }
  if (action.startsWith("decision_")) return { icon: "✎", text: `you decided: ${action.replace("decision_", "").replaceAll("_", " ")}` };
  if (action.startsWith("finding_")) return { icon: "◉", text: `finding ${action.replace("finding_", "").replaceAll("_", " ")}` };
  if (action.startsWith("report_")) return { icon: "§", text: `report ${action.replace("report_", "").replaceAll("_", " ")}` };
  if (action.includes("failed")) return { icon: "✕", text: action.replaceAll("_", " ") };
  return { icon: "•", text: action.replaceAll("_", " ") };
}

export function AgentActivity({
  status,
  runs,
  events,
  polling,
}: {
  status: WorkflowStatus;
  runs: AgentRunRow[] | null;
  events: AuditEvent[] | null;
  polling: boolean;
}) {
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!polling) return;
    const t = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [polling]);

  const now = Date.now();
  const running = (runs ?? []).filter((r) => r.status === "running");
  const latestRunning = running.length ? running[running.length - 1] : null;
  const currentStage = latestRunning?.stage ?? status.stage;
  const intel = currentStage ? STAGE_INTEL[currentStage] : null;
  const isActive = polling && running.length > 0;
  const isPaused =
    status.status === "BLOCKED" ||
    status.status === "WAITING_FOR_HUMAN" ||
    status.pending_checkpoints.length > 0;

  const recent = [...(events ?? [])].slice(-6).reverse();
  const totalCost = (runs ?? []).reduce((a, r) => a + (r.cost_usd ?? 0), 0);
  const totalCalls = (runs ?? []).reduce((a, r) => a + (r.llm_calls ?? 0), 0);

  return (
    <Card className="overflow-hidden p-0">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 bg-slate-50/60 px-4 py-2.5">
        <span className="text-xs font-bold uppercase tracking-[0.08em] text-slate-500">
          Agent activity
        </span>
        <LivePill
          active={isActive}
          label={
            isActive
              ? `Working now · ${stageLabel(currentStage ?? "")}`
              : isPaused
                ? "Paused · waiting on you"
                : status.status === "COMPLETED"
                  ? "Complete"
                  : "Between stages"
          }
        />
      </div>

      <div className="p-4 sm:p-5">
        {isActive && latestRunning && intel ? (
          <div className="flex items-start gap-3">
            <span className="relative flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-blue-600 text-xl text-white">
              {intel.icon}
              <span className="absolute -right-1 -top-1 h-3 w-3 animate-ping rounded-full bg-blue-400" />
              <span className="absolute -right-1 -top-1 h-3 w-3 rounded-full bg-blue-500 ring-2 ring-white" />
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-[15px] font-bold tracking-tight text-slate-900">
                {latestRunning.agent} agent · {stageLabel(latestRunning.stage)}
                <span className="ml-2 font-mono text-xs font-medium tabular-nums text-blue-700">
                  {elapsedSince(latestRunning.started_at, now)}
                </span>
              </p>
              <p className="mt-0.5 text-[13px] leading-relaxed text-slate-600">{intel.doing}</p>
              <p className="mt-1 text-xs text-slate-400">
                Produces: {intel.outputs}{" "}
                {latestRunning.attempt > 1 && (
                  <span className="font-semibold text-amber-700">· attempt {latestRunning.attempt}</span>
                )}
              </p>
            </div>
          </div>
        ) : isPaused ? (
          <div className="flex items-start gap-3">
            <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-amber-400 text-xl text-white">
              ✋
            </span>
            <div>
              <p className="text-[15px] font-bold tracking-tight text-slate-900">
                Paused — {status.pending_checkpoints.length} decision
                {status.pending_checkpoints.length === 1 ? "" : "s"} waiting
              </p>
              <p className="mt-0.5 text-[13px] text-slate-600">
                {status.pending_checkpoints[0]?.title ?? "Review the checkpoint above to resume."} Agents
                restart automatically the moment you decide.
              </p>
            </div>
          </div>
        ) : (
          <div className="flex items-start gap-3">
            <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-emerald-500 text-xl text-white">
              {status.status === "COMPLETED" ? "✓" : "◷"}
            </span>
            <div>
              <p className="text-[15px] font-bold tracking-tight text-slate-900">
                {status.status === "COMPLETED"
                  ? "Review complete — agents are done."
                  : `Between stages · ${status.status.replaceAll("_", " ")}`}
              </p>
              <p className="mt-0.5 text-[13px] text-slate-600">
                {(runs ?? []).filter((r) => r.status === "succeeded").length} of{" "}
                {(runs ?? []).length} agent runs succeeded
                {totalCalls > 0 && (
                  <>
                    {" "}· {totalCalls} LLM calls · ${totalCost.toFixed(4)}
                  </>
                )}
                . Full per-stage history lives under the Audit tab.
              </p>
            </div>
          </div>
        )}

        {recent.length > 0 && (
          <div className="mt-4 border-t border-slate-100 pt-3">
            <p className="mb-2 text-[11px] font-bold uppercase tracking-[0.08em] text-slate-400">
              Live feed — what just happened
            </p>
            <ol className="space-y-1.5">
              {recent.map((e) => {
                const h = humanAction(e.action);
                return (
                  <li key={e.id} className="flex items-baseline gap-2 text-[13px]">
                    <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-slate-100 text-[11px] text-slate-500">
                      {h.icon}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-slate-700">
                      <strong className="font-semibold text-slate-800">{e.actor}</strong> {h.text}
                      {e.summary && e.action.startsWith("transition_") ? null : e.summary &&
                        !e.action.startsWith("checkpoint_") &&
                        !e.action.startsWith("transition_") ? (
                        <span className="text-slate-400"> — {e.summary.slice(0, 90)}</span>
                      ) : null}
                    </span>
                    <span className="shrink-0 text-[11px] tabular-nums text-slate-400">
                      {timeAgo(e.ts)}
                    </span>
                  </li>
                );
              })}
            </ol>
          </div>
        )}

        {(runs ?? []).length > 0 && (
          <div className="mt-3 flex flex-wrap gap-1.5">
            {(runs ?? []).slice(-8).map((r) => (
              <Badge
                key={r.id}
                tone={r.status === "succeeded" ? "green" : r.status === "running" ? "blue" : r.status === "failed" ? "red" : "slate"}
                dot={r.status === "running"}
              >
                {r.stage} · {r.status}
              </Badge>
            ))}
          </div>
        )}
      </div>
    </Card>
  );
}
