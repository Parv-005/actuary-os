"use client";

import Link from "next/link";
import { Card, EmptyState } from "@/components/ui/primitives";
import type { WorkflowSummary } from "@/lib/api";
import { StatusChip } from "./StatusChip";

function timeAgo(iso: string | null): string {
  if (!iso) return "";
  const s = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export function WorkflowList({ workflows }: { workflows: WorkflowSummary[] }) {
  if (!workflows.length) {
    return (
      <EmptyState
        icon="◈"
        title="No reviews yet"
        message="Create a monthly review from your own CSVs, or run the guided demo on seeded data."
      />
    );
  }
  return (
    <div className="grid gap-3">
      {workflows.map((w) => {
        const needsAction = w.pending_checkpoints > 0;
        return (
          <Link key={w.id} href={`/workflows/${w.id}`} className="block">
            <Card
              className={`card-lift flex items-center justify-between gap-4 p-4 sm:p-5 ${
                needsAction ? "border-l-4 border-l-amber-400" : ""
              }`}
            >
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <span className="text-[15px] font-bold tracking-tight text-slate-900">
                    {w.human_ref}
                  </span>
                  <span className="text-xs text-slate-400">
                    updated {timeAgo(w.updated_at)}
                  </span>
                </div>
                <div className="mt-1 text-sm text-slate-500">
                  {w.portfolio} · {w.period}
                  {needsAction && (
                    <span className="ml-2 inline-flex items-center gap-1 font-semibold text-amber-700">
                      <span className="live-dot bg-amber-500 text-amber-500" />
                      {w.pending_checkpoints} action
                      {w.pending_checkpoints > 1 ? "s" : ""} needed
                    </span>
                  )}
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-3">
                <span className="hidden text-sm font-medium text-slate-400 transition-colors group-hover:text-slate-600 sm:inline">
                  Open →
                </span>
                <StatusChip status={w.status} />
              </div>
            </Card>
          </Link>
        );
      })}
    </div>
  );
}
