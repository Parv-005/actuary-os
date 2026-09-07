/* Typed client for the Vortex ActuaryOS FastAPI backend (§10 contracts). */

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export interface ApiError extends Error {
  status: number;
  code?: string;
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const err = new Error(
      (body as { detail?: string }).detail ?? `API ${res.status}`
    ) as ApiError;
    err.status = res.status;
    err.code = (body as { code?: string }).code;
    throw err;
  }
  return (await res.json()) as T;
}

export const get = <T>(path: string): Promise<T> => req<T>(path);
export const post = <T>(path: string, body?: unknown): Promise<T> =>
  req<T>(path, { method: "POST", body: JSON.stringify(body ?? {}) });

/* ---- domain types (mirror §10 response shapes) ---- */

export interface WorkflowSummary {
  id: string;
  human_ref: string;
  period: string;
  portfolio: string;
  status: string;
  stage: string | null;
  pending_checkpoints: number;
  updated_at: string | null;
}

export interface StageStatus {
  stage: string;
  agent: string;
  state: string;
  attempt: number;
  duration_ms: number;
  error: string | null;
}

export interface CheckpointOption {
  decision: string;
  label: string;
  payload?: Record<string, unknown>;
}

export interface PendingCheckpoint {
  id: string;
  type: string;
  severity: string;
  blocking: boolean;
  title: string;
  context: Record<string, unknown>;
  options: CheckpointOption[];
}

export interface WorkflowStatus {
  id: string;
  human_ref: string;
  status: string;
  stage: string | null;
  stage_statuses: StageStatus[];
  pending_checkpoints: PendingCheckpoint[];
  error: Record<string, unknown> | null;
  updated_at: string | null;
}

export interface ValidationResult {
  check_id: string;
  name: string;
  category: string;
  severity: string;
  status: string;
  message: string;
  details: Record<string, unknown>;
  affected_row_count?: number;
  resolution?: Record<string, unknown> | null;
}

export interface MetricRow {
  id: string;
  metric_key: string;
  dimensions: Record<string, string>;
  period: string;
  value: number | null;
  prev_value: number | null;
  expected_value: number | null;
  delta_pp: number | null;
  unit: string;
  undefined_reason: string | null;
  flags: Record<string, unknown>;
  formula: string;
}

export interface FindingSummary {
  id: string;
  title: string;
  severity: string;
  confidence: number;
  evidence_count: number;
  alternatives: string[];
  possible_drivers: string[];
  status: string;
  human_review_required: boolean;
  decision_question: string | null;
  data_quality?: string | null;
}

export interface EvidenceChain {
  metric: Record<string, unknown> | null;
  error?: string;
}

export interface EvidenceItem {
  id: string;
  type: string;
  ref_id: string;
  description: string;
  snapshot: Record<string, unknown>;
  chain?: EvidenceChain;
  document?: { title: string; version: string; doc_type: string } | null;
  created_at?: string | null;
}

export interface FindingDetail extends FindingSummary {
  narrative: string;
  correlation_caveat: string | null;
  links: string[];
}

export interface ReportVersion {
  id: string;
  version: number;
  status: string;
  sections: Record<string, unknown>;
  body_markdown: string;
  qa_result: Record<string, unknown> | null;
  approved_by: string | null;
  approved_at: string | null;
  generated_at: string | null;
}

export interface AuditEvent {
  id: string;
  ts: string | null;
  actor_type: string;
  actor: string;
  action: string;
  from_status: string | null;
  to_status: string | null;
  entity_type: string;
  entity_id: string | null;
  summary: string;
}

export interface AgentRunRow {
  id: string;
  agent: string;
  stage: string;
  attempt: number;
  status: string;
  duration_ms: number;
  llm_calls: number;
  tokens_in: number;
  tokens_out: number;
  cost_usd: number;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
}

/* ---- calls ---- */

export const api = {
  health: () => get<{ status: string; db: string; version: string }>("/health"),
  listWorkflows: () => get<{ workflows: WorkflowSummary[] }>("/workflows"),
  createWorkflow: (body: { reporting_period: string; portfolio?: string; demo?: boolean }) =>
    post<{ id: string; human_ref: string; status: string }>("/workflows", body),
  uploadFiles: async (id: string, files: File[]) => {
    const form = new FormData();
    for (const f of files) form.append("files", f);
    const res = await fetch(`${API_URL}/workflows/${id}/upload`, {
      method: "POST",
      body: form,
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      const detail =
        (body as { detail?: string }).detail ?? `upload failed: ${res.status}`;
      const err = new Error(detail) as ApiError;
      err.status = res.status;
      err.code = (body as { code?: string }).code;
      throw err;
    }
    return (await res.json()) as {
      files: unknown[];
      status: string;
      auto_started: boolean;
    };
  },
  startWorkflow: (id: string) =>
    post<{ status: string }>(`/workflows/${id}/start`),
  resumeWorkflow: (id: string) =>
    post<{ status: string }>(`/workflows/${id}/resume`),
  getStatus: (id: string) => get<WorkflowStatus>(`/workflows/${id}/status`),
  getCheckpoints: (id: string) =>
    get<{ pending: PendingCheckpoint[] }>(`/workflows/${id}/checkpoints`),
  postDecision: (
    id: string,
    body: {
      checkpoint_id?: string;
      finding_id?: string;
      report_id?: string;
      decision: string;
      rationale?: string;
      payload?: Record<string, unknown>;
    }
  ) =>
    post<{ workflow_status: string; applied: string[]; supersedes_prior?: boolean }>(
      `/workflows/${id}/decisions`,
      body
    ),
  getValidation: (id: string) =>
    get<{ results: ValidationResult[] }>(`/workflows/${id}/validation`),
  getMetrics: (id: string, params?: Record<string, string>) => {
    const qs = params ? `?${new URLSearchParams(params).toString()}` : "";
    return get<{ metrics: MetricRow[]; undefined: unknown[] }>(
      `/workflows/${id}/metrics${qs}`
    );
  },
  getSeries: (id: string, series: string) =>
    get<{ metric: string; series: { period: string; value: number | null; source: string }[] }>(
      `/workflows/${id}/metrics?series=${series}`
    ),
  getFindings: (id: string) =>
    get<{ findings: FindingSummary[] }>(`/workflows/${id}/findings`),
  getFinding: (id: string, fid: string) =>
    get<{ finding: FindingDetail; evidence: EvidenceItem[]; prior_findings: unknown[] }>(
      `/workflows/${id}/findings/${fid}`
    ),
  getReport: (id: string, version?: number) =>
    get<{ report: ReportVersion; history: { version: number; status: string; generated_at: string | null }[] }>(
      `/workflows/${id}/report${version ? `?version=${version}` : ""}`
    ),
  getAuditLog: (id: string, params?: { limit?: number; after?: string }) => {
    const qs = params
      ? `?${new URLSearchParams(
          Object.fromEntries(
            Object.entries(params).map(([k, v]) => [k, String(v)])
          )
        ).toString()}`
      : "";
    return get<{ events: AuditEvent[] }>(`/workflows/${id}/audit-log${qs}`);
  },
  getAgentRuns: (id: string) =>
    get<{ runs: AgentRunRow[] }>(`/workflows/${id}/agent-runs`),
  getDemoInstructions: () =>
    get<{
      period: string;
      portfolio: string;
      files: { filename: string; blurb: string }[];
      steps: { n: number; title: string; detail: string }[];
    }>("/demo/instructions"),
  downloadUrl: (workflowId: string, fileId: string) =>
    `${API_URL}/workflows/${workflowId}/files/${fileId}/download`,
};

/* ---- presentation helpers ---- */

export const ACTIVE_STATUSES = new Set([
  "CREATED",
  "INPUT_WAIT",
  "INGESTING",
  "VALIDATING",
  "VALIDATED",
  "ANALYZING",
  "ANALYZED",
  "INVESTIGATING",
  "INSIGHTS_READY",
  "REPORTING",
  "QA",
  "RETRYING",
]);

export const TERMINAL_STATUSES = new Set(["COMPLETED", "REJECTED", "CANCELLED"]);

export function isPolling(status: string): boolean {
  return ACTIVE_STATUSES.has(status);
}

const STAGE_LABELS: Record<string, string> = {
  intake: "Intake",
  data_prep: "Data Prep",
  validation: "Validation",
  analysis: "Analysis",
  insight: "Insight",
  knowledge: "Knowledge",
  reporting: "Reporting",
  qa: "QA",
};

export function stageLabel(stage: string): string {
  return STAGE_LABELS[stage] ?? stage;
}

export function formatPct(value: number | null, digits = 1): string {
  if (value === null || value === undefined) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

export function dimLabel(dims: Record<string, string>): string {
  const parts = Object.entries(dims).map(([, v]) => v);
  return parts.length ? parts.join(" · ") : "Portfolio";
}
