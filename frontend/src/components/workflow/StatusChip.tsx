"use client";

import { Badge, type BadgeTone } from "@/components/ui/primitives";
import { TERMINAL_STATUSES } from "@/lib/api";

const DOT: Record<BadgeTone, boolean> = {
  slate: false,
  red: true,
  yellow: true,
  green: true,
  blue: true,
  navy: false,
};

export function StatusChip({ status }: { status: string }) {
  let tone: BadgeTone = "slate";
  if (status === "COMPLETED") tone = "green";
  else if (status === "WAITING_FOR_HUMAN" || status === "BLOCKED") tone = "yellow";
  else if (status === "FAILED" || status === "REJECTED") tone = "red";
  else if (TERMINAL_STATUSES.has(status)) tone = "slate";
  else tone = "blue";
  return (
    <Badge tone={tone} dot={DOT[tone]}>
      {status.replaceAll("_", " ")}
    </Badge>
  );
}
