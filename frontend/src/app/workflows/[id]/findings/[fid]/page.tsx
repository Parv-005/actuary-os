"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Badge, Button, Card, EmptyState, Spinner } from "@/components/ui/primitives";
import { EvidenceChainView } from "@/components/workflow/EvidenceChain";
import { FindingDecisionDialog } from "@/components/workflow/FindingDecisionDialog";
import { api, type EvidenceItem, type FindingDetail } from "@/lib/api";

const SEV_HERO: Record<string, string> = {
  high: "from-red-600 to-red-800",
  medium: "from-amber-500 to-amber-700",
  low: "from-emerald-500 to-emerald-700",
};

function splitNarrative(narrative: string): { evidence: string; hypothesis: string; conclusion: string; rest: string } {
  const out = { evidence: "", hypothesis: "", conclusion: "", rest: narrative };
  for (const key of ["evidence", "hypothesis", "conclusion"] as const) {
    const re = new RegExp(`${key}:\\s*([\\s\\S]*?)(?=(?:Evidence|Hypothesis|Conclusion):|$)`, "i");
    const m = narrative.match(re);
    if (m) out[key] = m[1].trim();
  }
  return out;
}

export default function FindingDetailPage({
  params,
}: {
  params: { id: string; fid: string };
}) {
  const { id, fid } = params;
  const [finding, setFinding] = useState<FindingDetail | null>(null);
  const [evidence, setEvidence] = useState<EvidenceItem[] | null>(null);
  const [prior, setPrior] = useState<{ id: string; title: string; status: string; workflow_ref: string | null }[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [dialog, setDialog] = useState(false);

  const load = useCallback(async () => {
    try {
      const body = await api.getFinding(id, fid);
      setFinding(body.finding);
      setEvidence(body.evidence);
      setPrior(
        (body.prior_findings as { id: string; title: string; status: string; workflow_ref: string | null }[]) ?? []
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "load failed");
    }
  }, [id, fid]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <main className="mx-auto max-w-4xl space-y-4 px-4 py-8 sm:px-8">
      <Link
        href={`/workflows/${id}`}
        className="inline-flex items-center gap-1 text-sm font-medium text-slate-500 transition-colors hover:text-slate-900"
      >
        ← Back to workflow
      </Link>
      {!finding && !error && <Spinner label="Loading finding…" />}
      {error && (
        <Card className="border-red-300 bg-red-50 p-4 text-sm text-red-800">{error}</Card>
      )}
      {finding && (
        <div className="stagger space-y-4">
          <section
            className={`overflow-hidden rounded-2xl bg-gradient-to-br text-white shadow-pop ${SEV_HERO[finding.severity] ?? "from-slate-600 to-slate-800"}`}
          >
            <div className="px-5 py-6 sm:px-7">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone="navy">{finding.severity} severity</Badge>
                <Badge tone="navy">AI Observation · {finding.confidence.toFixed(2)} confidence</Badge>
                <span className="ml-auto">
                  <Button variant="light" size="sm" onClick={() => setDialog(true)}>
                    Decide
                  </Button>
                </span>
              </div>
              <h1 className="mt-3 max-w-3xl text-xl font-black tracking-tight sm:text-2xl">
                {finding.title}
              </h1>
              {finding.decision_question && (
                <p className="mt-1.5 text-sm font-medium text-white/85">
                  ? {finding.decision_question}
                </p>
              )}
            </div>
          </section>

          {finding.data_quality && (
            <Card className="border-amber-300 bg-amber-50 p-4 text-sm text-amber-900">
              <span className="font-bold">Data-quality caveat: </span>
              {finding.data_quality}
            </Card>
          )}

          <Card className="space-y-4 p-5 sm:p-6">
            <NarrativeBlock finding={finding} />
            {finding.possible_drivers.length > 0 && (
              <div className="rounded-xl bg-slate-50 p-3.5 text-sm ring-1 ring-inset ring-slate-200/60">
                <span className="text-xs font-bold uppercase tracking-wide text-slate-500">
                  Possible drivers
                </span>
                <ul className="mt-1.5 list-disc space-y-0.5 pl-5 leading-relaxed text-slate-700">
                  {finding.possible_drivers.map((d, i) => (
                    <li key={i}>{d}</li>
                  ))}
                </ul>
              </div>
            )}
            {finding.alternatives.length > 0 && (
              <div className="text-sm">
                <span className="text-xs font-bold uppercase tracking-wide text-slate-500">
                  Alternatives considered
                </span>
                <p className="mt-1 leading-relaxed text-slate-600">
                  {finding.alternatives.join("; ")}
                </p>
              </div>
            )}
            {finding.correlation_caveat && (
              <p className="border-l-2 border-slate-200 pl-3 text-sm italic text-slate-500">
                {finding.correlation_caveat}
              </p>
            )}
          </Card>

          <section>
            <div className="mb-2 flex items-baseline justify-between">
              <h2 className="text-xs font-bold uppercase tracking-[0.08em] text-slate-500">
                Evidence chain
              </h2>
              <span className="text-xs text-slate-400">
                {evidence?.length ?? 0} item{(evidence?.length ?? 0) === 1 ? "" : "s"} · frozen at
                finding time
              </span>
            </div>
            {!evidence || !evidence.length ? (
              <EmptyState message="No evidence attached." />
            ) : (
              <div className="grid gap-3">
                {evidence.map((e, i) => (
                  <EvidenceChainView key={e.id} workflowId={id} item={e} index={i} />
                ))}
              </div>
            )}
          </section>

          {prior.length > 0 && (
            <Card className="border-blue-200 bg-blue-50/60 p-4 text-sm">
              <span className="font-bold text-slate-800">
                ⟡ Repeat monitoring item — linked to prior:{" "}
              </span>
              {prior.map((p) => (
                <span key={p.id} className="ml-1 text-slate-700">
                  {p.title} ({p.workflow_ref ?? "prior"} · {p.status})
                </span>
              ))}
            </Card>
          )}

          {dialog && (
            <FindingDecisionDialog
              workflowId={id}
              findingId={fid}
              findingTitle={finding.title}
              onApplied={load}
              onClose={() => setDialog(false)}
            />
          )}
        </div>
      )}
    </main>
  );
}

const NARRATIVE_TABS = [
  ["Evidence", "evidence"],
  ["Hypothesis", "hypothesis"],
  ["Conclusion", "conclusion"],
] as const;

function NarrativeBlock({ finding }: { finding: FindingDetail }) {
  const parts = splitNarrative(finding.narrative || "");
  const hasSplit = parts.evidence || parts.hypothesis || parts.conclusion;
  if (!hasSplit) {
    return (
      <p className="whitespace-pre-wrap text-[15px] leading-relaxed text-slate-800">
        {finding.narrative}
      </p>
    );
  }
  return (
    <div className="grid gap-2.5 sm:grid-cols-3">
      {NARRATIVE_TABS.map(([label, key]) =>
        parts[key] ? (
          <div
            key={label}
            className="rounded-xl bg-slate-50 p-3.5 ring-1 ring-inset ring-slate-200/60"
          >
            <div className="text-xs font-bold uppercase tracking-wide text-blue-800">
              {label}
            </div>
            <p className="mt-1 whitespace-pre-wrap text-sm leading-relaxed text-slate-700">
              {parts[key]}
            </p>
          </div>
        ) : null
      )}
    </div>
  );
}
