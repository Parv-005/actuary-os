"use client";

import { useState } from "react";
import Link from "next/link";
import { Badge, Button, Card, EmptyState, Modal, Spinner } from "@/components/ui/primitives";
import { useDecision } from "@/hooks/useDecision";
import type { ReportVersion } from "@/lib/api";

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

const SEV_RAIL: Record<string, string> = {
  high: "bg-red-500",
  medium: "bg-amber-400",
  low: "bg-emerald-500",
};

const REPORT_OPTIONS: Record<string, { icon: string; title: string; blurb: string; next: string; needsNote?: boolean }[]> = {
  final_approval: [
    { icon: "✓", title: "Approve", blurb: "Sign off the QA-verified draft.", next: "Report locks and the workflow completes with a full audit trail." },
    { icon: "✎", title: "Request revision", blurb: "Send it back with a note on what to fix.", next: "Reporting regenerates v+1, then QA re-verifies every number." },
    { icon: "✕", title: "Reject", blurb: "This draft can't go out.", next: "Workflow closes as rejected. A note of ≥20 chars is required.", needsNote: true },
  ],
  qa_failure: [
    { icon: "✎", title: "Request revision", blurb: "Regenerate from the frozen metrics.", next: "Reporting regenerates v+1, then QA re-verifies." },
    { icon: "⇧", title: "Escalate", blurb: "A senior reviewer needs to see this.", next: "Stays parked and flagged as escalated." },
  ],
};

function ReportDecide({
  workflowId,
  reportId,
  reportVersion,
  checkpointType,
  onApplied,
}: {
  workflowId: string;
  reportId: string;
  reportVersion: number;
  checkpointType: "final_approval" | "qa_failure";
  onApplied: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [decision, setDecision] = useState(
    checkpointType === "final_approval" ? "approve" : "request_revision"
  );
  const [rationale, setRationale] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);
  const { submit, submitting, error } = useDecision(workflowId, onApplied);
  const options = REPORT_OPTIONS[checkpointType];
  const active = options.find((o) => o.title.toLowerCase().replaceAll(" ", "_") === decision);
  if (!open) {
    return (
      <Button variant={checkpointType === "final_approval" ? "primary" : "secondary"} onClick={() => setOpen(true)}>
        {checkpointType === "final_approval" ? "✓ Review & sign off (CP-6)" : "⚠ Review QA failure (CP-7)"}
      </Button>
    );
  }
  return (
    <Modal
      title={`${checkpointType === "final_approval" ? "CP-6 · Sign off report v" : "CP-7 · QA failure — report v"}${reportVersion}`}
      subtitle={
        checkpointType === "final_approval"
          ? "QA verified every number. Nothing is final until you approve it."
          : "Number-verify caught a mismatch. Regenerate from frozen metrics or escalate."
      }
      onClose={() => setOpen(false)}
      wide
    >
      <div className="space-y-4">
        <div className="grid gap-2">
          {options.map((o) => {
            const id = o.title.toLowerCase().replaceAll(" ", "_");
            const isActive = id === decision;
            return (
              <button
                key={id}
                type="button"
                onClick={() => setDecision(id)}
                aria-pressed={isActive}
                className={`flex items-start gap-3 rounded-xl border p-3 text-left transition-all ${
                  isActive
                    ? "border-blue-600 bg-blue-50/70 ring-1 ring-blue-600/30"
                    : "border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50"
                }`}
              >
                <span className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-sm font-black ${isActive ? "bg-blue-600 text-white" : "bg-slate-100 text-slate-500"}`}>
                  {o.icon}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-2 text-sm font-bold text-slate-900">
                    {o.title}
                    {o.needsNote && (
                      <span className="rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] font-bold uppercase text-amber-800">note required</span>
                    )}
                  </span>
                  <span className="mt-0.5 block text-xs text-slate-500">{o.blurb}</span>
                </span>
              </button>
            );
          })}
        </div>
        {active && (
          <p className="flex items-start gap-2 rounded-xl bg-slate-50 px-3 py-2.5 text-[13px] text-slate-600 ring-1 ring-inset ring-slate-200/70">
            <span>→</span>
            <span><strong className="text-slate-800">What happens next: </strong>{active.next}</span>
          </p>
        )}
        <div>
          <div className="flex items-baseline justify-between">
            <label className="field-label" htmlFor="report-rationale">
              Rationale {decision === "reject" ? "(required, ≥20 chars)" : "(optional, recommended)"}
            </label>
            <span className={`text-xs tabular-nums ${rationale.trim().length >= 20 ? "font-bold text-emerald-600" : "text-slate-400"}`}>
              {rationale.trim().length}/20
            </span>
          </div>
          <textarea
            id="report-rationale"
            value={rationale}
            onChange={(e) => setRationale(e.target.value)}
            rows={3}
            className="field-input resize-y"
            placeholder={decision === "approve" ? "e.g. Reviewed against QA checklist — figures tie to frozen metrics. Approved." : "e.g. Executive summary overstates the severity driver — rework finding 1 and re-verify."}
          />
        </div>
        {(localError || error) && (
          <p className="rounded-lg bg-red-50 px-3 py-2 text-sm font-medium text-red-700 ring-1 ring-inset ring-red-200" role="alert">
            {localError ?? error}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={() => setOpen(false)}>Cancel</Button>
          <Button
            disabled={submitting}
            onClick={async () => {
              setLocalError(null);
              if (decision === "reject" && rationale.trim().length < 20) {
                setLocalError("A rationale of at least 20 characters is required to reject.");
                return;
              }
              try {
                await submit({ report_id: reportId, decision, rationale });
                setOpen(false);
              } catch {
                /* inline */
              }
            }}
          >
            {submitting ? "Submitting…" : `Confirm — ${decision.replaceAll("_", " ")}`}
          </Button>
        </div>
      </div>
    </Modal>
  );
}

function DocHeading({ n, children }: { n: string; children: React.ReactNode }) {
  return (
    <h3 className="flex items-baseline gap-2.5 text-[15px] font-black tracking-tight text-slate-900">
      <span className="font-mono text-xs font-bold text-slate-300">{n}</span>
      {children}
    </h3>
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
  if (!report)
    return (
      <EmptyState
        icon="📄"
        title="No report draft yet"
        message="The reporting agent drafts it after investigation; QA verifies every number before you see it."
      />
    );
  const s = asSections(report.sections ?? {});
  const qaPassed = report.qa_result != null && (report.qa_result as { passed?: boolean }).passed === true;
  const approved = report.status === "approved";
  return (
    <div className="space-y-4">
      <Card
        className={`flex flex-wrap items-center justify-between gap-3 p-4 sm:p-5 ${
          approved ? "border-emerald-300 bg-emerald-50/50" : ""
        }`}
      >
        <div className="flex min-w-0 items-center gap-3">
          <span
            aria-hidden="true"
            className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-xl text-lg font-black ${
              approved ? "bg-emerald-500 text-white" : "bg-ink-900 text-white"
            }`}
          >
            {approved ? "✓" : "§"}
          </span>
          <div className="min-w-0 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-[15px] font-black tracking-tight text-slate-900">
                {approved ? "Approved report" : "Draft — Awaiting Actuary Approval"}
              </span>
              <Badge tone={approved ? "green" : qaPassed ? "blue" : "yellow"} dot>
                {report.status.replaceAll("_", " ")}
              </Badge>
              {qaPassed && (
                <Badge tone="green" dot>
                  QA ✓ every number verified
                </Badge>
              )}
            </div>
            <div className="mt-0.5 text-xs text-slate-500">
              {approved && report.approved_by
                ? `signed off by ${report.approved_by} · ${report.approved_at ?? ""}`
                : "Nothing here is final until you approve it."}
            </div>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label className="text-xs font-semibold text-slate-500" htmlFor="report-version">
            Version
          </label>
          <select
            id="report-version"
            value={report.version}
            onChange={(e) => onVersion(Number(e.target.value))}
            className="field-input w-auto py-1.5 text-sm"
          >
            {history.map((h) => (
              <option key={h.version} value={h.version}>
                v{h.version} ({h.status})
              </option>
            ))}
          </select>
          {pendingReview && !approved && (
            <ReportDecide
              workflowId={workflowId}
              reportId={report.id}
              reportVersion={report.version}
              checkpointType={pendingReview}
              onApplied={onApplied}
            />
          )}
        </div>
      </Card>

      <Card className="space-y-5 p-5 sm:p-7">
        <section>
          <DocHeading n="01">Executive summary</DocHeading>
          <p className="mt-2 max-w-3xl whitespace-pre-wrap text-[15px] leading-relaxed text-slate-800">
            {s.executive_summary ?? "—"}
          </p>
        </section>

        <section>
          <DocHeading n="02">Key metrics</DocHeading>
          <div className="mt-2 overflow-x-auto">
            <table className="data-table min-w-[520px]">
              <thead>
                <tr>
                  <th>Metric</th>
                  <th>Value</th>
                  <th>Prev</th>
                  <th>Δ</th>
                </tr>
              </thead>
              <tbody>
                {(s.key_metrics ?? []).map((m, i) => (
                  <tr key={i} className="transition-colors hover:bg-blue-50/40">
                    <td className="font-semibold text-slate-800">
                      {m.metric_key}
                      {m.dimensions && Object.keys(m.dimensions).length > 0 && (
                        <span className="font-normal text-slate-500">
                          {" "}
                          ({Object.values(m.dimensions).join(" · ")})
                        </span>
                      )}
                    </td>
                    <td className="font-bold tabular-nums">{String(m.value ?? "—")}</td>
                    <td className="tabular-nums text-slate-500">{String(m.prev_value ?? "—")}</td>
                    <td className="tabular-nums text-slate-600">{String(m.delta_pp ?? "—")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section>
          <DocHeading n="03">Findings</DocHeading>
          <div className="mt-2 space-y-2">
            {(s.findings ?? []).map((f, i) => (
              <div
                key={i}
                className="flex gap-3 overflow-hidden rounded-xl bg-slate-50 p-3.5 ring-1 ring-inset ring-slate-200/60"
              >
                <div className={`w-1 shrink-0 rounded-full ${SEV_RAIL[f.severity] ?? "bg-slate-300"}`} />
                <div className="min-w-0 text-sm">
                  <div className="font-bold tracking-tight text-slate-900">{f.title}</div>
                  {f.narrative && (
                    <p className="mt-1 whitespace-pre-wrap leading-relaxed text-slate-700">
                      {f.narrative}
                    </p>
                  )}
                </div>
              </div>
            ))}
            {!(s.findings ?? []).length && (
              <p className="text-sm text-slate-500">No findings in this version.</p>
            )}
          </div>
        </section>

        <section>
          <DocHeading n="04">Exceptions</DocHeading>
          <div className="mt-2 space-y-1.5">
            {(s.exceptions ?? []).map((e, i) => (
              <div key={i} className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-sm">
                <Badge tone={e.status === "ACCEPTED_EXCEPTION" ? "slate" : "yellow"} dot>
                  {e.status ?? "flag"}
                </Badge>
                <span className="font-semibold text-slate-800">{e.name ?? e.check_id}</span>
                <span className="text-slate-600">— {e.message}</span>
                {e.rationale && (
                  <span className="w-full text-[13px] italic text-slate-500">
                    “{e.rationale}”
                  </span>
                )}
              </div>
            ))}
            {!(s.exceptions ?? []).length && (
              <p className="text-sm text-slate-500">None.</p>
            )}
          </div>
        </section>

        <div className="grid gap-5 border-t border-slate-100 pt-5 md:grid-cols-2">
          <section>
            <DocHeading n="05">Decisions</DocHeading>
            <div className="mt-2 space-y-1.5">
              {(s.decisions ?? []).map((d, i) => (
                <div key={i} className="text-sm">
                  <span className="font-semibold capitalize text-slate-800">
                    {d.decision?.replaceAll("_", " ")}
                  </span>
                  {d.rationale && <span className="text-slate-600"> — {d.rationale}</span>}
                </div>
              ))}
              {!(s.decisions ?? []).length && (
                <p className="text-sm text-slate-500">None recorded yet.</p>
              )}
            </div>
          </section>
          <section>
            <DocHeading n="06">Open questions</DocHeading>
            <ul className="mt-2 list-disc space-y-1 pl-5 text-sm leading-relaxed text-slate-700">
              {(s.open_questions ?? []).map((q, i) => (
                <li key={i}>{q}</li>
              ))}
            </ul>
            {!(s.open_questions ?? []).length && (
              <p className="text-sm text-slate-500">None.</p>
            )}
          </section>
        </div>

        {(s.citations ?? []).length > 0 && (
          <p className="border-t border-slate-100 pt-4 text-[13px] text-slate-500">
            <span className="font-semibold text-slate-700">Methodology cited: </span>
            {(s.citations ?? []).map((c, i) => (
              <span key={i} className="mr-2 rounded bg-slate-100 px-1.5 py-0.5">
                {c.title} ({c.version})
              </span>
            ))}
          </p>
        )}
      </Card>

      <p className="text-xs text-slate-500">
        <Link className="font-medium text-blue-700 underline-offset-2 hover:underline" href={`/workflows/${workflowId}`}>
          Full finding drill-downs live under the Findings tab →
        </Link>
      </p>
    </div>
  );
}
