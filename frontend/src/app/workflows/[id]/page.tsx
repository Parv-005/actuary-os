"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Button, Card, LivePill, SkeletonCard } from "@/components/ui/primitives";
import { AgentRunTable, AuditTimeline } from "@/components/workflow/AuditViews";
import { AgentActivity } from "@/components/workflow/AgentActivity";
import { ChartsPanel } from "@/components/workflow/ChartsPanel";
import { CheckpointBanner } from "@/components/workflow/CheckpointPanel";
import { DemoGuide } from "@/components/workflow/DemoGuide";
import { FindingsList } from "@/components/workflow/FindingsList";
import { KpiCards } from "@/components/workflow/KpiCards";
import { ReportView } from "@/components/workflow/ReportView";
import { StageTracker } from "@/components/workflow/StageTracker";
import { StatusChip } from "@/components/workflow/StatusChip";
import { UploadZone } from "@/components/workflow/UploadZone";
import { ValidationTable } from "@/components/workflow/ValidationTable";
import { useWorkflowPolling } from "@/hooks/useWorkflowPolling";
import {
  api,
  type AgentRunRow,
  type AuditEvent,
  type FindingSummary,
  type MetricRow,
  type ReportVersion,
  type ValidationResult,
  type WorkflowStatus,
} from "@/lib/api";

type Tab = "overview" | "validation" | "findings" | "report" | "audit";

function pendingReviewType(
  status: WorkflowStatus
): "final_approval" | "qa_failure" | null {
  const types = status.pending_checkpoints.map((c) => c.type);
  if (types.includes("final_approval")) return "final_approval";
  if (types.includes("qa_failure")) return "qa_failure";
  return null;
}

function RetryCard({ workflowId, error }: { workflowId: string; error: unknown }) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  return (
    <Card className="animate-banner-in border-l-4 border-l-red-500 p-4 text-sm sm:p-5">
      <div className="flex items-center gap-2.5">
        <span className="flex h-9 w-9 items-center justify-center rounded-full bg-red-100 text-base font-black text-red-700">
          ✕
        </span>
        <div>
          <div className="text-[15px] font-black tracking-tight text-slate-900">
            Workflow failed — nothing is lost
          </div>
          <p className="text-xs text-slate-500">
            State is checkpointed; retry resumes from the failed stage only.
          </p>
        </div>
      </div>
      <pre className="nice-scroll mt-3 max-h-32 overflow-auto whitespace-pre-wrap rounded-xl bg-ink-950 p-3 font-mono text-[11px] leading-relaxed text-red-200">
        {JSON.stringify(error ?? "unknown error", null, 2)}
      </pre>
      {msg && (
        <p className="mt-2 text-sm font-medium text-red-700" role="alert">
          {msg}
        </p>
      )}
      <div className="mt-3">
        <Button
          variant="danger"
          onClick={async () => {
            setBusy(true);
            try {
              await api.resumeWorkflow(workflowId);
              window.location.reload();
            } catch (e) {
              setMsg(e instanceof Error ? e.message : "retry failed");
              setBusy(false);
            }
          }}
          disabled={busy}
        >
          {busy ? "Retrying…" : "↻ Retry from failed stage"}
        </Button>
      </div>
    </Card>
  );
}

function InputsCard({ workflowId, onStarted }: { workflowId: string; onStarted: () => void }) {
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <Card className="space-y-4 p-4 sm:p-5">
      <div className="flex items-center gap-2.5">
        <span className="flex h-9 w-9 items-center justify-center rounded-full bg-blue-100 text-base font-black text-blue-700">
          ⭳
        </span>
        <div>
          <h3 className="text-[15px] font-black tracking-tight text-slate-900">
            Inputs needed
          </h3>
          <p className="text-xs text-slate-500">
            Attach CSVs, then start — intake classifies and fires checkpoints.
          </p>
        </div>
      </div>
      <UploadZone onFiles={setFiles} disabled={busy} />
      {error && (
        <p
          className="rounded-lg bg-red-50 px-3 py-2 text-sm font-medium text-red-700 ring-1 ring-inset ring-red-200"
          role="alert"
        >
          {error}
        </p>
      )}
      <div className="flex justify-end gap-2">
        <Button
          variant="secondary"
          disabled={busy || !files.length}
          onClick={async () => {
            setBusy(true);
            try {
              await api.uploadFiles(workflowId, files);
              setFiles([]);
              onStarted();
            } catch (e) {
              setError(e instanceof Error ? e.message : "upload failed");
            } finally {
              setBusy(false);
            }
          }}
        >
          Upload
        </Button>
        <Button
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            try {
              await api.startWorkflow(workflowId);
              onStarted();
            } catch (e) {
              setError(e instanceof Error ? e.message : "start failed");
            } finally {
              setBusy(false);
            }
          }}
        >
          Start
        </Button>
      </div>
    </Card>
  );
}

export default function WorkflowDetailPage({
  params,
}: {
  params: { id: string };
}) {
  const id = params.id;
  const { status, error, polling, refresh, restart } = useWorkflowPolling(id);
  const [tab, setTab] = useState<Tab>("overview");
  const [metrics, setMetrics] = useState<MetricRow[] | null>(null);
  const [metricsLoading, setMetricsLoading] = useState(true);
  const [validation, setValidation] = useState<ValidationResult[] | null>(null);
  const [validationLoading, setValidationLoading] = useState(true);
  const [findings, setFindings] = useState<FindingSummary[] | null>(null);
  const [findingsLoading, setFindingsLoading] = useState(true);
  const [report, setReport] = useState<ReportVersion | null>(null);
  const [reportHistory, setReportHistory] = useState<
    { version: number; status: string; generated_at: string | null }[]
  >([]);
  const [reportLoading, setReportLoading] = useState(true);
  const [reportVersion, setReportVersion] = useState<number | undefined>(undefined);
  const [audit, setAudit] = useState<AuditEvent[] | null>(null);
  const [auditLoading, setAuditLoading] = useState(true);
  const [runs, setRuns] = useState<AgentRunRow[] | null>(null);
  const [runsLoading, setRunsLoading] = useState(true);

  const loadSecondary = useCallback(async () => {
    try {
      const m = await api.getMetrics(id);
      setMetrics(m.metrics);
    } catch {
      setMetrics([]);
    } finally {
      setMetricsLoading(false);
    }
    try {
      const v = await api.getValidation(id);
      setValidation(v.results);
    } catch {
      setValidation([]);
    } finally {
      setValidationLoading(false);
    }
    try {
      const f = await api.getFindings(id);
      setFindings(f.findings);
    } catch {
      setFindings([]);
    } finally {
      setFindingsLoading(false);
    }
    try {
      const r = await api.getReport(id, reportVersion);
      setReport(r.report);
      setReportHistory(r.history);
    } catch {
      setReport(null);
      setReportHistory([]);
    } finally {
      setReportLoading(false);
    }
    try {
      const a = await api.getAuditLog(id, { limit: 200 });
      setAudit(a.events);
    } catch {
      setAudit([]);
    } finally {
      setAuditLoading(false);
    }
    try {
      const ar = await api.getAgentRuns(id);
      setRuns(ar.runs);
    } catch {
      setRuns([]);
    } finally {
      setRunsLoading(false);
    }
  }, [id, reportVersion]);

  useEffect(() => {
    void loadSecondary();
  }, [loadSecondary]);

  // refresh derived panels whenever the pipeline state advances
  useEffect(() => {
    if (status) void loadSecondary();
  }, [status?.status, loadSecondary]); // eslint-disable-line react-hooks/exhaustive-deps

  const onApplied = useCallback(() => {
    restart();
    void loadSecondary();
  }, [restart, loadSecondary]);

  return (
    <main className="mx-auto max-w-6xl space-y-4 px-4 py-6 sm:px-8 sm:py-8">
      <Link
        href="/"
        className="inline-flex items-center gap-1 text-sm font-medium text-slate-500 transition-colors hover:text-slate-900"
      >
        ← Dashboard
      </Link>

      {!status && !error && (
        <div className="space-y-3">
          <SkeletonCard lines={2} />
          <SkeletonCard lines={4} />
        </div>
      )}
      {error && !status && (
        <Card className="border-red-300 bg-red-50 p-4 text-sm text-red-800">
          {error}
        </Card>
      )}

      {status && (
        <>
          <div className="animate-fade-up rounded-2xl bg-ink-950 px-5 py-4 text-white shadow-pop sm:px-6">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2.5">
                  <h1 className="text-xl font-black tracking-tight sm:text-2xl">
                    {status.human_ref}
                  </h1>
                  <StatusChip status={status.status} />
                </div>
                <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px] text-slate-400">
                  <span>
                    stage: <span className="font-semibold text-slate-200">{status.stage ?? "—"}</span>
                  </span>
                  <LivePill
                    active={polling}
                    label={polling ? "Live · updating every 2s" : status.status === "COMPLETED" ? "Complete" : "Paused · action required"}
                  />
                </p>
              </div>
              <div className="flex items-center gap-2">
                <DemoGuide workflowId={id} />
              </div>
            </div>
          </div>

          <StageTracker stages={status.stage_statuses} />

          <AgentActivity status={status} runs={runs} events={audit} polling={polling} />

          <CheckpointBanner
            workflowId={id}
            checkpoints={status.pending_checkpoints}
            onApplied={onApplied}
          />

          {status.status === "FAILED" && (
            <RetryCard workflowId={id} error={status.error} />
          )}

          {(status.status === "INPUT_WAIT" || status.status === "CREATED") && (
            <InputsCard workflowId={id} onStarted={restart} />
          )}

          <nav className="sticky top-14 z-30 -mx-1 flex gap-1 overflow-x-auto bg-[#eef1f6]/95 px-1 py-1 backdrop-blur" aria-label="Workflow sections">
            {(["overview", "validation", "findings", "report", "audit"] as Tab[]).map((t) => {
              const active = tab === t;
              const count =
                t === "findings" && findings?.length ? ` (${findings.length})` : "";
              return (
                <button
                  key={t}
                  onClick={() => setTab(t)}
                  aria-current={active ? "page" : undefined}
                  className={`whitespace-nowrap rounded-lg px-4 py-2 text-sm font-bold capitalize transition-all ${
                    active
                      ? "bg-ink-900 text-white shadow-sm"
                      : "text-slate-500 hover:bg-white hover:text-slate-800"
                  }`}
                >
                  {t}
                  {count}
                </button>
              );
            })}
          </nav>

          {tab === "overview" && (
            <div className="space-y-4">
              <KpiCards metrics={metrics} loading={metricsLoading} />
              <ChartsPanel workflowId={id} validation={validation} />
              <Card className="flex flex-wrap items-center justify-between gap-2 p-4 text-sm text-slate-600">
                <span className="flex items-center gap-2">
                  <span className={`live-dot ${polling ? "bg-emerald-500 text-emerald-500" : "bg-slate-300 text-slate-300"}`} />
                  {polling
                    ? "Live — updating every 2s while the pipeline runs."
                    : "Paused — resolve the checkpoint above to resume."}
                </span>
                <button
                  className="font-semibold text-blue-700 underline-offset-2 hover:underline"
                  onClick={() => {
                    void refresh();
                    void loadSecondary();
                  }}
                >
                  Refresh now
                </button>
              </Card>
            </div>
          )}

          {tab === "validation" && (
            <ValidationTable results={validation} loading={validationLoading} />
          )}

          {tab === "findings" && (
            <FindingsList
              workflowId={id}
              findings={findings}
              loading={findingsLoading}
            />
          )}

          {tab === "report" && (
            <ReportView
              workflowId={id}
              report={report}
              history={reportHistory}
              loading={reportLoading}
              pendingReview={pendingReviewType(status)}
              onApplied={onApplied}
              onVersion={(v) => {
                setReportVersion(v);
                setReportLoading(true);
              }}
            />
          )}

          {tab === "audit" && (
            <div className="space-y-4">
              <AuditTimeline events={audit} loading={auditLoading} />
              <AgentRunTable runs={runs} loading={runsLoading} />
            </div>
          )}
        </>
      )}
    </main>
  );
}
