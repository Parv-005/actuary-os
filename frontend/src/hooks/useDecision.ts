"use client";

import { useState } from "react";
import { api } from "@/lib/api";

export interface DecisionInput {
  checkpoint_id?: string;
  finding_id?: string;
  report_id?: string;
  decision: string;
  rationale?: string;
  payload?: Record<string, unknown>;
}

/** POST a human decision, then restart polling (per §11 useDecision). */
export function useDecision(workflowId: string, onApplied: () => void) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(input: DecisionInput): Promise<string> {
    setSubmitting(true);
    setError(null);
    try {
      const res = await api.postDecision(workflowId, input);
      onApplied(); // restarts polling + refreshes checkpoints
      return res.workflow_status;
    } catch (e) {
      setError(e instanceof Error ? e.message : "decision failed");
      throw e;
    } finally {
      setSubmitting(false);
    }
  }

  return { submit, submitting, error };
}
