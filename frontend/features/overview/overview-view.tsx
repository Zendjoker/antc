"use client";

import { CheckCircle2, Plug, Route, SendHorizonal, Timer, Undo2, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { WaterOrb } from "@/components/brand/water-orb";
import { orbState } from "@/components/layout/orb-state";
import { ErrorState, PageHeader, Panel, Skeleton } from "@/components/layout/primitives";
import { useFeedback } from "@/components/providers/feedback-provider";
import { Button } from "@/components/ui/button";
import { ActivityTimeline, TaskList } from "@/features/activity/activity-view";
import { STARTERS } from "@/features/assistant/assistant-view";
import { useChat } from "@/features/assistant/use-chat";
import { useLinkState, useLive } from "@/lib/api/queries";
import { cap } from "@/lib/format";
import { DevicesPanel } from "./devices-panel";
import { MediaPanel, TimersPanel } from "./media-timers";

const DONE = ["COMPLETED", "SUCCEEDED", "DONE", "FAILED", "CANCELLED", "STOPPED"];

function greeting(name?: string) {
  const h = new Date().getHours();
  const part = h < 5 ? "Good evening" : h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
  return name ? `${part}, ${name}` : part;
}

export function OverviewView() {
  const { data, error, isPending, refetch } = useLive();
  const link = useLinkState();
  const router = useRouter();
  const { send, pending } = useChat();
  const { act } = useFeedback();
  const [ask, setAsk] = useState("");

  if (isPending) {
    return (<><PageHeader title="Overview" /><div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-24" />)}</div></>);
  }
  if (error) {
    return (<><PageHeader title="Overview" /><Panel><ErrorState title="Can't reach the ZendAgent server" description={error.message} onRetry={() => refetch()} /></Panel></>);
  }

  const online = link === "online";
  const tasks = data.tasks ?? [];
  const running = tasks.filter((t) => !DONE.includes(t.state.toUpperCase()));
  const devices = data.home?.devices ?? [];
  const lastReply = [...(data.conversation ?? [])].reverse().find((m) => m.role === "assistant")?.text;
  const ring = data.ringing as { label: string; kind: string } | null | undefined;
  const undo = typeof data.undo === "string" ? data.undo : null;
  const lists = Object.entries(data.lists?.lists ?? {}).filter(([, items]) => items.length > 0);
  const needsYou = (data.missions ?? []).reduce((n, m) => n + (m.approvals_pending || 0), 0);

  async function submit(text: string) {
    if (await send(text)) { setAsk(""); router.push("/assistant"); }
  }

  return (
    <div className="space-y-4">
      <PageHeader
        kicker="ZEND / Control Center"
        title={greeting(data.user)}
        description={[new Date().toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" }), data.location].filter(Boolean).join(" · ")}
        actions={<Button variant="outline" size="sm" disabled={!online || !undo} title={undo ? `Undo: ${undo}` : "Nothing to undo"} onClick={() => act({ do: "undo" })}><Undo2 /> Undo</Button>}
      />

      {ring && (
        <div role="alert" className="flex items-center gap-3 rounded-2xl border border-primary/30 bg-tint px-4 py-3 text-sm text-secondary-foreground">
          <span className="flex-1 font-medium">{cap(ring.label)} {ring.kind === "alarm" ? "alarm" : "timer"} is ringing</span>
          <Button size="sm" onClick={() => act({ do: "stop_ringing" }, { quiet: true })}>Stop</Button>
        </div>
      )}

      <section className="relative overflow-hidden rounded-3xl border bg-card p-6 shadow-card sm:p-8" aria-label="Assistant">
        <div aria-hidden className="pointer-events-none absolute -left-32 top-1/2 size-[420px] -translate-y-1/2 rounded-full bg-[radial-gradient(circle,rgba(0,157,255,.08),transparent_65%)]" />
        <div className="relative grid items-center gap-6 md:grid-cols-[240px_1fr] md:gap-8">
          <div className="flex justify-center"><WaterOrb state={orbState(link, data.state?.name)} size={140} /></div>
          <div className="min-w-0">
            <div className="mb-2 flex items-center gap-2 text-[11px] font-bold uppercase tracking-[0.14em] text-muted-foreground">
              <span className={online ? "size-2 rounded-full bg-zend-secondary shadow-[0_0_0_3px_rgba(0,157,255,.16)]" : "size-2 rounded-full bg-muted-foreground/60"} /> ZEND assistant
            </div>
            <div className="mb-4 flex flex-wrap items-baseline gap-x-3">
              <b className="text-[26px] font-bold tracking-tight">{online ? cap((data.state?.name ?? "ready").replace(/_/g, " ")) : "Offline"}</b>
              <span className="text-sm text-muted-foreground">{online ? data.state?.label : "Start ZEND to talk"}</span>
            </div>
            <form onSubmit={(e) => { e.preventDefault(); void submit(ask); }} className="flex h-[54px] items-center rounded-[14px] border border-input bg-background/60 pl-[18px] pr-2 focus-within:border-primary/50 focus-within:bg-card focus-within:ring-4 focus-within:ring-primary/15">
              <input aria-label="Ask ZendAgent" className="min-w-0 flex-1 bg-transparent text-[15px] outline-none placeholder:text-muted-foreground disabled:opacity-60" placeholder={online ? "What can I take care of for you?" : "ZendAgent is offline"} value={ask} disabled={!online || !!pending} onChange={(e) => setAsk(e.target.value)} />
              <Button type="submit" size="icon" aria-label="Send" className="rounded-[10px]" disabled={!online || !ask.trim() || !!pending}><SendHorizonal /></Button>
            </form>
            {lastReply && <p className="mt-3.5 text-[14.5px] leading-relaxed text-sidebar-foreground">{lastReply}</p>}
            <div className="mt-3.5 flex flex-wrap gap-2">
              {STARTERS.map((s) => <Button key={s} size="sm" variant="outline" className="rounded-full bg-card" disabled={!online || !!pending} onClick={() => submit(s)}>{s}</Button>)}
            </div>
          </div>
        </div>
      </section>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Tile href="/connections" icon={Plug} label="Google connection" value={data.connections?.google ? cap(data.connections.google.split(" ")[0]) : "—"} small />
        <Tile href="/activity" icon={CheckCircle2} label="Active tasks" value={running.length} />
        <Tile href="/overview" icon={Timer} label="Timers & alarms" value={(data.timers ?? []).length} />
        <Tile href="/missions" icon={Route} label="Missions" value={data.missions?.length ?? 0} hint={needsYou ? `${needsYou} waiting for you` : undefined} />
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Current work">
          {running.length ? <TaskList tasks={running} /> : <p className="text-sm text-muted-foreground">Nothing running. <Link href="/activity" className="text-primary hover:underline">See recent tasks</Link></p>}
        </Panel>
        <Panel title="Recent activity"><ActivityTimeline items={data.activity ?? []} limit={6} /><Link href="/activity" className="mt-2 inline-block text-sm text-primary hover:underline">View all activity</Link></Panel>
        <Panel title="Now playing"><MediaPanel live={data} canAct={online} /></Panel>
        <Panel title="Timers & alarms"><TimersPanel live={data} canAct={online} /></Panel>
      </div>

      <Panel title="Home devices"><DevicesPanel devices={devices} online={!!data.online && !!data.home?.online} canAct={online} /></Panel>

      {lists.length > 0 && (
        <Panel title="Lists">
          <div className="grid gap-4 sm:grid-cols-2">
            {lists.map(([name, items]) => (
              <div key={name}><h3 className="mb-1 text-sm font-medium">{cap(name)}</h3><ul className="list-disc pl-5 text-sm text-muted-foreground">{items.map((it, i) => <li key={i}>{it}</li>)}</ul></div>
            ))}
          </div>
        </Panel>
      )}
    </div>
  );
}

function Tile({ href, icon: Icon, label, value, hint, small }: { href: string; icon: LucideIcon; label: string; value: React.ReactNode; hint?: string; small?: boolean }) {
  return (
    <Link href={href} className="grid min-h-[84px] grid-cols-[44px_1fr] items-center gap-3.5 rounded-[18px] border bg-card px-[18px] py-4 shadow-card transition hover:-translate-y-px hover:border-primary/40">
      <span className="grid size-11 place-items-center rounded-xl bg-tint text-primary"><Icon className="size-5" aria-hidden /></span>
      <span className="min-w-0">
        <span className="block text-[12.5px] text-muted-foreground">{label}</span>
        <b className={small ? "block truncate text-base font-semibold" : "block text-[22px] font-bold leading-tight tracking-tight"}>{value}</b>
        {hint && <span className="block text-xs font-medium text-warning">{hint}</span>}
      </span>
    </Link>
  );
}