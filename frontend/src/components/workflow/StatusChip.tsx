"use client";

import { Badge } from "@/components/ui/primitives";
import { TERMINAL_STATUSES } from "@/lib/api";

export function StatusChip({ status }: { status: string }) {
  let tone: "slate" | "red" | "yellow" | "green" | "blue" = "slate";
  if (status === "COMPLETED") tone = "green";
  else if (status === "WAITING_FOR_HUMAN" || status === "BLOCKED") tone = "red";
  else if (status === "FAILED" || status === "REJECTED") tone = "red";
  else if (TERMINAL_STATUSES.has(status)) tone = "slate";
  else tone = "blue";
  if (status === "FAILED") tone = "red";
  return <Badge tone={tone}>{status.replaceAll("_", " ")}</Badge>;
}
