"use client";

import { useState } from "react";
import { Button, Modal } from "@/components/ui/primitives";
import { useDecision } from "@/hooks/useDecision";

const FINDING_DECISIONS = [
  { id: "accept", icon: "✓", title: "Accept", blurb: "Finding stands as written.", next: "Recorded; the report keeps it." },
  { id: "monitor", icon: "◷", title: "Monitor", blurb: "Watch it next period, no action now.", next: "Flagged for repeat monitoring." },
  { id: "comment", icon: "💬", title: "Comment", blurb: "Add a note, change nothing.", next: "Note joins the audit trail." },
  { id: "investigate_further", icon: "🔍", title: "Investigate further", blurb: "One bounded extra round with your focus note.", next: "Insight re-runs once, then QA re-verifies." },
  { id: "reject", icon: "✕", title: "Reject", blurb: "This finding doesn't hold.", next: "Marked rejected with your rationale." },
  { id: "override", icon: "⚡", title: "Override", blurb: "Replace with your judgement.", next: "Marked overridden — your note is the record." },
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
  const meta = FINDING_DECISIONS.find((d) => d.id === decision);

  async function handleSubmit() {
    setLocalError(null);
    if (RATIONALE_REQUIRED.has(decision) && rationale.trim().length < 20) {
      setLocalError("Add a rationale of at least 20 characters — the audit trail needs it.");
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
    <Modal title="Review this AI observation" subtitle={findingTitle} onClose={onClose} wide>
      <div className="space-y-4">
        <p className="text-xs leading-relaxed text-slate-500">
          Your decision is recorded with your name; later decisions supersede earlier ones (both are
          kept in the audit trail).
        </p>
        <div className="grid gap-2 sm:grid-cols-2">
          {FINDING_DECISIONS.map((d) => {
            const active = d.id === decision;
            return (
              <button
                key={d.id}
                type="button"
                onClick={() => setDecision(d.id)}
                aria-pressed={active}
                className={`flex items-start gap-2.5 rounded-xl border p-3 text-left transition-all ${
                  active
                    ? "border-blue-600 bg-blue-50/70 ring-1 ring-blue-600/30"
                    : "border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50"
                }`}
              >
                <span
                  className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-sm font-black ${
                    active ? "bg-blue-600 text-white" : "bg-slate-100 text-slate-500"
                  }`}
                >
                  {d.icon}
                </span>
                <span className="min-w-0">
                  <span className="flex items-center gap-1.5 text-sm font-bold text-slate-900">
                    {d.title}
                    {RATIONALE_REQUIRED.has(d.id) && (
                      <span className="rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] font-bold uppercase text-amber-800">
                        note
                      </span>
                    )}
                  </span>
                  <span className="mt-0.5 block text-xs leading-relaxed text-slate-500">{d.blurb}</span>
                </span>
              </button>
            );
          })}
        </div>

        {meta && (
          <p className="flex items-start gap-2 rounded-xl bg-slate-50 px-3 py-2.5 text-[13px] text-slate-600 ring-1 ring-inset ring-slate-200/70">
            <span>→</span>
            <span>
              <strong className="text-slate-800">What happens next: </strong>
              {meta.next}
            </span>
          </p>
        )}

        <div>
          <div className="flex items-baseline justify-between">
            <label className="field-label" htmlFor="finding-rationale">
              Rationale {RATIONALE_REQUIRED.has(decision) ? "(required, ≥20 chars)" : "(optional, recommended)"}
            </label>
            <span className={`text-xs tabular-nums ${rationale.trim().length >= 20 ? "font-bold text-emerald-600" : "text-slate-400"}`}>
              {rationale.trim().length}/20
            </span>
          </div>
          <textarea
            id="finding-rationale"
            value={rationale}
            onChange={(e) => setRationale(e.target.value)}
            rows={3}
            className="field-input resize-y"
            placeholder="e.g. Construction severity spike matches the two large fire losses — accepted pending reinsurance review."
          />
        </div>
        {(localError || error) && (
          <p className="rounded-lg bg-red-50 px-3 py-2 text-sm font-medium text-red-700 ring-1 ring-inset ring-red-200" role="alert">
            {localError ?? error}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} disabled={submitting}>
            {submitting ? "Submitting…" : `Confirm — ${meta?.title ?? decision}`}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
