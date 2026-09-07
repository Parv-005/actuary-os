"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Button, Card } from "@/components/ui/primitives";
import { UploadZone } from "@/components/workflow/UploadZone";
import { api } from "@/lib/api";

const STEPS = ["Period", "Files", "Launch"];

export default function NewWorkflowPage() {
  const router = useRouter();
  const [period, setPeriod] = useState("2026-09");
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const step = files.length > 0 ? 3 : 2;

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
    <main className="mx-auto max-w-2xl space-y-5 px-4 py-8 sm:px-8">
      <Link
        href="/"
        className="inline-flex items-center gap-1 text-sm font-medium text-slate-500 transition-colors hover:text-slate-900"
      >
        ← Dashboard
      </Link>
      <div>
        <h1 className="text-2xl font-black tracking-tight text-slate-900 sm:text-3xl">
          New Monthly Review
        </h1>
        <p className="mt-1 text-sm text-slate-500">
          Three steps — period, files, launch. Or skip the queue with the guided demo.
        </p>
      </div>

      <ol className="flex items-center gap-2" aria-label="Progress">
        {STEPS.map((s, i) => {
          const n = i + 1;
          const done = n < step;
          const current = n === step;
          return (
            <li key={s} className="flex flex-1 items-center gap-2 last:flex-none">
              <span
                className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[11px] font-black ${
                  done
                    ? "bg-emerald-500 text-white"
                    : current
                      ? "bg-ink-900 text-white"
                      : "bg-slate-200 text-slate-500"
                }`}
              >
                {done ? "✓" : n}
              </span>
              <span
                className={`text-xs font-bold ${current || done ? "text-slate-800" : "text-slate-400"}`}
              >
                {s}
              </span>
              {n < STEPS.length && <span className="mx-1 h-px flex-1 bg-slate-200" />}
            </li>
          );
        })}
      </ol>

      <Card className="space-y-5 p-5 sm:p-6">
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label className="field-label" htmlFor="period">
              1 · Reporting period
            </label>
            <input
              id="period"
              value={period}
              onChange={(e) => setPeriod(e.target.value)}
              pattern="\d{4}-(0[1-9]|1[0-2])"
              placeholder="YYYY-MM"
              className="field-input font-mono"
            />
          </div>
          <div>
            <span className="field-label">Portfolio</span>
            <div className="field-input bg-slate-50 font-medium text-slate-700">
              General Insurance
            </div>
          </div>
        </div>
        <div>
          <span className="field-label">2 · Source files</span>
          <UploadZone onFiles={setFiles} disabled={busy} />
        </div>
        {error && (
          <p
            className="rounded-lg bg-red-50 px-3 py-2 text-sm font-medium text-red-700 ring-1 ring-inset ring-red-200"
            role="alert"
          >
            {error}
          </p>
        )}
        <div className="flex items-center justify-between gap-3 border-t border-slate-100 pt-4">
          <p className="text-xs text-slate-400">
            {files.length
              ? `${files.length} file${files.length > 1 ? "s" : ""} attached — intake classifies on arrival.`
              : "No files yet — you can upload after creating."}
          </p>
          <Button onClick={handleLaunch} disabled={busy} size="lg">
            {busy ? (
              <>
                <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-white" />
                Launching…
              </>
            ) : files.length ? (
              "3 · Launch review →"
            ) : (
              "Create (upload later)"
            )}
          </Button>
        </div>
      </Card>
    </main>
  );
}
