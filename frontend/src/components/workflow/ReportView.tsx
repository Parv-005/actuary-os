"use client";

import { useState } from "react";
import Link from "next/link";
import { Badge, Button, Card, EmptyState, Spinner } from "@/components/ui/primitives";
import { useDecision } from "@/hooks/useDecision";
import type { ReportVersion } from "@/lib/api";
import { severityIcon } from "./FindingsList";

interface Sections {
  executive_summary?: string;
  key_metrics?: { metric_key: string; dimensions?: Record<string, string>; value?: unknown; prev_value?: unknown; delta_pp?: unknown }[];
  findings?: { title: string; severity: string; confidence?: number; evidence_count?: number; narrative?: string; decision_question?: string | null }[];
  exceptions?: { check_id?: string; name?: string; status?: string; message?: string; rationale?: string }[];
  decisions?: { decision?: string; rationale?: string; checkpoint?: string; decided_at?: string }[];
  open_questions?: string[];
  citations?: { title?: string; version?: string }[];
}

function asSections(s: Record<string, unknown>): Sections {
  return s as unknown as Sections;
}

function ReportDecide({
  workflowId,
  reportId,
  checkpointType,
  onApplied,
}: {
  workflowId: string;
  reportId: string;
  checkpointType: "final_approval" | "qa_failure";
  onApplied: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [decision, setDecision] = useState(
    checkpointType === "final_approval" ? "approve" : "request_revision"
  );
  const [rationale, setRationale] = useState("");
  const { submit, submitting, error } = useDecision(workflowId, onApplied);
  if (!open) {
    return (
      <Button variant="secondary" onClick={() => setOpen(true)}>
        Review (CP-{checkpointType === "final_approval" ? "6" : "7"})
      </Button>
    );
  }
  const options =
    checkpointType === "final_approval"
      ? ["approve", "request_revision", "reject"]
      : ["request_revision", "escalate"];
  return (
    <div className="flex flex-wrap items-center gap-2">
      <select
        value={decision}
        onChange={(e) => setDecision(e.target.value)}
        className="rounded border border-slate-300 px-2 py-1.5 text-sm"
      >
        {options.map((o) => (
          <option key={o} value={o}>
            {o.replaceAll("_", " ")}
          </option>
        ))}
      </select>
      <input
        value={rationale}
        onChange={(e) => setRationale(e.target.value)}
        placeholder="Rationale / comment (≥20 chars for reject)"
        className="min-w-64 flex-1 rounded border border-slate-300 px-2 py-1.5 text-sm"
      />
      <Button
        disabled={submitting}
        onClick={async () => {
          try {
            await submit({ report_id: reportId, decision, rationale });
            setOpen(false);
          } catch {
            /* inline below */
          }
        }}
      >
        {submitting ? "…" : "Submit"}
      </Button>
      <Button variant="ghost" onClick={() => setOpen(false)}>
        Cancel
      </Button>
      {error && (
        <span className="text-sm text-red-700" role="alert">
          {error}
        </span>
      )}
    </div>
  );
}

export function ReportView({
  workflowId,
  report,
  history,
  loading,
  pendingReview,
  onApplied,
  onVersion,
}: {
  workflowId: string;
  report: ReportVersion | null;
  history: { version: number; status: string; generated_at: string | null }[];
  loading: boolean;
  pendingReview: "final_approval" | "qa_failure" | null;
  onApplied: () => void;
  onVersion: (v: number) => void;
}) {
  if (loading) return <Spinner label="Loading report…" />;
  if (!report) return <EmptyState message="No report draft yet — it appears after QA." />;
  const s = asSections(report.sections ?? {});
  const qaPassed = report.qa_result != null && (report.qa_result as { passed?: boolean }).passed === true;
  return (
    <div className="space-y-4">
      <Card className="flex flex-wrap items-center justify-between gap-2 p-4">
        <div className="flex items-center gap-2 text-sm">
          <span className="font-medium">Draft — Awaiting Actuary Approval</span>
          <Badge tone={report.status === "approved" ? "green" : qaPassed ? "blue" : "yellow"}>
            {report.status.replaceAll("_", " ")}
          </Badge>
          {qaPassed && <Badge tone="green">QA ✓</Badge>}
          {report.status === "approved" && (
            <span className="text-slate-500">
              by {report.approved_by} · {report.approved_at}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <label className="text-xs text-slate-500">Version</label>
          <select
            value={report.version}
            onChange={(e) => onVersion(Number(e.target.value))}
            className="rounded border border-slate-300 px-2 py-1 text-sm"
          >
            {history.map((h) => (
              <option key={h.version} value={h.version}>
                v{h.version} ({h.status})
              </option>
            ))}
          </select>
          {pendingReview && report.status !== "approved" && (
            <ReportDecide
              workflowId={workflowId}
              reportId={report.id}
              checkpointType={pendingReview}
              onApplied={onApplied}
            />
          )}
        </div>
      </Card>

      <Card className="p-4">
        <h3 className="mb-1 font-medium">Executive summary</h3>
        <p className="whitespace-pre-wrap text-sm">{s.executive_summary ?? "—"}</p>
      </Card>

      <Card className="overflow-hidden">
        <h3 className="p-4 pb-0 font-medium">Key metrics</h3>
        <table className="mt-2 w-full text-left text-sm">
          <thead className="bg-slate-50 text-xs uppercase text-slate-500">
            <tr>
              <th className="px-4 py-2">Metric</th>
              <th className="px-4 py-2">Value</th>
              <th className="px-4 py-2">Prev</th>
              <th className="px-4 py-2">Δ</th>
            </tr>
          </thead>
          <tbody>
            {(s.key_metrics ?? []).map((m, i) => (
              <tr key={i} className="border-t border-slate-100">
                <td className="px-4 py-2">
                  {m.metric_key}
                  {m.dimensions && Object.keys(m.dimensions).length > 0 && (
                    <span className="text-slate-500">
                      {" "}
                      ({Object.values(m.dimensions).join(" · ")})
                    </span>
                  )}
                </td>
                <td className="px-4 py-2">{String(m.value ?? "—")}</td>
                <td className="px-4 py-2">{String(m.prev_value ?? "—")}</td>
                <td className="px-4 py-2">{String(m.delta_pp ?? "—")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      <Card className="p-4">
        <h3 className="mb-2 font-medium">Findings</h3>
        <div className="space-y-2">
          {(s.findings ?? []).map((f, i) => (
            <div key={i} className="rounded bg-slate-50 p-3 text-sm">
              <div className="font-medium">
                {severityIcon(f.severity)} {f.title}
              </div>
              {f.narrative && (
                <p className="mt-1 whitespace-pre-wrap text-slate-700">{f.narrative}</p>
              )}
            </div>
          ))}
          {!(s.findings ?? []).length && (
            <p className="text-sm text-slate-500">No findings in this version.</p>
          )}
        </div>
      </Card>

      <Card className="p-4">
        <h3 className="mb-2 font-medium">Exceptions</h3>
        {(s.exceptions ?? []).map((e, i) => (
          <div key={i} className="mb-1 text-sm">
            <Badge tone={e.status === "ACCEPTED_EXCEPTION" ? "slate" : "yellow"}>
              {e.status ?? "flag"}
            </Badge>{" "}
            <span className="font-medium">{e.name ?? e.check_id}</span> — {e.message}
            {e.rationale && (
              <span className="text-slate-600"> (rationale: {e.rationale})</span>
            )}
          </div>
        ))}
        {!(s.exceptions ?? []).length && (
          <p className="text-sm text-slate-500">None.</p>
        )}
      </Card>

      <div className="grid gap-4 md:grid-cols-2">
        <Card className="p-4">
          <h3 className="mb-2 font-medium">Decisions</h3>
          {(s.decisions ?? []).map((d, i) => (
            <div key={i} className="mb-1 text-sm">
              <span className="font-medium">{d.decision?.replaceAll("_", " ")}</span>
              {d.rationale && <span className="text-slate-600"> — {d.rationale}</span>}
            </div>
          ))}
          {!(s.decisions ?? []).length && (
            <p className="text-sm text-slate-500">None recorded yet.</p>
          )}
        </Card>
        <Card className="p-4">
          <h3 className="mb-2 font-medium">Open questions</h3>
          <ul className="list-disc space-y-1 pl-5 text-sm">
            {(s.open_questions ?? []).map((q, i) => (
              <li key={i}>{q}</li>
            ))}
          </ul>
          {!(s.open_questions ?? []).length && (
            <p className="text-sm text-slate-500">None.</p>
          )}
        </Card>
      </div>

      {(s.citations ?? []).length > 0 && (
        <Card className="p-4 text-sm">
          <span className="font-medium">Methodology cited: </span>
          {(s.citations ?? []).map((c, i) => (
            <span key={i} className="mr-2">
              {c.title} ({c.version})
            </span>
          ))}
        </Card>
      )}

      <p className="text-xs text-slate-500">
        <Link className="underline" href={`/workflows/${workflowId}`}>
          Full finding drill-downs live under the Findings tab.
        </Link>
      </p>
    </div>
  );
}
