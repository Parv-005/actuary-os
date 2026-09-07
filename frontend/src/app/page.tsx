"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  Button,
  Card,
  SectionTitle,
  SkeletonCard,
} from "@/components/ui/primitives";
import { WorkflowList } from "@/components/workflow/WorkflowList";
import { api, type WorkflowSummary } from "@/lib/api";

const DEMO_STEPS = [
  { n: "1", title: "Upload", text: "Seeded claims, premium & exposure extracts" },
  { n: "2", title: "Checkpoints", text: "You resolve CP-1 → CP-2 → CP-4 gates" },
  { n: "3", title: "Investigate", text: "Agents find what moved & why" },
  { n: "4", title: "Approve", text: "QA-verified draft, one-click sign-off" },
];

function DemoHero({ onLaunch }: { onLaunch: () => void }) {
  const [busy, setBusy] = useState(false);
  return (
    <section className="animate-fade-up overflow-hidden rounded-2xl bg-ink-950 text-white shadow-pop">
      <div className="relative px-6 py-8 sm:px-10 sm:py-10">
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-0"
          style={{
            background:
              "radial-gradient(600px 220px at 85% -10%, rgba(59,130,246,0.35), transparent 60%), radial-gradient(500px 200px at 10% 110%, rgba(16,185,129,0.18), transparent 60%)",
          }}
        />
        <div className="relative">
          <p className="text-xs font-bold uppercase tracking-[0.14em] text-blue-300">
            September 2026 · Live pipeline
          </p>
          <h2 className="mt-2 max-w-xl text-2xl font-black tracking-tight sm:text-3xl">
            Run a full Monthly Portfolio Review in minutes.
          </h2>
          <p className="mt-2 max-w-2xl text-sm leading-relaxed text-slate-300">
            Real agents on seeded demo files — duplicate claims submission (CP-1),
            −2.1% premium reconciliation (CP-2), a construction-severity finding,
            assumption-variance gate (CP-4), QA-verified draft report, final
            approval (CP-6).
          </p>
          <div className="mt-5 flex flex-wrap gap-3">
            <Button
              variant="light"
              size="lg"
              onClick={() => {
                setBusy(true);
                onLaunch();
              }}
              disabled={busy}
            >
              {busy ? (
                <>
                  <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-white" />
                  Launching…
                </>
              ) : (
                <>Start Guided Demo →</>
              )}
            </Button>
            <Link
              href="/workflows/new"
              className="inline-flex items-center justify-center gap-1.5 rounded-lg px-5 py-2.5 text-sm font-semibold text-slate-200 ring-1 ring-inset ring-white/20 transition-all hover:bg-white/10 hover:text-white"
            >
              Use your own CSVs
            </Link>
          </div>
          <dl className="mt-7 grid grid-cols-2 gap-3 sm:grid-cols-4">
            {DEMO_STEPS.map((s) => (
              <div
                key={s.n}
                className="rounded-xl bg-white/[0.06] p-3 ring-1 ring-inset ring-white/10"
              >
                <dt className="flex items-center gap-2 text-sm font-bold">
                  <span className="flex h-5 w-5 items-center justify-center rounded-full bg-blue-500/90 text-[11px] font-black">
                    {s.n}
                  </span>
                  {s.title}
                </dt>
                <dd className="mt-1 text-xs leading-relaxed text-slate-400">{s.text}</dd>
              </div>
            ))}
          </dl>
        </div>
      </div>
    </section>
  );
}

export default function Dashboard() {
  const router = useRouter();
  const [workflows, setWorkflows] = useState<WorkflowSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [healthy, setHealthy] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setInterval> | null = null;
    // Any successful API response proves the backend is awake — and keep
    // re-checking until healthy so a cold-start failure doesn't stick.
    const check = () =>
      api
        .health()
        .then(() => {
          if (!cancelled) {
            setHealthy(true);
            if (timer) clearInterval(timer);
          }
        })
        .catch(() => {
          if (!cancelled) setHealthy(false);
        });
    void check();
    timer = setInterval(check, 10000);
    api
      .listWorkflows()
      .then((b) => {
        if (cancelled) return;
        setWorkflows(b.workflows);
        setHealthy(true);
        if (timer) clearInterval(timer);
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message);
      });
    return () => {
      cancelled = true;
      if (timer) clearInterval(timer);
    };
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

  const actionable = (workflows ?? []).filter((w) => w.pending_checkpoints > 0);

  return (
    <main className="mx-auto max-w-6xl space-y-6 px-4 py-8 sm:px-8">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-black tracking-tight text-slate-900 sm:text-3xl">
            Dashboard
          </h1>
          <p className="mt-1 text-sm text-slate-500">
            AI prepares every review — you decide at each gate.
            {actionable.length > 0 && (
              <span className="ml-2 font-semibold text-amber-700">
                {actionable.length} review{actionable.length > 1 ? "s" : ""} waiting on you.
              </span>
            )}
          </p>
        </div>
        <Link
          href="/workflows/new"
          className="rounded-lg bg-ink-900 px-4 py-2 text-sm font-semibold text-white shadow-sm transition-all hover:bg-ink-800 active:scale-[0.98]"
        >
          + New Monthly Review
        </Link>
      </div>

      {healthy === false && (
        <Card className="animate-banner-in border-amber-300 bg-amber-50 p-4 text-sm text-amber-900">
          <span className="font-semibold">Waking backend…</span> the API is cold or
          unreachable. This page retries automatically; everything resumes once it
          responds.
        </Card>
      )}

      <DemoHero onLaunch={launchDemo} />

      <section>
        <SectionTitle
          hint={`${workflows?.length ?? "—"} total`}
        >
          Reviews
        </SectionTitle>
        {error && (
          <Card className="mb-3 border-red-300 bg-red-50 p-3 text-sm text-red-800">
            {error}
          </Card>
        )}
        {workflows === null && !error ? (
          <div className="grid gap-3">
            <SkeletonCard lines={2} />
            <SkeletonCard lines={2} />
          </div>
        ) : (
          <div className="stagger">
            <WorkflowList workflows={workflows ?? []} />
          </div>
        )}
      </section>
    </main>
  );
}
