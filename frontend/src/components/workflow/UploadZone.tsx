"use client";

import { useState } from "react";
import { Button } from "@/components/ui/primitives";

const KIND_STYLE: Record<string, string> = {
  claims: "bg-blue-50 text-blue-700 ring-blue-200",
  premium: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  exposure: "bg-violet-50 text-violet-700 ring-violet-200",
  unknown: "bg-slate-100 text-slate-500 ring-slate-200",
};

/** Drag-drop CSV picker with filename-heuristic kind preview (§11, [ID]). */
export function UploadZone({
  onFiles,
  disabled,
}: {
  onFiles: (files: File[]) => void;
  disabled?: boolean;
}) {
  const [names, setNames] = useState<string[]>([]);
  const [drag, setDrag] = useState(false);

  function guessKind(name: string): string {
    const n = name.toLowerCase();
    if (n.includes("claim")) return "claims";
    if (n.includes("premium")) return "premium";
    if (n.includes("expos")) return "exposure";
    return "unknown";
  }

  function handle(list: FileList | null) {
    if (!list) return;
    const files = Array.from(list).filter((f) =>
      f.name.toLowerCase().endsWith(".csv")
    );
    setNames(files.map((f) => f.name));
    onFiles(files);
  }

  return (
    <div>
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDrag(true);
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDrag(false);
          if (!disabled) handle(e.dataTransfer.files);
        }}
        className={`rounded-2xl border-2 border-dashed p-8 text-center transition-all sm:p-10 ${
          drag
            ? "scale-[1.01] border-blue-500 bg-blue-50/60"
            : "border-slate-300 bg-slate-50/50 hover:border-slate-400 hover:bg-slate-50"
        } ${disabled ? "opacity-50" : ""}`}
      >
        <div
          aria-hidden="true"
          className="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl bg-ink-900 text-xl text-white shadow-sm"
        >
          ⇪
        </div>
        <p className="mt-3 text-[15px] font-bold tracking-tight text-slate-900">
          {drag ? "Drop to attach" : "Drop CSVs here or browse"}
        </p>
        <label className="mt-3 inline-block">
          <span className="inline-flex cursor-pointer items-center rounded-lg bg-ink-900 px-4 py-2 text-sm font-semibold text-white shadow-sm transition-all hover:bg-ink-800 active:scale-[0.98]">
            Browse files
          </span>
          <input
            type="file"
            accept=".csv"
            multiple
            className="hidden"
            disabled={disabled}
            onChange={(e) => handle(e.target.files)}
          />
        </label>
        <p className="mt-3 text-xs text-slate-500">
          claims · premium · exposure — CSV, ≤10MB and ≤20k rows each
        </p>
      </div>
      {names.length > 0 && (
        <ul className="mt-3 space-y-1.5 text-sm">
          {names.map((n) => {
            const kind = guessKind(n);
            return (
              <li
                key={n}
                className="animate-fade-up flex items-center justify-between gap-3 rounded-xl bg-white px-3.5 py-2 ring-1 ring-inset ring-slate-200"
              >
                <span className="min-w-0 truncate font-medium text-slate-800">{n}</span>
                <span
                  className={`shrink-0 rounded-full px-2.5 py-0.5 text-xs font-bold ring-1 ring-inset ${KIND_STYLE[kind]}`}
                >
                  → {kind}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

export function UploadButton({
  label,
  onClick,
  disabled,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <Button onClick={onClick} disabled={disabled}>
      {label}
    </Button>
  );
}
