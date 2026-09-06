"use client";

import { useState } from "react";
import { Button } from "@/components/ui/primitives";

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
        className={`rounded-lg border-2 border-dashed p-8 text-center text-sm ${
          drag ? "border-slate-500 bg-slate-100" : "border-slate-300 bg-white"
        } ${disabled ? "opacity-50" : ""}`}
      >
        <p className="font-medium">Drop CSVs here or</p>
        <label className="mt-2 inline-block">
          <span
            className={`cursor-pointer rounded px-4 py-2 text-sm font-medium ${
              disabled ? "bg-slate-300 text-white" : "bg-slate-900 text-white"
            }`}
          >
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
        <p className="mt-2 text-xs text-slate-500">
          claims.csv, premium.csv, exposure.csv (≤10MB, ≤20k rows each)
        </p>
      </div>
      {names.length > 0 && (
        <ul className="mt-3 space-y-1 text-sm">
          {names.map((n) => (
            <li key={n} className="flex justify-between rounded bg-slate-50 px-3 py-1.5">
              <span>{n}</span>
              <span className="text-slate-500">→ {guessKind(n)}</span>
            </li>
          ))}
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
