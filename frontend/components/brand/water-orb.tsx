"use client";

import { cn } from "@/lib/utils";

export type OrbState = string;

/** The water orb. `state` is the assistant's real state name. */
export function WaterOrb({ state, size = 140, className }: { state: OrbState; size?: number; className?: string }) {
  const live = ["listening", "speaking", "idle_check", "call"].includes(state);
  return (
    <div
      data-live={live}
      className={cn("relative grid shrink-0 place-items-center", className)}
      style={{ width: size * 1.7, height: size * 1.7 }}
      aria-hidden
    >
      <span className="zend-orbit" style={{ width: size * 1.33, height: size * 1.33 }} />
      <span className="zend-orbit two" style={{ width: size * 1.6, height: size * 1.6 }} />
      <div className="zend-orb" data-state={state} style={{ width: size, height: size }}><span /></div>
    </div>
  );
}

export function MiniOrb({ state }: { state: OrbState }) {
  return <div className="zend-orb sm shrink-0" data-state={state}><span /></div>;
}
