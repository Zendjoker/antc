"use client";

import { Bell, Pause, Play, SkipBack, SkipForward, Timer, Volume2, VolumeX, X } from "lucide-react";
import { useState } from "react";
import { EmptyState } from "@/components/layout/primitives";
import { useFeedback } from "@/components/providers/feedback-provider";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { LiveSnapshot } from "@/lib/api/types";
import { cap } from "@/lib/format";

export function MediaPanel({ live, canAct }: { live: LiveSnapshot; canAct: boolean }) {
  const { act } = useFeedback();
  const np = live.now_playing;
  const vol = live.volume;
  const [drag, setDrag] = useState<number | null>(null);
  const commit = () => { if (drag != null) act({ do: "volume", percent: drag }, { quiet: true }).finally(() => setDrag(null)); };
  const level = drag ?? vol?.level ?? 0;
  const btn = (label: string, what: string, Icon: typeof Play) => (
    <Button size="icon" variant="ghost" aria-label={label} disabled={!canAct} onClick={() => act({ do: what }, { quiet: true })}><Icon /></Button>
  );
  return (
    <div>
      {!np?.title ? <EmptyState title={live.online ? "Nothing playing" : "Media unavailable"} description={live.online ? "Play something in Spotify or any media app." : undefined} /> : (
        <div className="flex items-center gap-3">
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-medium">{np.title}</div>
            <div className="truncate text-xs text-muted-foreground">{[np.artist, np.app].filter(Boolean).join(" · ")}</div>
          </div>
          <div className="flex">
            {btn("Previous", "previous", SkipBack)}
            {btn(np.playing ? "Pause" : "Play", "play_pause", np.playing ? Pause : Play)}
            {btn("Next", "next", SkipForward)}
          </div>
        </div>
      )}
      {vol && vol.level != null && (
        <div className="mt-3 flex items-center gap-2">
          <Button size="icon" variant="ghost" aria-label={vol.muted ? "Unmute" : "Mute"} disabled={!canAct} onClick={() => act({ do: vol.muted ? "unmute" : "mute" }, { quiet: true })}>{vol.muted ? <VolumeX /> : <Volume2 />}</Button>
          <input type="range" min={0} max={100} aria-label="Volume" className="flex-1 accent-primary" disabled={!canAct} value={vol.muted && drag == null ? 0 : level}
            onChange={(e) => setDrag(+e.target.value)} onPointerUp={commit} onKeyUp={commit} />
          <span className="w-10 text-right text-xs tabular-nums text-muted-foreground">{vol.muted ? "Muted" : `${level}%`}</span>
        </div>
      )}
    </div>
  );
}

interface TimerItem { id: string; label: string; kind: string; at: string; in_s: number; total_s?: number; daily?: boolean }

export function TimersPanel({ live, canAct }: { live: LiveSnapshot; canAct: boolean }) {
  const { act } = useFeedback();
  const timers = (live.timers ?? []) as TimerItem[];
  const [minutes, setMinutes] = useState("");
  const [label, setLabel] = useState("");

  async function start(e: React.FormEvent) {
    e.preventDefault();
    const m = parseFloat(minutes);
    if (!(m > 0)) return;
    const r = await act({ do: "timer", seconds: Math.round(m * 60), label: label.trim() });
    if (r.outcome === "completed") { setMinutes(""); setLabel(""); }
  }

  return (
    <div>
      {timers.length === 0 ? <EmptyState title={live.online ? "No timers" : "Timers unavailable"} description={live.online ? "Start one here, or just ask." : undefined} /> : (
        <ul className="divide-y">
          {timers.map((t) => (
            <li key={t.id} className="flex items-center gap-3 py-2 text-sm first:pt-0">
              {t.kind === "timer" ? <Timer className="size-4 text-muted-foreground" aria-hidden /> : <Bell className="size-4 text-muted-foreground" aria-hidden />}
              <div className="min-w-0 flex-1"><div className="truncate font-medium">{cap(t.label)}</div><div className="text-xs text-muted-foreground">{t.kind === "timer" ? `Rings at ${t.at}` : `Alarm${t.daily ? " · every day" : ""}`}</div></div>
              <Button size="icon" variant="ghost" aria-label={`Cancel ${t.label}`} disabled={!canAct} onClick={() => act({ do: "cancel_timer", id: t.id }, { quiet: true })}><X /></Button>
            </li>
          ))}
        </ul>
      )}
      <form onSubmit={start} className="mt-3 flex flex-wrap gap-2">
        <Input className="w-24" type="number" min={0} step="any" aria-label="Minutes" placeholder="Minutes" value={minutes} onChange={(e) => setMinutes(e.target.value)} />
        <Input className="min-w-32 flex-1" aria-label="Timer label" placeholder="Label (optional)" value={label} onChange={(e) => setLabel(e.target.value)} />
        <Button type="submit" variant="outline" disabled={!canAct || !(parseFloat(minutes) > 0)}>Start timer</Button>
      </form>
    </div>
  );
}
