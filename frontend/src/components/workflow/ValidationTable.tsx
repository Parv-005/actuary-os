"use client";

import { Fragment, useState } from "react";
import { Badge, Card, EmptyState, Spinner } from "@/components/ui/primitives";
import type { ValidationResult } from "@/lib/api";

function statusTone(status: string): "red" | "yellow" | "green" | "slate" {
  if (status === "BLOCKER") return "red";
  if (status === "WARNING") return "yellow";
  if (status === "PASS") return "green";
  if (status === "ACCEPTED_EXCEPTION") return "slate";
  return "slate";
}

export function ValidationTable({
  results,
  loading,
}: {
  results: ValidationResult[] | null;
  loading: boolean;
}) {
  const [open, setOpen] = useState<string | null>(null);
  if (loading) return <Spinner label="Loading validation checks…" />;
  if (!results || !results.length)
    return <EmptyState message="No validation results yet." />;
  return (
    <Card className="overflow-hidden">
      <table className="w-full text-left text-sm">
        <thead className="bg-slate-50 text-xs uppercase text-slate-500">
          <tr>
            <th className="px-4 py-2">Check</th>
            <th className="px-4 py-2">Category</th>
            <th className="px-4 py-2">Severity</th>
            <th className="px-4 py-2">Status</th>
            <th className="px-4 py-2">Message</th>
          </tr>
        </thead>
        <tbody>
          {results.map((r) => (
            <Fragment key={r.check_id}>
              <tr
                className="cursor-pointer border-t border-slate-100 hover:bg-slate-50"
                onClick={() => setOpen(open === r.check_id ? null : r.check_id)}
              >
                <td className="px-4 py-2 font-medium">{r.name || r.check_id}</td>
                <td className="px-4 py-2 text-slate-600">{r.category}</td>
                <td className="px-4 py-2">
                  <Badge
                    tone={
                      r.severity === "ERROR"
                        ? "red"
                        : r.severity === "WARN"
                          ? "yellow"
                          : "slate"
                    }
                  >
                    {r.severity}
                  </Badge>
                </td>
                <td className="px-4 py-2">
                  <Badge tone={statusTone(r.status)}>{r.status}</Badge>
                </td>
                <td className="px-4 py-2 text-slate-700">{r.message}</td>
              </tr>
              {open === r.check_id && (
                <tr className="border-t border-slate-100 bg-slate-50">
                  <td colSpan={5} className="px-4 py-2 text-xs text-slate-600">
                    <pre className="whitespace-pre-wrap">
                      {JSON.stringify(
                        { details: r.details, resolution: r.resolution ?? null },
                        null,
                        2
                      )}
                    </pre>
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
    </Card>
  );
}
