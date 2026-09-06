"use client";

import Link from "next/link";
import { Card } from "@/components/ui/primitives";
import type { WorkflowSummary } from "@/lib/api";
import { StatusChip } from "./StatusChip";

export function WorkflowList({ workflows }: { workflows: WorkflowSummary[] }) {
  if (!workflows.length) {
    return (
      <Card className="p-6 text-center text-sm text-slate-500">
        No reviews yet — create one or start the guided demo.
      </Card>
    );
  }
  return (
    <div className="grid gap-3">
      {workflows.map((w) => (
        <Link key={w.id} href={`/workflows/${w.id}`}>
          <Card className="flex items-center justify-between p-4 transition-colors hover:border-slate-400">
            <div>
              <div className="font-medium">{w.human_ref}</div>
              <div className="text-sm text-slate-500">
                {w.portfolio} · {w.period}
                {w.pending_checkpoints > 0 && (
                  <span className="ml-2 font-medium text-red-700">
                    ● {w.pending_checkpoints} action
                    {w.pending_checkpoints > 1 ? "s" : ""} needed
                  </span>
                )}
              </div>
            </div>
            <StatusChip status={w.status} />
          </Card>
        </Link>
      ))}
    </div>
  );
}
