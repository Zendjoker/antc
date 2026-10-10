"use client";

import { DoorOpen, Lightbulb, Power, Thermometer, User, Vibrate, Zap, type LucideIcon } from "lucide-react";
import { useState } from "react";
import { EmptyState } from "@/components/layout/primitives";
import { useFeedback } from "@/components/providers/feedback-provider";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch-tabs";
import type { HomeDevice } from "@/lib/api/types";
import { timeAgo } from "@/lib/format";
import { cn } from "@/lib/utils";

export const SWATCHES: { name: string; hex: string; body: Record<string, string> }[] = [
  { name: "warm", hex: "#ffc778", body: { white: "warm" } },
  { name: "white", hex: "#f4f4f4", body: { white: "neutral" } },
  { name: "red", hex: "#ff3b30", body: { color: "red" } },
  { name: "orange", hex: "#ff9500", body: { color: "orange" } },
  { name: "green", hex: "#34c759", body: { color: "green" } },
  { name: "blue", hex: "#0a84ff", body: { color: "blue" } },
  { name: "purple", hex: "#8e5cff", body: { color: "purple" } },
  { name: "pink", hex: "#ff4fa3", body: { color: "pink" } },
];

const ICONS: Record<string, LucideIcon> = { contact: DoorOpen, climate: Thermometer, vibration: Vibrate, presence: User, light: Lightbulb, switch: Power };

interface Device extends HomeDevice {
  name: string; kind: string; battery?: number | null; open?: boolean; opened?: number; temperature_text?: string; humidity?: number;
  moved?: number; present?: boolean; on?: boolean; brightness?: number; summary?: string; color?: boolean; effects?: string[];
}

export function DevicesPanel({ devices, online, canAct }: { devices: HomeDevice[]; online: boolean; canAct: boolean }) {
  if (!online || devices.length === 0) {
    return <EmptyState title={online ? "No devices connected" : "Home is offline"} description={online ? "Start Zigbee2MQTT and pair your sensors." : undefined} />;
  }
  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
      {(devices as Device[]).map((d) => <DeviceTile key={d.name} d={d} canAct={canAct} />)}
    </div>
  );
}

function DeviceTile({ d, canAct }: { d: Device; canAct: boolean }) {
  const { act } = useFeedback();
  const [level, setLevel] = useState<number | null>(null);
  const Icon = ICONS[d.kind] ?? Zap;
  const control = d.kind === "light" || d.kind === "switch";
  const send = (extra: Record<string, unknown>) => act({ do: "light", device: d.name, ...extra }, { quiet: true });
  const commit = () => { if (level != null) send({ brightness: level }).finally(() => setLevel(null)); };

  return (
    <div className={cn("rounded-lg border p-3", d.kind === "contact" && d.open && "border-warning/60 bg-warning/5", control && d.on && "border-primary/40 bg-primary/5")}>
      <div className="flex items-center gap-2 text-sm">
        <Icon className="size-4 text-muted-foreground" aria-hidden />
        <span className="flex-1 truncate font-medium">{d.name}</span>
        {d.battery != null && d.battery <= 20 && <span className="text-xs text-warning">{d.battery}% battery</span>}
      </div>
      {d.kind === "contact" && <Reading main={d.open ? "Open" : "Closed"} sub={d.open && d.opened ? `since ${timeAgo(d.opened)}` : d.opened ? `last opened ${timeAgo(d.opened)}` : ""} />}
      {d.kind === "climate" && <Reading main={d.temperature_text || "—"} sub={d.humidity != null ? `${Math.round(d.humidity)}% humidity` : ""} />}
      {d.kind === "vibration" && <Reading main={d.moved ? timeAgo(d.moved) : "No movement"} sub={d.moved ? "last movement" : "since ZendAgent started"} />}
      {d.kind === "presence" && <Reading main={d.present ? "Someone's there" : "Empty"} />}
      {control && (
        <>
          <div className="mt-2 flex items-center justify-between">
            <span className="text-lg font-semibold">{d.on ? "On" : "Off"}</span>
            <Switch aria-label={`${d.name} power`} checked={!!d.on} disabled={!canAct} onCheckedChange={(on) => send({ on })} />
          </div>
          {d.on && d.summary && <p className="text-xs text-muted-foreground">{d.summary.replace(/^on,?\s*/, "")}</p>}
          {d.kind === "light" && (
            <div className="mt-3 space-y-2">
              <input
                type="range" min={1} max={100} className="w-full accent-primary" aria-label={`${d.name} brightness`} disabled={!canAct}
                value={level ?? d.brightness ?? 1}
                onChange={(e) => setLevel(+e.target.value)}
                onPointerUp={commit}
                onKeyUp={commit}
              />
              {d.color && (
                <div className="flex flex-wrap items-center gap-1.5">
                  {SWATCHES.map((s) => (
                    <button key={s.name} type="button" title={s.name} aria-label={`${d.name}: ${s.name}`} disabled={!canAct} onClick={() => send(s.body)} className="size-6 rounded-full border border-black/10 disabled:opacity-50" style={{ background: s.hex }} />
                  ))}
                  {(d.effects ?? []).some((e) => /rainbow/i.test(e)) && <Button size="xs" variant="outline" disabled={!canAct} onClick={() => send({ effect: "rainbow" })}>Rainbow</Button>}
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function Reading({ main, sub }: { main: string; sub?: string }) {
  return <div className="mt-2"><div className="text-lg font-semibold">{main}</div>{sub && <div className="text-xs text-muted-foreground">{sub}</div>}</div>;
}
