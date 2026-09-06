"use client";

import { useEffect, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Card, Spinner } from "@/components/ui/primitives";
import { api, type MetricRow } from "@/lib/api";

interface TrendPoint {
  period: string;
  value: number | null;
  source: string;
}

function toPct(v: number | null): number | null {
  return v == null ? null : Math.round(v * 1000) / 10;
}

/** Recharts: LR trend by period (?series=) + segment current-vs-prior bars. */
export function ChartsPanel({ workflowId }: { workflowId: string }) {
  const [trend, setTrend] = useState<TrendPoint[] | null>(null);
  const [segments, setSegments] = useState<MetricRow[] | null>(null);

  useEffect(() => {
    let live = true;
    api
      .getSeries(workflowId, "loss_ratio")
      .then((b) => live && setTrend(b.series))
      .catch(() => live && setTrend([]));
    api
      .getMetrics(workflowId, {
        metric_key: "loss_ratio",
        group_by: "product,segment,region",
      })
      .then((b) => live && setSegments(b.metrics))
      .catch(() => live && setSegments([]));
    return () => {
      live = false;
    };
  }, [workflowId]);

  if (trend === null || segments === null)
    return <Spinner label="Loading charts…" />;
  if (!trend.length && !segments.length) return null;

  const segRows = segments
    .filter((m) => m.value != null)
    .slice(0, 8)
    .map((m) => ({
      name: Object.values(m.dimensions).join("/"),
      current: toPct(m.value),
      prior: toPct(m.prev_value),
    }));

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      {trend.length > 0 && (
        <Card className="p-4">
          <h3 className="mb-2 text-sm font-medium">Loss ratio trend by period</h3>
          <ResponsiveContainer width="100%" height={220}>
            <LineChart
              data={trend.map((p) => ({
                ...p,
                value: toPct(p.value),
              }))}
            >
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="period" fontSize={11} />
              <YAxis fontSize={11} tickFormatter={(v: number) => `${v}%`} />
              <Tooltip formatter={(v) => [`${v}%`, "LR"]} />
              <Legend />
              <Line type="monotone" dataKey="value" name="Loss ratio (%)" stroke="#0f172a" dot />
            </LineChart>
          </ResponsiveContainer>
          <p className="mt-1 text-xs text-slate-500">
            History points: system-of-record; latest point: computed (see KPI cards).
          </p>
        </Card>
      )}
      {segRows.length > 0 && (
        <Card className="p-4">
          <h3 className="mb-2 text-sm font-medium">Loss ratio by segment: current vs prior</h3>
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={segRows} layout="vertical">
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis type="number" fontSize={11} tickFormatter={(v: number) => `${v}%`} />
              <YAxis type="category" dataKey="name" fontSize={10} width={130} />
              <Tooltip formatter={(v) => [`${v}%`, ""]} />
              <Legend />
              <Bar dataKey="current" name="Current (%)" fill="#0f172a" />
              <Bar dataKey="prior" name="Prior (%)" fill="#94a3b8" />
            </BarChart>
          </ResponsiveContainer>
        </Card>
      )}
    </div>
  );
}
