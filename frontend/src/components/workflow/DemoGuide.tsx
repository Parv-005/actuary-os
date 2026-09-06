"use client";

import { useEffect, useState } from "react";
import { Button, Card, Modal, Spinner } from "@/components/ui/primitives";
import { api } from "@/lib/api";

interface Step {
  n: number;
  title: string;
  detail: string;
}

/** Guided-demo overlay (§11/§25): step checklist, progress in localStorage. */
export function DemoGuide({ workflowId }: { workflowId: string }) {
  const [open, setOpen] = useState(false);
  const [steps, setSteps] = useState<Step[] | null>(null);
  const [done, setDone] = useState<number[]>(() => {
    try {
      return JSON.parse(localStorage.getItem(`demo-guide-${workflowId}`) ?? "[]") as number[];
    } catch {
      return [];
    }
  });

  useEffect(() => {
    if (!open || steps) return;
    api
      .getDemoInstructions()
      .then((b) => setSteps(b.steps))
      .catch(() => setSteps([]));
  }, [open, steps]);

  function toggle(n: number) {
    const next = done.includes(n) ? done.filter((d) => d !== n) : [...done, n];
    setDone(next);
    localStorage.setItem(`demo-guide-${workflowId}`, JSON.stringify(next));
  }

  return (
    <>
      <Button variant="secondary" onClick={() => setOpen(true)}>
        Demo guide
      </Button>
      {open && (
        <Modal title="Guided demo — judge script (§25)" onClose={() => setOpen(false)}>
          {!steps ? (
            <Spinner label="Loading script…" />
          ) : (
            <ol className="space-y-2">
              {steps.map((s) => (
                <li key={s.n}>
                  <Card className={`p-3 text-sm ${done.includes(s.n) ? "opacity-60" : ""}`}>
                    <label className="flex cursor-pointer items-start gap-2">
                      <input
                        type="checkbox"
                        checked={done.includes(s.n)}
                        onChange={() => toggle(s.n)}
                        className="mt-1"
                      />
                      <span>
                        <span className="font-medium">
                          {s.n}. {s.title}
                        </span>
                        <span className="block text-slate-600">{s.detail}</span>
                      </span>
                    </label>
                  </Card>
                </li>
              ))}
            </ol>
          )}
          <p className="mt-3 text-xs text-slate-500">
            {done.length}/{steps?.length ?? 8} steps complete. Progress is stored in this
            browser only.
          </p>
        </Modal>
      )}
    </>
  );
}
