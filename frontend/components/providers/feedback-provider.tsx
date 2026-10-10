"use client";

import { useQueryClient } from "@tanstack/react-query";
import { AlertCircle, CheckCircle2, Clock, X } from "lucide-react";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { dedupedAction, type BackendResult } from "@/lib/api/actions";
import { cn } from "@/lib/utils";

export type ToastKind = "ok" | "err" | "pending";
interface ToastItem { id: number; message: string; kind: ToastKind }

interface Feedback {
  toast: (message: string, kind?: ToastKind) => void;
  /** Run a protected backend action. Toasts the outcome unless quiet, then refreshes live data. */
  act: (body: Record<string, unknown>, opts?: { quiet?: boolean }) => Promise<BackendResult>;
  busy: boolean;
}

const Ctx = createContext<Feedback | null>(null);
const ICON = { ok: CheckCircle2, err: AlertCircle, pending: Clock } as const;

export function FeedbackProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const [inflight, setInflight] = useState(0);
  const seq = useRef(0);
  const timers = useRef(new Set<ReturnType<typeof setTimeout>>());

  useEffect(() => { const t = timers.current; return () => t.forEach(clearTimeout); }, []);

  const toast = useCallback((message: string, kind: ToastKind = "ok") => {
    const id = ++seq.current;
    setToasts((l) => [...l, { id, message, kind }]);
    const t = setTimeout(() => setToasts((l) => l.filter((x) => x.id !== id)), kind === "err" ? 5000 : 3000);
    timers.current.add(t);
  }, []);

  const act = useCallback<Feedback["act"]>(async (body, opts) => {
    setInflight((n) => n + 1);
    try {
      const r = await dedupedAction(body);
      if (!opts?.quiet || !r.ok) {
        toast(
          r.outcome === "accepted" ? "Request accepted; completion is pending."
            : r.message || (r.ok ? "ZendAgent confirmed the request." : "Request failed."),
          r.ok && r.outcome === "completed" ? "ok" : r.ok ? "pending" : "err",
        );
      }
      await qc.invalidateQueries({ queryKey: ["live"] });
      return r;
    } finally {
      setInflight((n) => n - 1);
    }
  }, [qc, toast]);

  const value = useMemo(() => ({ toast, act, busy: inflight > 0 }), [toast, act, inflight]);

  return (
    <Ctx.Provider value={value}>
      {children}
      <div role="status" aria-live="polite" className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-80 max-w-[calc(100vw-2rem)] flex-col gap-2">
        {toasts.map((t) => {
          const Icon = ICON[t.kind];
          return (
            <div key={t.id} className={cn("pointer-events-auto flex items-start gap-2 rounded-lg border bg-popover p-3 text-sm text-popover-foreground shadow-lg", t.kind === "err" && "border-destructive/50")}>
              <Icon className={cn("mt-0.5 size-4 shrink-0", t.kind === "ok" && "text-success", t.kind === "err" && "text-destructive", t.kind === "pending" && "text-warning")} aria-hidden />
              <span className="flex-1">{t.message}</span>
              <button aria-label="Dismiss" onClick={() => setToasts((l) => l.filter((x) => x.id !== t.id))} className="text-muted-foreground hover:text-foreground"><X className="size-4" /></button>
            </div>
          );
        })}
      </div>
    </Ctx.Provider>
  );
}

export function useFeedback() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useFeedback must be used inside FeedbackProvider");
  return v;
}
