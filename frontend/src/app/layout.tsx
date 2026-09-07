import type { Metadata } from "next";
import { Inter } from "next/font/google";
import Link from "next/link";
import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Vortex ActuaryOS",
  description: "Monthly Portfolio Review — human-in-the-loop multi-agent actuarial workflow",
};

function BrandMark() {
  return (
    <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-blue-500 to-ink-900 text-base font-black text-white shadow-sm">
      V
    </span>
  );
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={inter.variable}>
      <body className="flex min-h-screen flex-col bg-[#eef1f6] font-sans text-slate-900 antialiased">
        <header className="sticky top-0 z-40 border-b border-white/10 bg-ink-950/95 text-white backdrop-blur">
          <div className="mx-auto flex h-14 max-w-6xl items-center justify-between gap-4 px-4 sm:px-8">
            <Link href="/" className="flex items-center gap-2.5">
              <BrandMark />
              <span className="leading-tight">
                <span className="block text-[15px] font-bold tracking-tight">
                  Vortex ActuaryOS
                </span>
                <span className="block text-[11px] font-medium text-slate-400">
                  Monthly Portfolio Review
                </span>
              </span>
            </Link>
            <nav className="flex items-center gap-1 text-sm">
              <Link
                href="/"
                className="rounded-lg px-3 py-1.5 font-medium text-slate-300 transition-colors hover:bg-white/10 hover:text-white"
              >
                Dashboard
              </Link>
              <Link
                href="/workflows/new"
                className="rounded-lg px-3 py-1.5 font-medium text-slate-300 transition-colors hover:bg-white/10 hover:text-white"
              >
                New review
              </Link>
              <span className="ml-2 hidden rounded-full bg-white/10 px-2.5 py-1 text-[11px] font-semibold text-slate-300 ring-1 ring-inset ring-white/15 sm:inline">
                Demo environment
              </span>
            </nav>
          </div>
        </header>
        <div className="flex-1">{children}</div>
        <footer className="border-t border-slate-200/70 bg-white/60">
          <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-2 px-4 py-4 text-xs text-slate-500 sm:px-8">
            <span>
              <span className="font-semibold text-slate-700">Vortex ActuaryOS</span> — AI
              prepares, the actuary decides.
            </span>
            <span>Deterministic analytics · human-gated checkpoints · full audit trail</span>
          </div>
        </footer>
      </body>
    </html>
  );
}
