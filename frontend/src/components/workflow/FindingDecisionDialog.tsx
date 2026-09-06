"use client";

import { useState } from "react";
import { Button, Modal } from "@/components/ui/primitives";
import { useDecision } from "@/hooks/useDecision";

const FINDING_DECISIONS = [
  "accept",
  "reject",
  "override",
  "monitor",
  "investigate_further",
  "comment",
];

const RATIONALE_REQUIRED = new Set(["reject", "override"]);

/** CP-5 finding review dialog (finding_id target, §12). */
export function FindingDecisionDialog({
  workflowId,
  findingId,
  findingTitle,
  onApplied,
  onClose,
}: {
  workflowId: string;
  findingId: string;
  findingTitle: string;
  onApplied: () => void;
  onClose: () => void;
}) {
  const [decision, setDecision] = useState("accept");
  const [rationale, setRationale] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);
  const { submit, submitting, error } = useDecision(workflowId, onApplied);

  async function handleSubmit() {
    setLocalError(null);
    if (RATIONALE_REQUIRED.has(decision) && rationale.trim().length < 20) {
      setLocalError("A rationale of at least 20 characters is required.");
      return;
    }
    try {
      await submit({ finding_id: findingId, decision, rationale });
      onClose();
    } catch {
      /* shown inline */
    }
  }

  return (
    <Modal title={`Decide: ${findingTitle}`} onClose={onClose}>
      <div className="space-y-3">
        <p className="text-xs text-slate-500">
          AI Observation — your decision is recorded with your name; later
          decisions supersede earlier ones (both kept).
        </p>
        <div>
          <label className="mb-1 block text-sm font-medium">Decision</label>
          <select
            value={decision}
            onChange={(e) => setDecision(e.target.value)}
            className="w-full rounded border border-slate-300 px-3 py-2 text-sm"
          >
            {FINDING_DECISIONS.map((d) => (
              <option key={d} value={d}>
                {d.replaceAll("_", " ")}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="mb-1 block text-sm font-medium">
            Rationale{" "}
            {RATIONALE_REQUIRED.has(decision) ? "(required, ≥20 chars)" : "(optional)"}
          </label>
          <textarea
            value={rationale}
            onChange={(e) => setRationale(e.target.value)}
            rows={3}
            className="w-full rounded border border-slate-300 px-3 py-2 text-sm"
          />
        </div>
        {(localError || error) && (
          <p className="text-sm text-red-700" role="alert">
            {localError ?? error}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} disabled={submitting}>
            {submitting ? "Submitting…" : "Submit decision"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
