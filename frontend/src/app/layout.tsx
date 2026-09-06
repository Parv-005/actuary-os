import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Vortex ActuaryOS",
  description: "Monthly Portfolio Review — human-in-the-loop multi-agent actuarial workflow",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-slate-50 text-slate-900">{children}</body>
    </html>
  );
}
