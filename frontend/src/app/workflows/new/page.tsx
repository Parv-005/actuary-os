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
        await api.uploadFiles(w.id, files);
        await api.startWorkflow(w.id);
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
