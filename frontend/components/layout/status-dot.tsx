"use client";

import { useLinkState, type LinkState } from "@/lib/api/queries";
import { cn } from "@/lib/utils";

const META: Record<LinkState, { label: string; dot: string }> = {
  online: { label: "Online", dot: "bg-success" },
  offline: { label: "Agent offline", dot: "bg-warning" },
  disconnected: { label: "Server unreachable", dot: "bg-destructive" },
  connecting: { label: "Connecting", dot: "bg-muted-foreground animate-pulse" },
};

export function StatusDot({ className }: { className?: string }) {
  const s = useLinkState();
  const m = META[s];
  return (
    <span role="status" aria-live="polite" className={cn("inline-flex items-center gap-2 text-sm text-muted-foreground", className)}>
      <span className={cn("size-2 rounded-full", m.dot)} aria-hidden />
      {m.label}
    </span>
  );
}
