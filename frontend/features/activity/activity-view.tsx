"use client";

import { Activity, Bell, Lightbulb, Mail, Music, Phone, Timer, Wrench } from "lucide-react";
import { useMemo, useState } from "react";
import { EmptyState, ErrorState, PageHeader, Panel, Skeleton } from "@/components/layout/primitives";
import { Badge, stateTone } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/switch-tabs";
import { useLive } from "@/lib/api/queries";
import type { ActivityItem, TaskItem } from "@/lib/api/types";
import { cap, clock, timeAgo, usd } from "@/lib/format";
import { cn } from "@/lib/utils";

const ICONS: Record<string, typeof Activity> = { timer: Timer, alarm: Bell, light: Lightbulb, mail: Mail, music: Music, call: Phone, phone: Phone, tool: Wrench };

export function dayKey(at: number, now = new Date()) {
  const d = new Date(at * 1000);
  if (d.toDateString() === now.toDateString()) return "Today";
  const y = new Date(now); y.setDate(now.getDate() - 1);
  if (d.toDateString() === y.toDateString()) return "Yesterday";
  return d.toLocaleDateString([], { weekday: "long", month: "short", day: "numeric" });
}

export function ActivityTimeline({ items, limit }: { items: ActivityItem[]; limit?: number }) {
  const list = limit ? items.slice(0, limit) : items;
  if (list.length === 0) return <EmptyState title="No recent activity" description="Actions ZendAgent takes will show up here." />;
  return (
    <ol className="space-y-0">
      {list.map((a, i) => {
        const day = dayKey(a.at);
        const header = i === 0 || day !== dayKey(list[i - 1].at) ? day : null;
        const Icon = ICONS[a.kind] ?? Activity;
        return (
          <li key={`${a.at}-${i}`}>
            {header && <div className="pb-1 pt-3 text-xs font-medium text-muted-foreground first:pt-0">{header}</div>}
            <div className="flex items-start gap-3 py-2 text-sm">
              <Icon className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
              <span className="flex-1">{a.text}</span>
              <time className="shrink-0 text-xs text-muted-foreground" title={new Date(a.at * 1000).toLocaleString()}>{clock(a.at)} · {timeAgo(a.at)}</time>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

export function ActivityView() {
  const { data, error, isPending, refetch } = useLive();
  const [kind, setKind] = useState("all");
  const items = useMemo(() => data?.activity ?? [], [data]);
  const kinds = useMemo(() => [...new Set(items.map((a) => a.kind).filter(Boolean))], [items]);
  const shown = kind === "all" ? items : items.filter((a) => a.kind === kind);

  if (isPending) return (<><PageHeader title="Activity" /><Skeleton className="h-48" /></>);
  if (error) return (<><PageHeader title="Activity" /><Panel><ErrorState title="Can't load activity" description={error.message} onRetry={() => refetch()} /></Panel></>);

  const tasks = data.tasks ?? [];
  return (
    <>
      <PageHeader title="Activity" description="What ZendAgent has done recently. Updates live." />
      {!data.online && <p role="status" className="mb-4 rounded-lg border border-warning/40 bg-warning/10 px-3 py-2 text-sm text-warning">ZendAgent isn&apos;t running. Showing the last known history.</p>}
      <Tabs defaultValue="events">
        <TabsList>
          <TabsTrigger value="events">Events ({items.length})</TabsTrigger>
          <TabsTrigger value="tasks">Tasks ({tasks.length})</TabsTrigger>
        </TabsList>
        <TabsContent value="events">
          {kinds.length > 1 && (
            <div className="mb-3 flex flex-wrap gap-1.5" role="group" aria-label="Filter by type">
              {["all", ...kinds].map((k) => (
                <button key={k} aria-pressed={kind === k} onClick={() => setKind(k)} className={cn("rounded-full border px-3 py-1 text-xs font-medium", kind === k ? "border-primary bg-primary/10 text-primary" : "text-muted-foreground hover:text-foreground")}>
                  {k === "all" ? "All" : cap(k)}
                </button>
              ))}
            </div>
          )}
          <Panel><ActivityTimeline items={shown} /></Panel>
        </TabsContent>
        <TabsContent value="tasks"><Panel><TaskList tasks={tasks} /></Panel></TabsContent>
      </Tabs>
    </>
  );
}

export function TaskList({ tasks }: { tasks: TaskItem[] }) {
  if (tasks.length === 0) return <EmptyState title="No tasks yet" description="Requests ZendAgent works on show up here with their steps." />;
  return (
    <ul className="divide-y">
      {tasks.map((t) => (
        <li key={t.id} className="py-3 first:pt-0 last:pb-0">
          <div className="flex items-start gap-2">
            <p className="flex-1 text-sm">{t.goal}</p>
            <Badge tone={stateTone(t.state)}>{cap(t.state.toLowerCase())}</Badge>
          </div>
          <div className="mt-1 flex flex-wrap gap-x-3 text-xs text-muted-foreground">
            <span>{cap(t.kind)}</span><span>{t.seconds.toFixed(1)}s</span><span>{usd(t.cost_usd)}</span><span>{timeAgo(t.updated)}</span>
          </div>
          {t.steps.length > 0 && (
            <ul className="mt-2 flex flex-wrap gap-1.5">
              {t.steps.map((s, i) => (
                <li key={i}><Badge tone={stateTone(String(s.state ?? ""))}>{String(s.tool ?? "step")}</Badge></li>
              ))}
            </ul>
          )}
        </li>
      ))}
    </ul>
  );
}
