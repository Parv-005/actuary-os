"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Button, Card, Spinner } from "@/components/ui/primitives";
import { WorkflowList } from "@/components/workflow/WorkflowList";
import { api, type WorkflowSummary } from "@/lib/api";

function DemoLaunchCard({ onLaunch }: { onLaunch: () => void }) {
  const [busy, setBusy] = useState(false);
  return (
    <Card className="border-slate-900 p-6">
      <h2 className="text-lg font-semibold">Monthly Portfolio Review — September 2026</h2>
      <p className="mt-1 text-sm text-slate-600">
        Runs the real pipeline on seeded demo files: duplicate claims submission (CP-1),
        −2.1% premium reconciliation (CP-2), construction severity finding, assumption
        variance gate (CP-4), QA-verified draft report, final approval (CP-6).
      </p>
      <div className="mt-4">
        <Button onClick={() => { setBusy(true); onLaunch(); }} disabled={busy}>
          {busy ? "Launching…" : "Start Guided Demo"}
        </Button>
      </div>
    </Card>
  );
}

export default function Dashboard() {
  const router = useRouter();
  const [workflows, setWorkflows] = useState<WorkflowSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [healthy, setHealthy] = useState<boolean | null>(null);

  useEffect(() => {
    api
      .health()
      .then(() => setHealthy(true))
      .catch(() => setHealthy(false));
    api
      .listWorkflows()
      .then((b) => setWorkflows(b.workflows))
      .catch((e: Error) => setError(e.message));
  }, []);

  async function launchDemo() {
    try {
      const w = await api.createWorkflow({
        reporting_period: "2026-09",
        portfolio: "General Insurance",
        demo: true,
      });
      router.push(`/workflows/${w.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "demo launch failed");
    }
  }

  return (
    <main className="mx-auto max-w-4xl space-y-6 p-8">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Vortex ActuaryOS</h1>
          <p className="text-sm text-slate-600">
            Monthly Portfolio Review — AI prepares, the actuary decides.
          </p>
        </div>
        <Link
          href="/workflows/new"
          className="rounded bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700"
        >
          New Monthly Review
        </Link>
      </header>

      {healthy === false && (
        <Card className="border-yellow-300 bg-yellow-50 p-4 text-sm">
          Waking backend… the API is cold or unreachable. This page retries on
          navigation; polls resume automatically once it responds.
        </Card>
      )}

      <DemoLaunchCard onLaunch={launchDemo} />

      <section>
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-500">
          Reviews
        </h2>
        {error && (
          <Card className="mb-3 border-red-300 bg-red-50 p-3 text-sm text-red-800">
            {error}
          </Card>
        )}
        {workflows === null && !error ? (
          <Spinner label="Loading reviews…" />
        ) : (
          <WorkflowList workflows={workflows ?? []} />
        )}
      </section>
    </main>
  );
}
