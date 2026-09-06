export default function Dashboard() {
  return (
    <main className="mx-auto max-w-4xl p-8">
      <h1 className="text-2xl font-semibold">Vortex ActuaryOS</h1>
      <p className="mt-2 text-slate-600">
        Monthly Portfolio Review — dashboard lands in Phase 14. Backend API:{" "}
        <code className="rounded bg-slate-200 px-1">
          {process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}
        </code>
      </p>
    </main>
  );
}
