"use client";

import { useState } from "react";
import {
  Badge,
  Button,
  Card,
  Modal,
  Spinner,
} from "@/components/ui/primitives";
import { useDecision } from "@/hooks/useDecision";
import type { PendingCheckpoint } from "@/lib/api";

const RATIONALE_REQUIRED = new Set(["reject", "override", "accept_exception"]);

function prettyDecision(d: string): string {
  return d.replaceAll("_", " ");
}

function CheckpointContext({ context }: { context: Record<string, unknown> }) {
  const entries = Object.entries(context ?? {}).filter(
    ([k]) => !["report_id", "checkpoint_id"].includes(k)
  );
  if (!entries.length) return null;
  return (
    <dl className="mt-3 space-y-1 rounded bg-slate-50 p-3 text-sm">
      {entries.map(([k, v]) => (
        <div key={k} className="flex gap-2">
          <dt className="w-40 shrink-0 font-medium text-slate-600">
            {k.replaceAll("_", " ")}
          </dt>
          <dd className="text-slate-800">
            {typeof v === "object" ? JSON.stringify(v) : String(v)}
          </dd>
        </div>
      ))}
    </dl>
  );
}

export function DecisionForm({
  workflowId,
  checkpoint,
  onApplied,
  onDone,
}: {
  workflowId: string;
  checkpoint: PendingCheckpoint;
  onApplied: () => void;
  onDone: () => void;
}) {
  const [decision, setDecision] = useState(checkpoint.options[0]?.decision ?? "");
  const [rationale, setRationale] = useState("");
  const [extraPayload, setExtraPayload] = useState("{}");
  const [localError, setLocalError] = useState<string | null>(null);
  const { submit, submitting, error } = useDecision(workflowId, onApplied);
  const needsRationale =
    RATIONALE_REQUIRED.has(decision) || decision === "review_assumption";

  // one decision can have several labeled options (CP-1: which file is
  // authoritative) — each option may carry its own payload
  const decisionOptions = checkpoint.options.filter((o) => o.decision === decision);
  const [optionIdx, setOptionIdx] = useState(0);
  const chosenOption = decisionOptions[Math.min(optionIdx, decisionOptions.length - 1)];

  async function handleSubmit() {
    setLocalError(null);
    if (needsRationale && rationale.trim().length < 20) {
      setLocalError("A rationale of at least 20 characters is required.");
      return;
    }
    let parsed: Record<string, unknown> = { ...(chosenOption?.payload ?? {}) };
    if (extraPayload.trim() && extraPayload.trim() !== "{}") {
      try {
        parsed = { ...parsed, ...(JSON.parse(extraPayload) as Record<string, unknown>) };
      } catch {
        setLocalError("Extra payload must be valid JSON (or empty).");
        return;
      }
    }
    try {
      await submit({
        checkpoint_id: checkpoint.id,
        decision,
        rationale,
        payload: parsed,
      });
      onDone();
    } catch {
      /* error shown inline */
    }
  }

  const distinctDecisions = Array.from(
    new Set(checkpoint.options.map((o) => o.decision))
  );
  const optionLabel = (d: string) =>
    checkpoint.options.find((o) => o.decision === d)?.label ?? prettyDecision(d);

  return (
    <div className="space-y-3">
      <div>
        <label className="mb-1 block text-sm font-medium">Decision</label>
        <select
          value={decision}
          onChange={(e) => {
            setDecision(e.target.value);
            setOptionIdx(0);
          }}
          className="w-full rounded border border-slate-300 px-3 py-2 text-sm"
        >
          {distinctDecisions.map((d) => (
            <option key={d} value={d}>
              {optionLabel(d)}
            </option>
          ))}
        </select>
      </div>
      {decisionOptions.length > 1 && (
        <div className="rounded bg-slate-50 p-3 text-sm">
          <div className="mb-1 font-medium">Choose one</div>
          {decisionOptions.map((o, i) => (
            <label key={i} className="flex items-center gap-2 py-0.5">
              <input
                type="radio"
                name="cp-option"
                checked={i === Math.min(optionIdx, decisionOptions.length - 1)}
                onChange={() => setOptionIdx(i)}
              />
              <span>{o.label}</span>
            </label>
          ))}
        </div>
      )}
      <div>
        <label className="mb-1 block text-sm font-medium">
          Rationale {needsRationale ? "(required, ≥20 chars)" : "(optional)"}
        </label>
        <textarea
          value={rationale}
          onChange={(e) => setRationale(e.target.value)}
          rows={3}
          className="w-full rounded border border-slate-300 px-3 py-2 text-sm"
          placeholder="e.g. Known endorsement processing lag — documented"
        />
      </div>
      <details className="text-sm">
        <summary className="cursor-pointer text-slate-500">
          Advanced: extra payload JSON
        </summary>
        <input
          value={extraPayload}
          onChange={(e) => setExtraPayload(e.target.value)}
          placeholder="{}"
          className="mt-1 w-full rounded border border-slate-300 px-3 py-2 font-mono text-xs"
        />
      </details>
      {(localError || error) && (
        <p className="text-sm text-red-700" role="alert">
          {localError ?? error}
        </p>
      )}
      <div className="flex justify-end gap-2">
        <Button variant="secondary" onClick={onDone}>
          Cancel
        </Button>
        <Button onClick={handleSubmit} disabled={submitting}>
          {submitting ? "Submitting…" : "Submit decision"}
        </Button>
      </div>
    </div>
  );
}

/** Blocking (red) banner + yellow side notice; modal auto-opens on blocking. */
export function CheckpointBanner({
  workflowId,
  checkpoints,
  onApplied,
}: {
  workflowId: string;
  checkpoints: PendingCheckpoint[];
  onApplied: () => void;
}) {
  const [openId, setOpenId] = useState<string | null>(() => {
    const blocking = checkpoints.find((c) => c.blocking);
    return blocking ? blocking.id : null;
  });
  if (!checkpoints.length) return null;
  const open = checkpoints.find((c) => c.id === openId) ?? null;
  return (
    <div className="space-y-2">
      {checkpoints.map((c) => (
        <Card
          key={c.id}
          className={`flex items-center justify-between gap-3 p-3 ${
            c.blocking ? "border-red-300 bg-red-50" : "border-yellow-300 bg-yellow-50"
          }`}
        >
          <div className="text-sm">
            <Badge tone={c.blocking ? "red" : "yellow"}>
              {c.blocking ? "Action required — workflow paused" : "Review suggested"}
            </Badge>
            <div className="mt-1 font-medium">{c.title}</div>
          </div>
          <Button
            variant={c.blocking ? "primary" : "secondary"}
            onClick={() => setOpenId(c.id)}
          >
            Decide
          </Button>
        </Card>
      ))}
      {open && (
        <Modal title={open.title} onClose={() => setOpenId(null)}>
          <CheckpointContext context={open.context} />
          <div className="mt-3">
            <DecisionForm
              workflowId={workflowId}
              checkpoint={open}
              onApplied={onApplied}
              onDone={() => setOpenId(null)}
            />
          </div>
        </Modal>
      )}
    </div>
  );
}

/** Polling-aware checkpoint list for tabs that need the raw data. */
export function CheckpointListState({ loading }: { loading: boolean }) {
  if (loading) return <Spinner label="Checking for decisions…" />;
  return null;
}
