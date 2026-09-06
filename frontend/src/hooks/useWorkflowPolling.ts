"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, isPolling, type WorkflowStatus } from "@/lib/api";

/** Poll GET /status every 2s while active; stop + banner on human gates (§10). */
export function useWorkflowPolling(id: string | null) {
  const [status, setStatus] = useState<WorkflowStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [polling, setPolling] = useState(true);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchOnce = useCallback(async () => {
    if (!id) return;
    try {
      const s = await api.getStatus(id);
      setStatus(s);
      setError(null);
      if (!isPolling(s.status)) setPolling(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "status fetch failed");
    }
  }, [id]);

  const restart = useCallback(() => {
    setPolling(true);
    void fetchOnce();
  }, [fetchOnce]);

  useEffect(() => {
    void fetchOnce();
  }, [fetchOnce]);

  useEffect(() => {
    if (!polling || !id) return;
    timer.current = setInterval(fetchOnce, 2000);
    return () => {
      if (timer.current) clearInterval(timer.current);
    };
  }, [polling, id, fetchOnce]);

  return { status, error, polling, refresh: fetchOnce, restart };
}
