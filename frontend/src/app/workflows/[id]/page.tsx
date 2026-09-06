"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Button, Card, Spinner } from "@/components/ui/primitives";
import { AgentRunTable, AuditTimeline } from "@/components/workflow/AuditViews";
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
    <Card className="border-red-300 bg-red-50 p-4 text-sm">
      <div className="font-medium text-red-800">Workflow failed</div>
      <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap text-xs text-red-700">
        {JSON.stringify(error ?? "unknown error", null, 2)}
      </pre>
      {msg && <p className="mt-1 text-red-700">{msg}</p>}
      <div className="mt-3">
        <Button
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
          {busy ? "Retrying…" : "Retry from failed stage"}
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
    <Card className="space-y-3 p-4">
      <h3 className="font-medium">Inputs</h3>
      <UploadZone onFiles={setFiles} disabled={busy} />
      {error && (
        <p className="text-sm text-red-700" role="alert">
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
    <main className="mx-auto max-w-5xl space-y-4 p-8">
      <Link href="/" className="text-sm text-slate-500 hover:text-slate-800">
        ← Dashboard
      </Link>

      {!status && !error && <Spinner label="Loading workflow…" />}
      {error && !status && (
        <Card className="border-red-300 bg-red-50 p-4 text-sm text-red-800">
          {error}
        </Card>
      )}

      {status && (
        <>
          <header className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h1 className="text-2xl font-semibold">{status.human_ref}</h1>
              <p className="text-sm text-slate-500">
                stage: {status.stage ?? "—"}
                {!polling && status.status !== "COMPLETED" && (
                  <span className="ml-2 font-medium text-red-700">
                    ● Action required — polling paused
                  </span>
                )}
              </p>
            </div>
            <div className="flex items-center gap-2">
              <DemoGuide workflowId={id} />
              <StatusChip status={status.status} />
            </div>
          </header>

          <StageTracker stages={status.stage_statuses} />

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

          <nav className="flex gap-1 border-b border-slate-200">
            {(["overview", "validation", "findings", "report", "audit"] as Tab[]).map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={`px-4 py-2 text-sm font-medium capitalize ${
                  tab === t
                    ? "border-b-2 border-slate-900 text-slate-900"
                    : "text-slate-500 hover:text-slate-800"
                }`}
              >
                {t}
                {t === "findings" && findings?.length ? ` (${findings.length})` : ""}
              </button>
            ))}
          </nav>

          {tab === "overview" && (
            <div className="space-y-4">
              <KpiCards metrics={metrics} loading={metricsLoading} />
              <ChartsPanel workflowId={id} />
              <Card className="p-4 text-sm text-slate-600">
                {polling
                  ? "Live — updating every 2s while the pipeline runs."
                  : "Paused — resolve the checkpoint above to resume."}{" "}
                <button
                  className="underline hover:text-slate-900"
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
