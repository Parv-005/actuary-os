"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ReferenceDot,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Card, EmptyState, Spinner } from "@/components/ui/primitives";
import { api, type MetricRow, type ValidationResult } from "@/lib/api";

interface TrendPoint {
  period: string;
  value: number | null;
  source: string;
}

type TrendKey = "loss_ratio" | "claim_frequency" | "claim_severity" | "ave_variance";

const TREND_TABS: { key: TrendKey; label: string; fmt: (v: number | null) => string; scale: (v: number | null) => number | null }[] = [
  { key: "loss_ratio", label: "Loss ratio", fmt: (v) => (v == null ? "—" : `${(v * 100).toFixed(1)}%`), scale: (v) => (v == null ? null : Math.round(v * 1000) / 10) },
  { key: "claim_frequency", label: "Frequency", fmt: (v) => (v == null ? "—" : v.toFixed(3)), scale: (v) => v },
  { key: "claim_severity", label: "Severity ₹", fmt: (v) => (v == null ? "—" : `₹${Math.round(v).toLocaleString()}`), scale: (v) => (v == null ? null : Math.round(v)) },
  { key: "ave_variance", label: "AvE (pp)", fmt: (v) => (v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(1)}pp`), scale: (v) => v },
];

function toPct(v: number | null): number | null {
  return v == null ? null : Math.round(v * 1000) / 10;
}

const tooltipStyle = {
  borderRadius: 10,
  border: "1px solid #e2e8f0",
  fontSize: 12,
  boxShadow: "0 8px 24px -8px rgb(15 36 68 / 0.25)",
};

const SEG_COLORS = ["#0f2444", "#1d4ed8", "#0ea5e9", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#64748b"];

/** Expanded analytics: selectable trend + segment bars + AvE + contribution + validation donut. */
export function ChartsPanel({
  workflowId,
  validation,
}: {
  workflowId: string;
  validation?: ValidationResult[] | null;
}) {
  const [trendKey, setTrendKey] = useState<TrendKey>("loss_ratio");
  const [trend, setTrend] = useState<TrendPoint[] | null>(null);
  const [metrics, setMetrics] = useState<MetricRow[] | null>(null);

  useEffect(() => {
    let live = true;
    setTrend(null);
    api
      .getSeries(workflowId, trendKey)
      .then((b) => live && setTrend(b.series))
      .catch(() => live && setTrend([]));
    return () => {
      live = false;
    };
  }, [workflowId, trendKey]);

  useEffect(() => {
    let live = true;
    api
      .getMetrics(workflowId)
      .then((b) => live && setMetrics(b.metrics))
      .catch(() => live && setMetrics([]));
    return () => {
      live = false;
    };
  }, [workflowId]);

  const tab = TREND_TABS.find((t) => t.key === trendKey)!;

  const segRows = useMemo(() => {
    if (!metrics) return null;
    return metrics
      .filter((m) => m.metric_key === trendKey && Object.keys(m.dimensions).length > 0 && m.value != null)
      .slice(0, 8)
      .map((m, i) => ({
        name: Object.values(m.dimensions).join(" / "),
        current: tab.scale(m.value),
        prior: tab.scale(m.prev_value),
        color: SEG_COLORS[i % SEG_COLORS.length],
      }));
  }, [metrics, trendKey, tab]);

  const aveRows = useMemo(() => {
    if (!metrics) return [];
    return metrics
      .filter((m) => m.metric_key === "ave_variance" && m.value != null)
      .slice(0, 6)
      .map((m) => ({
        name: Object.values(m.dimensions).join(" / ") || "Portfolio",
        ave: m.value,
        expected: m.expected_value ?? 0,
      }));
  }, [metrics]);

  const contribRows = useMemo(() => {
    if (!metrics) return [];
    return metrics
      .filter((m) => m.metric_key === "deterioration_contribution" && m.value != null)
      .sort((a, b) => (b.value ?? 0) - (a.value ?? 0))
      .slice(0, 6)
      .map((m) => ({
        name: Object.values(m.dimensions).join(" / ") || "Portfolio",
        value: Math.round((m.value ?? 0) * 10) / 10,
      }));
  }, [metrics]);

  const freqSevRows = useMemo(() => {
    if (!metrics) return [];
    const byDim = new Map<string, { name: string; freq: number | null; sev: number | null }>();
    for (const m of metrics) {
      if (m.metric_key !== "claim_frequency" && m.metric_key !== "claim_severity") continue;
      if (Object.keys(m.dimensions).length === 0 || m.value == null) continue;
      const name = Object.values(m.dimensions).join(" / ");
      if (!byDim.has(name)) byDim.set(name, { name, freq: null, sev: null });
      const row = byDim.get(name)!;
      if (m.metric_key === "claim_frequency") row.freq = m.value;
      else row.sev = m.value;
    }
    return Array.from(byDim.values()).slice(0, 6);
  }, [metrics]);

  if (trend === null || metrics === null || segRows === null)
    return <Spinner label="Loading charts…" />;
  if (!trend.length && !metrics.length && !(validation ?? []).length) return null;

  const trendData = trend.map((p) => ({ ...p, value: tab.scale(p.value) }));
  const lastComputed = [...trendData].reverse().find((p) => p.source === "computed");
  const valCounts = (validation ?? []).reduce<Record<string, number>>((a, r) => {
    a[r.status] = (a[r.status] ?? 0) + 1;
    return a;
  }, {});
  const valPie = ["PASS", "WARNING", "BLOCKER", "ACCEPTED_EXCEPTION"]
    .filter((s) => valCounts[s])
    .map((s) => ({
      name: s.replaceAll("_", " "),
      value: valCounts[s],
      fill: s === "PASS" ? "#10b981" : s === "WARNING" ? "#f59e0b" : s === "BLOCKER" ? "#ef4444" : "#94a3b8",
    }));

  return (
    <div className="space-y-4">
      {/* trend with metric switcher */}
      {trend.length > 0 && (
        <Card className="p-4 sm:p-5">
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-sm font-bold tracking-tight text-slate-800">
              Trend by period — <span className="text-blue-700">{tab.label}</span>
            </h3>
            <div className="flex gap-1 rounded-lg bg-slate-100 p-1" role="tablist" aria-label="Metric">
              {TREND_TABS.map((t) => (
                <button
                  key={t.key}
                  role="tab"
                  aria-selected={t.key === trendKey}
                  onClick={() => setTrendKey(t.key)}
                  className={`rounded-md px-2.5 py-1 text-xs font-bold transition-all ${
                    t.key === trendKey ? "bg-white text-slate-900 shadow-sm" : "text-slate-500 hover:text-slate-700"
                  }`}
                >
                  {t.label}
                </button>
              ))}
            </div>
          </div>
          <ResponsiveContainer width="100%" height={230}>
            <LineChart data={trendData} margin={{ top: 8, right: 8, bottom: 0, left: -8 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e6ebf2" vertical={false} />
              <XAxis dataKey="period" fontSize={11} tickLine={false} axisLine={{ stroke: "#e2e8f0" }} />
              <YAxis fontSize={11} tickLine={false} axisLine={false} domain={["auto", "auto"]} width={56} />
              <Tooltip
                contentStyle={tooltipStyle}
                formatter={(v: any) => [typeof v === "number" ? tab.fmt(trendKey === "loss_ratio" ? v / 100 : trendKey === "claim_severity" ? v : v) : "—", tab.label]}
                labelFormatter={(l) => `Period ${l}`}
              />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Line
                type="monotone"
                dataKey="value"
                name={tab.label}
                stroke="#1d4ed8"
                strokeWidth={2.5}
                dot={{ r: 3, fill: "#1d4ed8" }}
                activeDot={{ r: 5 }}
                connectNulls
              />
              {lastComputed && (
                <ReferenceDot
                  x={lastComputed.period}
                  y={lastComputed.value ?? undefined}
                  r={6}
                  fill="#10b981"
                  stroke="#fff"
                  strokeWidth={2}
                />
              )}
            </LineChart>
          </ResponsiveContainer>
          <p className="mt-1 text-xs text-slate-500">
            <span className="mr-1 inline-block h-2 w-2 rounded-full bg-emerald-500 align-middle" />
            Latest point is computed this run; earlier points are system-of-record history.
          </p>
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        {segRows.length > 0 && (
          <Card className="p-4 sm:p-5">
            <div className="mb-1 flex items-baseline justify-between gap-2">
              <h3 className="text-sm font-bold tracking-tight text-slate-800">
                {tab.label} by segment — current vs prior
              </h3>
              <span className="text-[11px] text-slate-400">top {segRows.length}</span>
            </div>
            <ResponsiveContainer width="100%" height={230}>
              <BarChart data={segRows} layout="vertical" margin={{ top: 8, right: 8, bottom: 0, left: 8 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e6ebf2" horizontal={false} />
                <XAxis type="number" fontSize={11} tickLine={false} axisLine={{ stroke: "#e2e8f0" }} />
                <YAxis type="category" dataKey="name" fontSize={10} width={130} tickLine={false} axisLine={false} />
                <Tooltip contentStyle={tooltipStyle} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Bar dataKey="current" name="Current" fill="#0f2444" radius={[0, 4, 4, 0]} />
                <Bar dataKey="prior" name="Prior" fill="#b9c6dd" radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </Card>
        )}

        {freqSevRows.length > 0 && (
          <Card className="p-4 sm:p-5">
            <div className="mb-1 flex items-baseline justify-between gap-2">
              <h3 className="text-sm font-bold tracking-tight text-slate-800">
                Frequency vs severity by cell
              </h3>
              <span className="text-[11px] text-slate-400">what moved — volume or size?</span>
            </div>
            <ResponsiveContainer width="100%" height={230}>
              <BarChart data={freqSevRows} margin={{ top: 8, right: 8, bottom: 0, left: -8 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e6ebf2" vertical={false} />
                <XAxis dataKey="name" fontSize={10} tickLine={false} axisLine={{ stroke: "#e2e8f0" }} interval={0} angle={-18} dy={10} height={52} />
                <YAxis yAxisId="freq" fontSize={11} tickLine={false} axisLine={false} width={44} />
                <YAxis yAxisId="sev" orientation="right" fontSize={11} tickLine={false} axisLine={false} width={52} tickFormatter={(v: number) => `₹${v >= 1000 ? `${Math.round(v / 1000)}k` : v}`} />
                <Tooltip contentStyle={tooltipStyle} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Bar yAxisId="freq" dataKey="freq" name="Frequency" fill="#0ea5e9" radius={[4, 4, 0, 0]} />
                <Bar yAxisId="sev" dataKey="sev" name="Severity ₹" fill="#f59e0b" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
            <p className="mt-1 text-xs text-slate-500">
              High frequency + flat severity = more claims. Flat frequency + high severity = bigger claims.
            </p>
          </Card>
        )}

        {aveRows.length > 0 && (
          <Card className="p-4 sm:p-5">
            <div className="mb-1 flex items-baseline justify-between gap-2">
              <h3 className="text-sm font-bold tracking-tight text-slate-800">
                Actual vs expected (AvE)
              </h3>
              <span className="text-[11px] text-slate-400">pp variance</span>
            </div>
            <ResponsiveContainer width="100%" height={210}>
              <BarChart data={aveRows.map((r) => ({ ...r, variance: Math.round((r.ave ?? 0) * 10) / 10 }))} margin={{ top: 8, right: 8, bottom: 0, left: -8 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e6ebf2" vertical={false} />
                <XAxis dataKey="name" fontSize={10} tickLine={false} axisLine={{ stroke: "#e2e8f0" }} interval={0} angle={-18} dy={10} height={52} />
                <YAxis fontSize={11} tickLine={false} axisLine={false} />
                <Tooltip contentStyle={tooltipStyle} formatter={(v: any) => [`${Number(v) >= 0 ? "+" : ""}${v}pp`, "AvE"]} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Bar dataKey="variance" name="AvE variance (pp)" radius={[4, 4, 0, 0]}>
                  {aveRows.map((r, i) => (
                    <Cell key={i} fill={(r.ave ?? 0) >= 0 ? "#ef4444" : "#10b981"} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
            <p className="mt-1 text-xs text-slate-500">
              <span className="font-semibold text-red-600">Red</span> = worse than expected ·{" "}
              <span className="font-semibold text-emerald-600">Green</span> = better.
            </p>
          </Card>
        )}

        {contribRows.length > 0 && (
          <Card className="p-4 sm:p-5">
            <div className="mb-1 flex items-baseline justify-between gap-2">
              <h3 className="text-sm font-bold tracking-tight text-slate-800">
                What drove the move? (contribution)
              </h3>
              <span className="text-[11px] text-slate-400">% of total change</span>
            </div>
            <ResponsiveContainer width="100%" height={210}>
              <BarChart data={contribRows} layout="vertical" margin={{ top: 8, right: 8, bottom: 0, left: 8 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e6ebf2" horizontal={false} />
                <XAxis type="number" fontSize={11} tickLine={false} axisLine={{ stroke: "#e2e8f0" }} tickFormatter={(v: number) => `${v}%`} />
                <YAxis type="category" dataKey="name" fontSize={10} width={130} tickLine={false} axisLine={false} />
                <Tooltip contentStyle={tooltipStyle} formatter={(v: any) => [`${v}%`, "Contribution"]} />
                <Bar dataKey="value" name="Share of move (%)" radius={[0, 4, 4, 0]}>
                  {contribRows.map((_, i) => (
                    <Cell key={i} fill={SEG_COLORS[i % SEG_COLORS.length]} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </Card>
        )}

        {valPie.length > 0 && (
          <Card className="p-4 sm:p-5">
            <h3 className="mb-1 text-sm font-bold tracking-tight text-slate-800">
              Data trust — validation mix
            </h3>
            <div className="flex items-center gap-3">
              <ResponsiveContainer width="45%" height={180}>
                <PieChart>
                  <Pie data={valPie} dataKey="value" nameKey="name" innerRadius={42} outerRadius={64} paddingAngle={2} />
                  <Tooltip contentStyle={tooltipStyle} />
                </PieChart>
              </ResponsiveContainer>
              <ul className="flex-1 space-y-1.5 text-[13px]">
                {valPie.map((s) => (
                  <li key={s.name} className="flex items-center gap-2">
                    <span className="h-2.5 w-2.5 rounded-full" style={{ background: s.fill }} />
                    <span className="font-semibold text-slate-700">{s.value}×</span>
                    <span className="text-slate-500">{s.name}</span>
                  </li>
                ))}
              </ul>
            </div>
          </Card>
        )}

        {(() => {
          const lr = metrics.find((m) => m.metric_key === "loss_ratio" && Object.keys(m.dimensions).length === 0);
          if (!lr || lr.value == null) return null;
          const pct = toPct(lr.value) ?? 0;
          const ring = Math.min(100, Math.max(0, pct));
          return (
            <Card className="flex items-center gap-4 p-4 sm:p-5">
              <div
                className="flex h-24 w-24 shrink-0 items-center justify-center rounded-full"
                style={{ background: `conic-gradient(#1d4ed8 ${ring}%, #e6ebf2 ${ring}% 100%)` }}
              >
                <div className="flex h-[76px] w-[76px] flex-col items-center justify-center rounded-full bg-white">
                  <span className="text-lg font-black tabular-nums text-slate-900">{pct.toFixed(1)}%</span>
                  <span className="text-[10px] font-bold uppercase tracking-wide text-slate-400">loss ratio</span>
                </div>
              </div>
              <div className="text-[13px] leading-relaxed text-slate-600">
                <p>
                  Portfolio LR <strong className="text-slate-900">{(lr.value * 100).toFixed(1)}%</strong>
                  {lr.prev_value != null && (
                    <>
                      {" "}vs <strong>{(lr.prev_value * 100).toFixed(1)}%</strong> prior
                      {lr.delta_pp != null && (
                        <span className={lr.delta_pp >= 0 ? "font-bold text-red-600" : "font-bold text-emerald-600"}>
                          {" "}({lr.delta_pp >= 0 ? "+" : ""}{lr.delta_pp.toFixed(1)}pp)
                        </span>
                      )}
                    </>
                  )}
                  .
                </p>
                <p className="mt-1 text-xs text-slate-400">
                  {lr.formula || "Σ incurred ÷ Σ earned premium"} · computed from frozen canonical datasets.
                </p>
              </div>
            </Card>
          );
        })()}
      </div>

      {!trend.length && !segRows.length && (
        <EmptyState
          icon="📊"
          title="Charts land after analysis"
          message="The analysis agent hasn't produced metrics yet — this panel fills in automatically while you watch."
        />
      )}
    </div>
  );
}
