"use client";

import { Card, Spinner } from "@/components/ui/primitives";
import { dimLabel, formatPct, type MetricRow } from "@/lib/api";

function find(
  metrics: MetricRow[],
  key: string,
  dims: Record<string, string> = {}
): MetricRow | undefined {
  const want = JSON.stringify(dims);
  return metrics.find(
    (m) => m.metric_key === key && JSON.stringify(m.dimensions) === want
  );
}

/** Portfolio KPI cards: LR, AvE, severity headline (§11 KpiCards). */
export function KpiCards({
  metrics,
  loading,
}: {
  metrics: MetricRow[] | null;
  loading: boolean;
}) {
  if (loading) return <Spinner label="Loading metrics…" />;
  if (!metrics) return null;
  const lr = find(metrics, "loss_ratio");
  const ave = find(metrics, "ave_variance");
  const sev = find(metrics, "claim_severity");
  const freq = find(metrics, "claim_frequency");
  const cards = [
    {
      label: "Loss ratio",
      value: formatPct(lr?.value ?? null),
      sub:
        lr?.prev_value != null
          ? `was ${formatPct(lr.prev_value)} (${lr.delta_pp != null && lr.delta_pp >= 0 ? "+" : ""}${lr.delta_pp?.toFixed(1)}pp)`
          : dimLabel(lr?.dimensions ?? {}),
    },
    {
      label: "Actual vs expected",
      value:
        ave?.value != null
          ? `${ave.value >= 0 ? "+" : ""}${ave.value.toFixed(1)}pp`
          : "—",
      sub: "vs 62.8% expected",
    },
    {
      label: "Severity trend",
      value:
        sev?.delta_pp != null
          ? `${sev.delta_pp >= 0 ? "+" : ""}${sev.delta_pp.toFixed(1)}%`
          : "—",
      sub: dimLabel(sev?.dimensions ?? {}),
    },
    {
      label: "Frequency trend",
      value:
        freq?.delta_pp != null
          ? `${freq.delta_pp >= 0 ? "+" : ""}${freq.delta_pp.toFixed(1)}%`
          : "—",
      sub: dimLabel(freq?.dimensions ?? {}),
    },
  ];
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      {cards.map((c) => (
        <Card key={c.label} className="p-4">
          <div className="text-xs uppercase tracking-wide text-slate-500">
            {c.label}
          </div>
          <div className="mt-1 text-2xl font-semibold">{c.value}</div>
          <div className="mt-1 text-xs text-slate-500">{c.sub}</div>
        </Card>
      ))}
    </div>
  );
}
