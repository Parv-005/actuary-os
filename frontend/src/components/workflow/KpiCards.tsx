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

function DeltaPill({ delta, suffix }: { delta: number | null | undefined; suffix: string }) {
  if (delta == null) return null;
  const up = delta >= 0;
  return (
    <span
      className={`inline-flex items-center gap-0.5 rounded-full px-2 py-0.5 text-xs font-bold tabular-nums ${
        up ? "bg-red-50 text-red-700" : "bg-emerald-50 text-emerald-700"
      }`}
      title="Change vs prior period"
    >
      {up ? "▲" : "▼"} {up ? "+" : ""}
      {delta.toFixed(1)}
      {suffix}
    </span>
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
      delta: lr?.delta_pp ?? null,
      suffix: "pp",
      sub:
        lr?.prev_value != null
          ? `was ${formatPct(lr.prev_value)}`
          : dimLabel(lr?.dimensions ?? {}),
    },
    {
      label: "Actual vs expected",
      value:
        ave?.value != null
          ? `${ave.value >= 0 ? "+" : ""}${ave.value.toFixed(1)}pp`
          : "—",
      delta: null,
      suffix: "pp",
      sub: "vs 62.8% expected",
    },
    {
      label: "Severity trend",
      value:
        sev?.delta_pp != null
          ? `${sev.delta_pp >= 0 ? "+" : ""}${sev.delta_pp.toFixed(1)}%`
          : "—",
      delta: sev?.delta_pp ?? null,
      suffix: "%",
      sub: dimLabel(sev?.dimensions ?? {}),
    },
    {
      label: "Frequency trend",
      value:
        freq?.delta_pp != null
          ? `${freq.delta_pp >= 0 ? "+" : ""}${freq.delta_pp.toFixed(1)}%`
          : "—",
      delta: freq?.delta_pp ?? null,
      suffix: "%",
      sub: dimLabel(freq?.dimensions ?? {}),
    },
  ];
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      {cards.map((c) => (
        <Card key={c.label} className="p-4 sm:p-5">
          <div className="text-[11px] font-bold uppercase tracking-[0.08em] text-slate-500">
            {c.label}
          </div>
          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            <span className="text-[26px] font-black tabular-nums tracking-tight text-slate-900">
              {c.value}
            </span>
            <DeltaPill delta={c.delta} suffix={c.suffix} />
          </div>
          <div className="mt-1 truncate text-xs text-slate-500" title={c.sub}>
            {c.sub}
          </div>
        </Card>
      ))}
    </div>
  );
}
