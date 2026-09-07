"use client";

import { useEffect, type ReactNode } from "react";

export function Card({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`rounded-xl border border-slate-200/80 bg-white shadow-card ${className}`}
    >
      {children}
    </div>
  );
}

export function Button({
  children,
  onClick,
  disabled,
  variant = "primary",
  size = "md",
  type = "button",
}: {
  children: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  variant?: "primary" | "secondary" | "danger" | "ghost" | "light";
  size?: "sm" | "md" | "lg";
  type?: "button" | "submit";
}) {
  const variants: Record<string, string> = {
    primary:
      "bg-ink-900 text-white shadow-sm hover:bg-ink-800 active:scale-[0.98] disabled:bg-slate-300 disabled:shadow-none",
    secondary:
      "border border-slate-300 bg-white text-slate-800 shadow-sm hover:border-slate-400 hover:bg-slate-50 active:scale-[0.98] disabled:opacity-50",
    danger:
      "bg-red-700 text-white shadow-sm hover:bg-red-600 active:scale-[0.98] disabled:bg-red-300",
    ghost: "text-slate-600 hover:bg-slate-100 active:scale-[0.98] disabled:opacity-50",
    light:
      "bg-white/15 text-white ring-1 ring-inset ring-white/30 backdrop-blur hover:bg-white/25 active:scale-[0.98] disabled:opacity-50",
  };
  const sizes: Record<string, string> = {
    sm: "px-3 py-1.5 text-xs",
    md: "px-4 py-2 text-sm",
    lg: "px-5 py-2.5 text-sm",
  };
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={`inline-flex items-center justify-center gap-1.5 rounded-lg font-semibold transition-all focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-2 ${variants[variant]} ${sizes[size]}`}
    >
      {children}
    </button>
  );
}

export type BadgeTone = "slate" | "red" | "yellow" | "green" | "blue" | "navy";

export function Badge({
  children,
  tone = "slate",
  dot = false,
}: {
  children: ReactNode;
  tone?: BadgeTone;
  dot?: boolean;
}) {
  const tones: Record<BadgeTone, string> = {
    slate: "bg-slate-100 text-slate-700 ring-slate-200",
    red: "bg-red-50 text-red-700 ring-red-200",
    yellow: "bg-amber-50 text-amber-800 ring-amber-200",
    green: "bg-emerald-50 text-emerald-700 ring-emerald-200",
    blue: "bg-blue-50 text-blue-700 ring-blue-200",
    navy: "bg-ink-900 text-white ring-ink-900",
  };
  const dots: Record<BadgeTone, string> = {
    slate: "bg-slate-400",
    red: "bg-red-500",
    yellow: "bg-amber-500",
    green: "bg-emerald-500",
    blue: "bg-blue-500",
    navy: "bg-white",
  };
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold ring-1 ring-inset ${tones[tone]}`}
    >
      {dot && <span className={`h-1.5 w-1.5 rounded-full ${dots[tone]}`} />}
      {children}
    </span>
  );
}

export function Spinner({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2.5 text-sm text-slate-500" role="status">
      <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-ink-900" />
      {label}
    </div>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`skeleton rounded-lg ${className}`} aria-hidden="true" />;
}

export function SkeletonCard({ lines = 3 }: { lines?: number }) {
  return (
    <div className="rounded-xl border border-slate-200/80 bg-white p-4 shadow-card" aria-hidden="true">
      <div className="skeleton h-4 w-1/3 rounded" />
      <div className="mt-3 space-y-2">
        {Array.from({ length: lines }).map((_, i) => (
          <div
            key={i}
            className="skeleton h-3 rounded"
            style={{ width: `${92 - i * 14}%` }}
          />
        ))}
      </div>
    </div>
  );
}

export function EmptyState({
  title,
  message,
  icon = "○",
}: {
  title?: string;
  message: string;
  icon?: string;
}) {
  return (
    <div className="rounded-xl border border-dashed border-slate-300 bg-white/60 px-6 py-10 text-center">
      <div className="mx-auto flex h-11 w-11 items-center justify-center rounded-full bg-slate-100 text-xl text-slate-400">
        {icon}
      </div>
      {title && <p className="mt-3 text-sm font-semibold text-slate-800">{title}</p>}
      <p className="mx-auto mt-1 max-w-sm text-sm text-slate-500">{message}</p>
    </div>
  );
}

export function SectionTitle({
  children,
  hint,
}: {
  children: ReactNode;
  hint?: ReactNode;
}) {
  return (
    <div className="mb-2 flex items-baseline justify-between gap-3">
      <h2 className="text-xs font-bold uppercase tracking-[0.08em] text-slate-500">
        {children}
      </h2>
      {hint && <div className="text-xs text-slate-400">{hint}</div>}
    </div>
  );
}

export function LivePill({ active, label }: { active: boolean; label: string }) {
  return (
    <span
      className={`inline-flex items-center gap-2 rounded-full px-3 py-1 text-xs font-semibold ${
        active ? "bg-emerald-50 text-emerald-700 ring-1 ring-inset ring-emerald-200" : "bg-slate-100 text-slate-500"
      }`}
    >
      <span className={`live-dot ${active ? "bg-emerald-500 text-emerald-500" : "bg-slate-400 text-slate-400"}`} />
      {label}
    </span>
  );
}

export function Modal({
  title,
  subtitle,
  children,
  onClose,
  wide = false,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
  onClose: () => void;
  wide?: boolean;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-ink-950/55 p-4 backdrop-blur-[2px]"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <div
        className={`animate-pop-in nice-scroll max-h-[90vh] w-full overflow-y-auto rounded-2xl bg-white p-6 shadow-pop ${
          wide ? "max-w-2xl" : "max-w-lg"
        }`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-start justify-between gap-4">
          <div>
            <h2 className="text-lg font-bold tracking-tight text-slate-900">{title}</h2>
            {subtitle && <p className="mt-0.5 text-sm text-slate-500">{subtitle}</p>}
          </div>
          <button
            onClick={onClose}
            className="rounded-lg px-2 py-1 text-lg leading-none text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-700"
            aria-label="Close"
          >
            ✕
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
