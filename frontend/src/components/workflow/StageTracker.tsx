"use client";

import { stageLabel, type StageStatus } from "@/lib/api";

const ORDER = [
  "intake",
  "data_prep",
  "validation",
  "analysis",
  "insight",
  "knowledge",
  "reporting",
  "qa",
];

type NodeState = "done" | "active" | "failed" | "todo";

/** Connected pipeline stepper: numbered nodes, progress rail, live pulse. */
export function StageTracker({ stages }: { stages: StageStatus[] }) {
  const byStage = new Map(stages.map((s) => [s.stage, s]));
  const activeIdx = firstOpen(byStage);
  const doneCount = ORDER.filter((st) => byStage.get(st)?.state === "succeeded").length;
  const pct = Math.round((doneCount / ORDER.length) * 100);

  return (
    <div className="rounded-xl border border-slate-200/80 bg-white p-4 shadow-card sm:p-5">
      <div className="mb-1 flex items-center justify-between gap-2">
        <span className="text-xs font-bold uppercase tracking-[0.08em] text-slate-500">
          Pipeline
        </span>
        <span className="text-xs font-medium tabular-nums text-slate-400">
          {doneCount} of {ORDER.length} stages · {pct}%
        </span>
      </div>
      <div className="mb-3 h-1.5 overflow-hidden rounded-full bg-slate-100">
        <div
          className="h-full rounded-full bg-gradient-to-r from-blue-600 to-emerald-500 transition-all"
          style={{ width: `${pct}%` }}
        />
      </div>
      <ol className="flex items-start">
        {ORDER.map((stage, i) => {
          const s = byStage.get(stage);
          const state: NodeState = s?.state === "succeeded"
            ? "done"
            : s?.state === "failed"
              ? "failed"
              : s?.state === "running" || i === activeIdx
                ? "active"
                : "todo";
          const last = i === ORDER.length - 1;
          return (
            <li key={stage} className={`flex items-start ${last ? "" : "flex-1"}`}>
              <div className="flex flex-col items-center gap-1.5">
                <span
                  title={
                    s
                      ? `${s.agent} · ${s.state} · attempt ${s.attempt}${
                          s.error ? ` · ${s.error}` : ""
                        }`
                      : "not started"
                  }
                  className={`flex h-7 w-7 items-center justify-center rounded-full text-xs font-black ring-2 transition-all ${
                    state === "done"
                      ? "bg-emerald-500 text-white ring-emerald-200"
                      : state === "failed"
                        ? "bg-red-500 text-white ring-red-200"
                        : state === "active"
                          ? "bg-blue-600 text-white ring-blue-200"
                          : "bg-slate-100 text-slate-400 ring-slate-200"
                  }`}
                >
                  {state === "done" ? (
                    "✓"
                  ) : state === "failed" ? (
                    "✕"
                  ) : state === "active" ? (
                    <span className="live-dot bg-white text-white" />
                  ) : (
                    i + 1
                  )}
                </span>
                <span
                  className={`whitespace-nowrap text-[11px] font-semibold ${
                    state === "todo" ? "text-slate-400" : "text-slate-700"
                  }`}
                >
                  {stageLabel(stage)}
                </span>
                {s && (state === "done" || state === "failed" || state === "active") && (
                  <span className="whitespace-nowrap text-[10px] tabular-nums text-slate-400">
                    {state === "active"
                      ? s.state === "running"
                        ? "working…"
                        : "up next"
                      : `${(s.duration_ms / 1000).toFixed(1)}s${s.attempt > 1 ? ` · try ${s.attempt}` : ""}`}
                  </span>
                )}
              </div>
              {!last && (
                <div className="mx-1 mt-3.5 h-0.5 min-w-2 flex-1 rounded-full bg-slate-200">
                  <div
                    className={`h-full rounded-full transition-all ${
                      byStage.get(stage)?.state === "succeeded"
                        ? "bg-emerald-400"
                        : "bg-slate-200"
                    }`}
                    style={{
                      width: byStage.get(stage)?.state === "succeeded" ? "100%" : "0%",
                    }}
                  />
                </div>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function firstOpen(byStage: Map<string, StageStatus>): number {
  for (let i = 0; i < ORDER.length; i++) {
    if (byStage.get(ORDER[i])?.state !== "succeeded") return i;
  }
  return ORDER.length;
}
