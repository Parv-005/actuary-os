"use client";

import { Card, Badge } from "@/components/ui/primitives";
import { api, type EvidenceItem } from "@/lib/api";

/** Breadcrumb: Finding → Evidence → Calculation → Dataset → File (§11). */
export function EvidenceChainView({
  workflowId,
  item,
}: {
  workflowId: string;
  item: EvidenceItem;
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
    <Card className="p-3 text-sm">
      <div className="flex flex-wrap items-center gap-1 text-xs text-slate-500">
        <span>Evidence</span>
        <span>→</span>
        <Badge tone="slate">{item.type}</Badge>
      </div>
      <p className="mt-1">{item.description}</p>
      {item.type === "metric" && chain?.metric && (
        <div className="mt-2 rounded bg-slate-50 p-2 text-xs">
          <div>
            <span className="font-medium">Metric: </span>
            <code>{chain.metric.metric_key}</code>
            {chain.metric.dimensions &&
              Object.keys(chain.metric.dimensions).length > 0 && (
                <span className="text-slate-600">
                  {" "}
                  ({Object.values(chain.metric.dimensions).join(" · ")})
                </span>
              )}
            {chain.metric.value != null && (
              <span> = <strong>{chain.metric.value}</strong></span>
            )}
          </div>
          <div className="mt-1">
            <span className="font-medium">Calculation: </span>
            <code>{chain.metric.formula ?? "—"}</code>
          </div>
          <div className="mt-1">
            <span className="font-medium">Snapshot: </span>
            <code className="whitespace-pre-wrap">
              {JSON.stringify(item.snapshot, null, 1)}
            </code>
          </div>
          {(chain.datasets ?? []).map((d) => (
            <div key={d.id} className="mt-1">
              <span className="font-medium">Dataset ({d.kind}): </span>
              <code>{d.storage_path}</code>
              {d.row_count != null && (
                <span className="text-slate-500"> · {d.row_count} rows</span>
              )}
            </div>
          ))}
          {(chain.files ?? []).length > 0 && (
            <div className="mt-1">
              <span className="font-medium">Source files: </span>
              {(chain.files ?? []).map((f) => (
                <a
                  key={f.id}
                  className="ml-2 text-blue-700 underline"
                  href={api.downloadUrl(workflowId, f.id)}
                >
                  {f.filename} ⬇
                </a>
              ))}
            </div>
          )}
          {chain.error && <div className="text-red-700">{chain.error}</div>}
        </div>
      )}
      {item.type === "knowledge" && item.document && (
        <div className="mt-2 rounded bg-slate-50 p-2 text-xs">
          <span className="font-medium">Methodology: </span>
          {item.document.title} ({item.document.version})
        </div>
      )}
      {item.type !== "metric" && item.type !== "knowledge" && (
        <pre className="mt-2 max-h-40 overflow-auto rounded bg-slate-50 p-2 text-xs">
          {JSON.stringify(item.snapshot, null, 2)}
        </pre>
      )}
    </Card>
  );
}
