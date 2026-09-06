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

/** Pipeline progress: ✓ done · ● active · ○ pending · ✕ failed (§11). */
export function StageTracker({ stages }: { stages: StageStatus[] }) {
  const byStage = new Map(stages.map((s) => [s.stage, s]));
  return (
    <ol className="flex flex-wrap items-center gap-1 text-sm">
      {ORDER.map((stage, i) => {
        const s = byStage.get(stage);
        const state = s?.state;
        const done = state === "succeeded";
        const failed = state === "failed";
        const active =
          state === "running" || (!done && !failed && i === firstOpen(byStage));
        return (
          <li key={stage} className="flex items-center gap-1">
            {i > 0 && <span className="mx-1 text-slate-300">→</span>}
            <span
              title={
                s
                  ? `${s.agent} · ${s.state} · attempt ${s.attempt}${
                      s.error ? ` · ${s.error}` : ""
                    }`
                  : "not started"
              }
              className={`rounded-full px-3 py-1 font-medium ${
                done
                  ? "bg-green-100 text-green-800"
                  : failed
                    ? "bg-red-100 text-red-800"
                    : active
                      ? "bg-blue-100 text-blue-900"
                      : "bg-slate-100 text-slate-400"
              }`}
            >
              {done ? "✓ " : failed ? "✕ " : active ? "● " : "○ "}
              {stageLabel(stage)}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

function firstOpen(byStage: Map<string, StageStatus>): number {
  const order = [
    "intake",
    "data_prep",
    "validation",
    "analysis",
    "insight",
    "knowledge",
    "reporting",
    "qa",
  ];
  for (let i = 0; i < order.length; i++) {
    if (byStage.get(order[i])?.state !== "succeeded") return i;
  }
  return order.length;
}
