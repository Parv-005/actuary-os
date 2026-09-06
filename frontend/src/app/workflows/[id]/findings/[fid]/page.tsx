"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Badge, Button, Card, EmptyState, Spinner } from "@/components/ui/primitives";
import { EvidenceChainView } from "@/components/workflow/EvidenceChain";
import { FindingDecisionDialog } from "@/components/workflow/FindingDecisionDialog";
import { severityIcon } from "@/components/workflow/FindingsList";
import { api, type EvidenceItem, type FindingDetail } from "@/lib/api";

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
    <main className="mx-auto max-w-4xl space-y-4 p-8">
      <Link href={`/workflows/${id}`} className="text-sm text-slate-500 hover:text-slate-800">
        ← Back to workflow
      </Link>
      {!finding && !error && <Spinner label="Loading finding…" />}
      {error && (
        <Card className="border-red-300 bg-red-50 p-4 text-sm text-red-800">{error}</Card>
      )}
      {finding && (
        <>
          <header className="flex flex-wrap items-start justify-between gap-2">
            <h1 className="text-xl font-semibold">
              {severityIcon(finding.severity)} {finding.title}
            </h1>
            <div className="flex items-center gap-2">
              <Badge tone="blue">AI Observation — confidence {finding.confidence.toFixed(2)}</Badge>
              <Button variant="secondary" onClick={() => setDialog(true)}>
                Decide
              </Button>
            </div>
          </header>

          {finding.data_quality && (
            <Card className="border-yellow-300 bg-yellow-50 p-3 text-sm">
              Data-quality caveat: {finding.data_quality}
            </Card>
          )}

          <Card className="space-y-3 p-4">
            <NarrativeBlock finding={finding} />
            {finding.possible_drivers.length > 0 && (
              <div className="text-sm">
                <span className="font-medium">Possible drivers: </span>
                {finding.possible_drivers.join("; ")}
              </div>
            )}
            {finding.alternatives.length > 0 && (
              <div className="text-sm">
                <span className="font-medium">Alternatives considered: </span>
                {finding.alternatives.join("; ")}
              </div>
            )}
            {finding.correlation_caveat && (
              <p className="text-sm italic text-slate-600">{finding.correlation_caveat}</p>
            )}
          </Card>

          <section>
            <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-500">
              Evidence chain ({evidence?.length ?? 0})
            </h2>
            {!evidence || !evidence.length ? (
              <EmptyState message="No evidence attached." />
            ) : (
              <div className="grid gap-2">
                {evidence.map((e) => (
                  <EvidenceChainView key={e.id} workflowId={id} item={e} />
                ))}
              </div>
            )}
          </section>

          {prior.length > 0 && (
            <Card className="p-4 text-sm">
              <span className="font-medium">Repeat monitoring item — linked to prior: </span>
              {prior.map((p) => (
                <span key={p.id} className="ml-2">
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
        </>
      )}
    </main>
  );
}

function NarrativeBlock({ finding }: { finding: FindingDetail }) {
  const parts = splitNarrative(finding.narrative || "");
  const hasSplit = parts.evidence || parts.hypothesis || parts.conclusion;
  if (!hasSplit) {
    return <p className="whitespace-pre-wrap text-sm">{finding.narrative}</p>;
  }
  return (
    <div className="space-y-2 text-sm">
      {(
        [
          ["Evidence", parts.evidence],
          ["Hypothesis", parts.hypothesis],
          ["Conclusion", parts.conclusion],
        ] as const
      ).map(
        ([label, text]) =>
          text && (
            <div key={label}>
              <span className="font-medium">{label}: </span>
              <span className="whitespace-pre-wrap">{text}</span>
            </div>
          )
      )}
    </div>
  );
}
