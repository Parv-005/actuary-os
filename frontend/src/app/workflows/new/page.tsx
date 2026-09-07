"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Button, Card } from "@/components/ui/primitives";
import { UploadZone } from "@/components/workflow/UploadZone";
import { api } from "@/lib/api";

export default function NewWorkflowPage() {
  const router = useRouter();
  const [period, setPeriod] = useState("2026-09");
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleLaunch() {
    setBusy(true);
    setError(null);
    try {
      const w = await api.createWorkflow({
        reporting_period: period,
        portfolio: "General Insurance",
      });
      if (files.length) {
        const up = await api.uploadFiles(w.id, files);
        // Upload auto-starts the workflow once claims+premium+exposure are
        // present (backend returns auto_started + status INGESTING). Only
        // manual-start when still waiting for input, otherwise the extra
        // start 409s with "cannot start from state INGESTING".
        if (!up.auto_started && up.status === "INPUT_WAIT") {
          try {
            await api.startWorkflow(w.id);
          } catch (e) {
            // Auto-start raced us between upload and start — already running,
            // so proceed to the detail page instead of surfacing a 409.
            const msg = e instanceof Error ? e.message : "";
            const status = (e as { status?: number }).status;
            if (status !== 409 || !msg.includes("cannot start from state"))
              throw e;
          }
        }
      }
      router.push(`/workflows/${w.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "launch failed");
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto max-w-2xl space-y-6 p-8">
      <Link href="/" className="text-sm text-slate-500 hover:text-slate-800">
        ← Dashboard
      </Link>
      <h1 className="text-2xl font-semibold">New Monthly Review</h1>
      <Card className="space-y-4 p-6">
        <div>
          <label className="mb-1 block text-sm font-medium">Reporting period</label>
          <input
            value={period}
            onChange={(e) => setPeriod(e.target.value)}
            pattern="\d{4}-(0[1-9]|1[0-2])"
            className="w-40 rounded border border-slate-300 px-3 py-2 text-sm"
          />
        </div>
        <UploadZone onFiles={setFiles} disabled={busy} />
        {error && (
          <p className="text-sm text-red-700" role="alert">
            {error}
          </p>
        )}
        <div className="flex justify-end">
          <Button onClick={handleLaunch} disabled={busy}>
            {busy ? "Launching…" : files.length ? "Launch review" : "Create (upload later)"}
          </Button>
        </div>
      </Card>
    </main>
  );
}
