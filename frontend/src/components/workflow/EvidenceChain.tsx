"use client";

import { Badge, Card } from "@/components/ui/primitives";
import { api, type EvidenceItem } from "@/lib/api";

/** Breadcrumb: Finding → Evidence → Calculation → Dataset → File (§11). */
export function EvidenceChainView({
  workflowId,
  item,
  index,
}: {
  workflowId: string;
  item: EvidenceItem;
  index: number;
}) {
  const chain = item.chain as
    | {
        metric?: {
          metric_key?: string;
          formula?: string;
          value?: number | null;
          dimensions?: Record<string, string>;
        } | null;
        datasets?: { id: string; kind?: string; storage_path?: string; row_count?: number }[];
        files?: { id: string; filename: string }[];
        error?: string;
      }
    | undefined;

  return (
    <Card className="overflow-hidden p-0">
      <div className="flex items-center gap-3 border-b border-slate-100 bg-slate-50/70 px-4 py-2.5">
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-ink-900 text-[11px] font-black text-white">
          {index + 1}
        </span>
        <span className="text-xs font-semibold text-slate-500">
          Finding <span className="text-slate-300">→</span> Evidence
        </span>
        <Badge tone="slate">{item.type}</Badge>
        <span className="ml-auto hidden text-[11px] text-slate-400 sm:inline">
          {item.created_at ? new Date(item.created_at).toLocaleString() : ""}
        </span>
      </div>
      <div className="p-4 text-sm">
        <p className="font-medium leading-relaxed text-slate-800">{item.description}</p>
        {item.type === "metric" && chain?.metric && (
          <div className="mt-3 space-y-2 rounded-xl bg-slate-50 p-3 text-[13px] ring-1 ring-inset ring-slate-200/60">
            <div className="flex flex-wrap items-baseline gap-x-2">
              <span className="text-xs font-bold uppercase tracking-wide text-slate-400">
                Metric
              </span>
              <code className="rounded bg-white px-1.5 py-0.5 font-mono text-xs font-semibold text-ink-900 ring-1 ring-inset ring-slate-200">
                {chain.metric.metric_key}
              </code>
              {chain.metric.dimensions &&
                Object.keys(chain.metric.dimensions).length > 0 && (
                  <span className="text-xs text-slate-500">
                    ({Object.values(chain.metric.dimensions).join(" · ")})
                  </span>
                )}
              {chain.metric.value != null && (
                <span className="ml-auto text-sm font-black tabular-nums text-slate-900">
                  {chain.metric.value}
                </span>
              )}
            </div>
            <div>
              <span className="text-xs font-bold uppercase tracking-wide text-slate-400">
                Calculation{" "}
              </span>
              <code className="font-mono text-xs text-slate-700">
                {chain.metric.formula ?? "—"}
              </code>
            </div>
            <details>
              <summary className="cursor-pointer text-xs font-medium text-slate-500 hover:text-slate-700">
                Frozen input snapshot
              </summary>
              <pre className="nice-scroll mt-1 max-h-40 overflow-auto rounded-lg bg-ink-950 p-2.5 font-mono text-[11px] leading-relaxed text-slate-200">
                {JSON.stringify(item.snapshot, null, 1)}
              </pre>
            </details>
            {(chain.datasets ?? []).map((d) => (
              <div key={d.id} className="flex flex-wrap items-center gap-x-2 text-xs text-slate-600">
                <span className="font-bold uppercase tracking-wide text-slate-400">
                  Dataset {d.kind}
                </span>
                <code className="font-mono">{d.storage_path}</code>
                {d.row_count != null && (
                  <span className="text-slate-400">· {d.row_count.toLocaleString()} rows</span>
                )}
              </div>
            ))}
            {(chain.files ?? []).length > 0 && (
              <div className="flex flex-wrap items-center gap-2 border-t border-slate-200/70 pt-2">
                <span className="text-xs font-bold uppercase tracking-wide text-slate-400">
                  Source files
                </span>
                {(chain.files ?? []).map((f) => (
                  <a
                    key={f.id}
                    className="inline-flex items-center gap-1 rounded-lg bg-white px-2.5 py-1 text-xs font-semibold text-blue-700 ring-1 ring-inset ring-blue-200 transition-colors hover:bg-blue-50"
                    href={api.downloadUrl(workflowId, f.id)}
                  >
                    ⬇ {f.filename}
                  </a>
                ))}
              </div>
            )}
            {chain.error && (
              <div className="text-xs font-medium text-red-700">{chain.error}</div>
            )}
          </div>
        )}
        {item.type === "knowledge" && item.document && (
          <div className="mt-3 flex items-center gap-2 rounded-xl bg-amber-50/60 p-3 text-[13px] ring-1 ring-inset ring-amber-200/60">
            <span className="text-base">📖</span>
            <span>
              <span className="font-semibold text-slate-800">{item.document.title}</span>{" "}
              <span className="text-slate-500">({item.document.version})</span>
            </span>
          </div>
        )}
        {item.type !== "metric" && item.type !== "knowledge" && (
          <div className="mt-3 rounded-xl bg-slate-50 p-3 ring-1 ring-inset ring-slate-200/60">
            <p className="mb-2 text-[11px] font-bold uppercase tracking-wide text-slate-400">
              Frozen snapshot
            </p>
            <dl className="grid gap-1.5 sm:grid-cols-2">
              {Object.entries(item.snapshot ?? {}).slice(0, 8).map(([k, v]) => (
                <div key={k} className="rounded-lg bg-white px-2.5 py-1.5 ring-1 ring-inset ring-slate-200/70">
                  <dt className="text-[10px] font-bold uppercase tracking-wide text-slate-400">
                    {k.replaceAll("_", " ")}
                  </dt>
                  <dd className="mt-0.5 break-words text-[13px] font-semibold text-slate-700">
                    {v === null || v === undefined
                      ? "—"
                      : typeof v === "number"
                        ? Number.isInteger(v) ? v.toLocaleString() : String(Math.round(v * 100) / 100)
                        : Array.isArray(v)
                          ? v.length <= 3 ? v.join(", ") : `${v.length} items`
                          : String(v).slice(0, 140)}
                  </dd>
                </div>
              ))}
            </dl>
            {Object.keys(item.snapshot ?? {}).length === 0 && (
              <p className="text-xs text-slate-400">No snapshot fields recorded.</p>
            )}
          </div>
        )}
      </div>
    </Card>
  );
}
