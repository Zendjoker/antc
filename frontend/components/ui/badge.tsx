import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

export type Tone = "neutral" | "success" | "warning" | "danger" | "info";

const TONES: Record<Tone, string> = {
  neutral: "bg-muted text-muted-foreground",
  success: "bg-success/15 text-success",
  warning: "bg-warning/15 text-warning",
  danger: "bg-destructive/15 text-destructive",
  info: "bg-primary/10 text-primary",
};

export function Badge({ tone = "neutral", children, className }: { tone?: Tone; children: ReactNode; className?: string }) {
  return <span className={cn("inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium", TONES[tone], className)}>{children}</span>;
}

/** Maps backend task/mission/step state strings to a tone. */
export function stateTone(state: string): Tone {
  const s = state.toLowerCase();
  if (["completed", "succeeded", "done", "connected", "ok"].includes(s)) return "success";
  if (["failed", "error", "stopped", "expired", "disconnected"].includes(s)) return "danger";
  if (["running", "executing", "in_progress", "active"].includes(s)) return "info";
  if (["paused", "pending", "waiting", "blocked", "skipped"].includes(s)) return "warning";
  return "neutral";
}
