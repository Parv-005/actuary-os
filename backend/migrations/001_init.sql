-- 001_init.sql — all §8 tables. Idempotent: safe to re-run.
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('actuary','admin','viewer')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS workflows (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  human_ref TEXT UNIQUE NOT NULL,
  workflow_type TEXT NOT NULL DEFAULT 'monthly_portfolio_review',
  portfolio TEXT NOT NULL DEFAULT 'General Insurance',
  reporting_period TEXT NOT NULL CHECK (reporting_period ~ '^[0-9]{4}-[0-9]{2}$'),
  status TEXT NOT NULL DEFAULT 'CREATED' CHECK (status IN (
    'CREATED','INPUT_WAIT','INGESTING','VALIDATING','VALIDATED','ANALYZING','ANALYZED',
    'INVESTIGATING','INSIGHTS_READY','REPORTING','QA','WAITING_FOR_HUMAN','APPROVED',
    'COMPLETED','BLOCKED','RETRYING','FAILED','REJECTED','CANCELLED')),
  stage TEXT,
  config JSONB NOT NULL DEFAULT '{}',
  error JSONB,
  locked_until TIMESTAMPTZ NULL,
  resume_count INT NOT NULL DEFAULT 0,
  supersedes_workflow_id UUID NULL REFERENCES workflows(id),
  created_by UUID REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ NULL
);
CREATE INDEX IF NOT EXISTS idx_workflows_status ON workflows(status);
CREATE INDEX IF NOT EXISTS idx_workflows_period ON workflows(reporting_period);

CREATE TABLE IF NOT EXISTS files (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workflow_id UUID NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
  kind TEXT NOT NULL DEFAULT 'unknown' CHECK (kind IN ('claims','premium','exposure','unknown')),
  filename TEXT NOT NULL,
  storage_path TEXT NOT NULL,
  size_bytes BIGINT NOT NULL DEFAULT 0,
  checksum TEXT NOT NULL DEFAULT '',
  mime TEXT NOT NULL DEFAULT 'text/csv',
  row_count INT NULL,
  role TEXT NOT NULL DEFAULT 'primary' CHECK (role IN ('primary','duplicate','quarantined','superseded')),
  period_inferred TEXT NULL,
  uploaded_by UUID REFERENCES users(id),
  uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (workflow_id, filename)
);
CREATE INDEX IF NOT EXISTS idx_files_wf_kind ON files(workflow_id, kind);

CREATE TABLE IF NOT EXISTS dataset_versions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workflow_id UUID NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('claims','premium','exposure')),
  source_file_ids UUID[] NOT NULL DEFAULT '{}',
  storage_path TEXT NOT NULL,
  row_count INT NOT NULL DEFAULT 0,
  column_map JSONB NOT NULL DEFAULT '{}',
  transform_log JSONB NOT NULL DEFAULT '{}',
  checksum TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_datasets_wf ON dataset_versions(workflow_id);

CREATE TABLE IF NOT EXISTS validation_results (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workflow_id UUID NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
  check_id TEXT NOT NULL,
  check_name TEXT NOT NULL DEFAULT '',
  category TEXT NOT NULL CHECK (category IN ('structural','record','reconciliation','behavioral')),
  severity TEXT NOT NULL CHECK (severity IN ('INFO','WARNING','BLOCKER')),
  status TEXT NOT NULL CHECK (status IN ('PASS','WARNING','BLOCKER','ACCEPTED_EXCEPTION','RESOLVED')),
  message TEXT NOT NULL DEFAULT '',
  details JSONB NOT NULL DEFAULT '{}',
  affected_row_count INT NOT NULL DEFAULT 0,
  resolved_by UUID REFERENCES users(id),
  resolved_at TIMESTAMPTZ NULL,
  resolution JSONB NULL,
  UNIQUE (workflow_id, check_id)
);

CREATE TABLE IF NOT EXISTS metrics (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workflow_id UUID NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
  metric_key TEXT NOT NULL,
  dimensions JSONB NOT NULL DEFAULT '{}',
  period TEXT NOT NULL,
  value NUMERIC NULL,
  prev_value NUMERIC NULL,
  expected_value NUMERIC NULL,
  delta_pp NUMERIC NULL,
  unit TEXT NOT NULL DEFAULT 'ratio',
  undefined_reason TEXT NULL,
  flags JSONB NOT NULL DEFAULT '{}',
  formula TEXT NOT NULL DEFAULT '',
  inputs JSONB NOT NULL DEFAULT '{}',
  dataset_version_ids UUID[] NOT NULL DEFAULT '{}',
  module_version TEXT NOT NULL DEFAULT 'm1',
  computed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_metrics_wf_key ON metrics(workflow_id, metric_key);

CREATE TABLE IF NOT EXISTS findings (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workflow_id UUID NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
  agent TEXT NOT NULL DEFAULT 'insight',
  agent_version TEXT NOT NULL DEFAULT 'v1',
  model TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL,
  narrative TEXT NOT NULL DEFAULT '',
  severity TEXT NOT NULL CHECK (severity IN ('high','medium','low')),
  confidence NUMERIC(3,2) NOT NULL DEFAULT 0.5,
  possible_drivers JSONB NOT NULL DEFAULT '[]',
  alternatives JSONB NOT NULL DEFAULT '[]',
  correlation_caveat TEXT NULL,
  human_review_required BOOL NOT NULL DEFAULT FALSE,
  decision_question TEXT NULL,
  data_quality TEXT NULL,
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','accepted','rejected','overridden','monitoring','investigate_further')),
  links JSONB NOT NULL DEFAULT '[]',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_findings_wf ON findings(workflow_id);

CREATE TABLE IF NOT EXISTS evidence (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workflow_id UUID NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
  finding_id UUID NULL REFERENCES findings(id) ON DELETE CASCADE,
  evidence_type TEXT NOT NULL CHECK (evidence_type IN ('metric','validation','knowledge','file')),
  ref_id TEXT NOT NULL DEFAULT '',
  snapshot JSONB NOT NULL DEFAULT '{}',
  description TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_evidence_finding ON evidence(finding_id);

CREATE TABLE IF NOT EXISTS human_checkpoints (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workflow_id UUID NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
  checkpoint_type TEXT NOT NULL CHECK (checkpoint_type IN (
    'input_exception','validation_blocker','schema_mapping','assumption_variance',
    'finding_review','final_approval','qa_failure')),
  severity TEXT NOT NULL CHECK (severity IN ('red','yellow')),
  blocking BOOL NOT NULL DEFAULT TRUE,
  title TEXT NOT NULL,
  context JSONB NOT NULL DEFAULT '{}',
  options JSONB NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','resolved','dismissed')),
  raised_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_at TIMESTAMPTZ NULL,
  resolved_by UUID REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_checkpoints_wf ON human_checkpoints(workflow_id, status);

CREATE TABLE IF NOT EXISTS human_decisions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workflow_id UUID NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
  checkpoint_id UUID NULL REFERENCES human_checkpoints(id),
  finding_id UUID NULL REFERENCES findings(id),
  report_id UUID NULL,
  decision TEXT NOT NULL CHECK (decision IN (
    'accept','reject','override','investigate_further','request_revision','approve',
    'select_file','accept_exception','reject_data','request_rerun','monitor',
    'no_change_required','confirm_mapping','ignore_column','review_assumption',
    'escalate','comment')),
  rationale TEXT NOT NULL DEFAULT '',
  payload JSONB NOT NULL DEFAULT '{}',
  decided_by UUID REFERENCES users(id),
  decided_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_decisions_wf ON human_decisions(workflow_id);

CREATE TABLE IF NOT EXISTS reports (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workflow_id UUID NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
  version INT NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','qa_passed','approved','superseded')),
  sections JSONB NOT NULL DEFAULT '{}',
  body_markdown TEXT NOT NULL DEFAULT '',
  qa_result JSONB NULL,
  generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  approved_by UUID REFERENCES users(id),
  approved_at TIMESTAMPTZ NULL,
  UNIQUE (workflow_id, version)
);

CREATE TABLE IF NOT EXISTS agent_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workflow_id UUID NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
  agent TEXT NOT NULL,
  stage TEXT NOT NULL,
  attempt INT NOT NULL DEFAULT 1,
  status TEXT NOT NULL CHECK (status IN ('running','succeeded','failed','timeout','skipped')),
  input_ref JSONB NOT NULL DEFAULT '{}',
  output_ref JSONB NOT NULL DEFAULT '{}',
  error TEXT NULL,
  prompt_hash TEXT NULL,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  duration_ms INT NOT NULL DEFAULT 0,
  llm_calls INT NOT NULL DEFAULT 0,
  tokens_in INT NOT NULL DEFAULT 0,
  tokens_out INT NOT NULL DEFAULT 0,
  cost_usd NUMERIC(8,4) NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_runs_wf_stage ON agent_runs(workflow_id, stage, status);

CREATE TABLE IF NOT EXISTS audit_events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workflow_id UUID NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
  actor_type TEXT NOT NULL CHECK (actor_type IN ('agent','human','system')),
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  from_status TEXT NULL,
  to_status TEXT NULL,
  entity_type TEXT NOT NULL DEFAULT '',
  entity_id UUID NULL,
  summary TEXT NOT NULL DEFAULT '',
  details JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_audit_wf_ts ON audit_events(workflow_id, created_at);

CREATE TABLE IF NOT EXISTS knowledge_documents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  title TEXT NOT NULL,
  doc_type TEXT NOT NULL CHECK (doc_type IN ('methodology','prior_report','policy','definition')),
  version TEXT NOT NULL DEFAULT '1.0',
  effective_date DATE NULL,
  superseded_by UUID NULL REFERENCES knowledge_documents(id),
  content_text TEXT NOT NULL DEFAULT '',
  tags TEXT[] NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS reference_values (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  period TEXT NOT NULL,
  metric_key TEXT NOT NULL,
  dimensions JSONB NOT NULL DEFAULT '{}',
  value NUMERIC NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('system_of_record','methodology'))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_ref_period_key_dims
  ON reference_values(period, metric_key, (dimensions::text));

CREATE TABLE IF NOT EXISTS schema_migrations (
  version TEXT PRIMARY KEY,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
