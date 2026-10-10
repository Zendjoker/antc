"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Check, DollarSign, ExternalLink, Pause, Play, Plus, RefreshCw, Square, X } from "lucide-react";
import { useState, type ReactNode } from "react";
import { EmptyState, ErrorState, PageHeader, Panel, Skeleton, Stat } from "@/components/layout/primitives";
import { useConfirm } from "@/components/providers/confirm-provider";
import { useFeedback } from "@/components/providers/feedback-provider";
import { Badge, stateTone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/switch-tabs";
import { useLinkState, useLive, useMission } from "@/lib/api/queries";
import type { MissionDetail, MissionSummary } from "@/lib/api/types";
import { cap, timeAgo, usd } from "@/lib/format";
import { cn } from "@/lib/utils";

const label = (s: string) => cap(s.replace(/_/g, " "));
const RUNNING = ["running", "planned"];
const PAUSED = ["paused", "paused_budget", "paused_daily", "interrupted"];
const CLOSED = ["completed", "cancelled", "finished_with_problems"];

const RESOLVE_LABEL: Record<string, string> = {
  retry: "Retry (may cost again)", accept: "Accept as is", check: "Check Gmail again", mark_created: "It's in Gmail",
  mark_not_created: "It's not in Gmail", keep_mine: "Keep my edits", use_jarvis: "Use ZendAgent's version",
};
const RISKY_RESOLVE = ["retry", "use_jarvis"];

export function MissionsView() {
  const { data: live, isPending, error, refetch } = useLive();
  const missions = live?.missions ?? [];
  const [picked, setPicked] = useState<string | null>(null);
  const id = picked && missions.some((m) => m.id === picked) ? picked : (missions[0]?.id ?? null);

  if (isPending) return (<><PageHeader title="Missions" /><Skeleton className="h-40" /></>);
  if (error) return (<><PageHeader title="Missions" /><Panel><ErrorState title="Can't load missions" description={error.message} onRetry={() => refetch()} /></Panel></>);
  if (!live.online) return (<><PageHeader title="Missions" /><Panel><EmptyState title="ZendAgent isn't running" description="Start it to see its missions." /></Panel></>);
  if (missions.length === 0) return (<><PageHeader title="Missions" /><Panel><EmptyState title="No missions yet" description="Longer jobs you hand to ZendAgent show up here with their progress, spending and approvals." /></Panel></>);

  return (
    <>
      <PageHeader title="Missions" description="Progress, spending and decisions for long-running work." />
      <div className="grid gap-4 lg:grid-cols-[16rem_1fr]">
        <nav aria-label="Missions" className="flex gap-2 overflow-x-auto lg:flex-col lg:overflow-visible">
          {missions.map((m) => (
            <button
              key={m.id}
              aria-current={m.id === id ? "true" : undefined}
              onClick={() => setPicked(m.id)}
              className={cn("min-w-44 rounded-xl border bg-card p-3 text-left transition-colors hover:border-primary/50", m.id === id && "border-primary ring-1 ring-primary/30")}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-sm font-medium">{m.title || cap(m.kind)}</span>
                <Badge tone={stateTone(m.state)}>{label(m.state)}</Badge>
              </div>
              <div className="mt-1 truncate text-xs text-muted-foreground">{m.current || (m.summary || "").split("\n")[0]}</div>
              {m.approvals_pending > 0 && <div className="mt-1 text-xs font-medium text-warning">{m.approvals_pending} waiting for you</div>}
            </button>
          ))}
        </nav>
        {id && <MissionDetailView key={id} id={id} summary={missions.find((m) => m.id === id)!} />}
      </div>
    </>
  );
}

function MissionDetailView({ id, summary }: { id: string; summary: MissionSummary }) {
  const running = RUNNING.includes(summary.state);
  const { data, error, isPending, refetch, isFetching } = useMission(id, running);
  const qc = useQueryClient();
  const confirm = useConfirm();
  const { act } = useFeedback();
  const online = useLinkState() === "online";
  const [busy, setBusy] = useState(false);

  async function run(body: Record<string, unknown>, ask?: { title: string; description?: string; confirmLabel: string; destructive?: boolean }) {
    if (ask && !(await confirm(ask))) return;
    setBusy(true);
    try {
      const r = await act({ id, ...body });
      // Local preview servers only: never open an arbitrary URL from a response.
      if (r.outcome === "completed" && typeof r.url === "string" && /^http:\/\/127\.0\.0\.1:\d+\//.test(r.url)) window.open(r.url, "_blank", "noopener");
    } finally {
      setBusy(false);
      await qc.invalidateQueries({ queryKey: ["mission", id] });
    }
  }

  if (isPending) return <Skeleton className="h-64" />;
  if (error || !data) return <Panel><ErrorState title="Couldn't load this mission" description={error?.message} onRetry={() => refetch()} /></Panel>;

  const m = data.mission;
  const off = !online || busy;
  const pct = m.steps_total ? Math.round((100 * (m.steps_done + m.steps_failed)) / m.steps_total) : 0;
  const pending = data.approvals.filter((a) => a.status === "pending");
  const unknown = data.approvals.filter((a) => a.status === "unknown");
  const needs = pending.length + unknown.length + data.attention.length;

  return (
    <div className="min-w-0 space-y-4">
      <Panel>
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-base font-semibold">{m.title || cap(m.kind)}</h2>
          <Badge tone={stateTone(m.state)}>{label(m.state)}</Badge>
          <Button size="icon" variant="ghost" className="ml-auto" aria-label="Refresh mission" onClick={() => refetch()}><RefreshCw className={isFetching ? "animate-spin" : ""} /></Button>
        </div>
        <p className="mt-1 text-sm text-muted-foreground">{m.current ? `Now: ${m.current}` : (m.summary || "").split("\n")[0]}</p>
        <div className="mt-3 h-2 overflow-hidden rounded-full bg-muted" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label="Mission progress">
          <div className="h-full bg-primary transition-all" style={{ width: `${pct}%` }} />
        </div>
        <div className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
          <Stat label="Found" value={m.leads_found ?? 0} />
          <Stat label="Checked" value={m.leads_researched ?? 0} />
          <Stat label={`Qualify (aim ${m.target ?? "?"})`} value={m.leads_qualifying ?? 0} />
          <Stat label="Demos / drafts" value={`${m.demos ?? 0} / ${m.drafts ?? 0}`} />
          <Stat label="Spent" value={usd(m.spent_usd)} hint={`of ${usd(m.budget_usd)} budget`} />
          <Stat label="Steps" value={`${m.steps_done}/${m.steps_total}`} hint={m.steps_failed ? `${m.steps_failed} failed` : undefined} />
          <Stat label="Requests" value={m.requests ?? 0} />
          {data.owner && !data.owner.owner && <Stat label="Owner" value="Other process" hint="Controls may not apply" />}
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          {RUNNING.includes(m.state) && <Button size="sm" variant="outline" disabled={off} onClick={() => run({ do: "mission_pause" })}><Pause /> Pause</Button>}
          {PAUSED.includes(m.state) && <Button size="sm" variant="outline" disabled={off} onClick={() => run({ do: "mission_resume" })}><Play /> Resume</Button>}
          {m.state === "paused_budget" && <Button size="sm" variant="outline" disabled={off} onClick={() => run({ do: "mission_budget", extra_usd: 1 })}><DollarSign /> Add $1 and resume</Button>}
          {(m.leads_qualifying || 0) > (m.demos || 0) && <Button size="sm" variant="outline" disabled={off} onClick={() => run({ do: "mission_demos", count: 3 })}><Plus /> Build 3 more demos</Button>}
          {!CLOSED.includes(m.state) && (
            <Button size="sm" variant="destructive" disabled={off} onClick={() => run({ do: "mission_stop" }, { title: "Stop this mission?", description: "The work done so far is kept, but it won't continue.", confirmLabel: "Stop mission", destructive: true })}><Square /> Stop</Button>
          )}
        </div>
        {m.errors?.length > 0 && <div className="mt-3 space-y-1 rounded-lg bg-destructive/10 p-3 text-sm text-destructive">{m.errors.slice(-3).map((e, i) => <div key={i}>{e}</div>)}</div>}
        {m.workspace && <p className="mt-3 break-all text-xs text-muted-foreground">Files: {m.workspace}</p>}
      </Panel>

      <Tabs defaultValue={needs ? "decisions" : "steps"}>
        <TabsList>
          <TabsTrigger value="decisions">Needs you{needs ? ` (${needs})` : ""}</TabsTrigger>
          <TabsTrigger value="steps">Steps</TabsTrigger>
          <TabsTrigger value="leads">Leads ({data.leads.length})</TabsTrigger>
          <TabsTrigger value="work">Drafts & demos</TabsTrigger>
          <TabsTrigger value="history">History</TabsTrigger>
        </TabsList>

        <TabsContent value="decisions"><Decisions data={data} off={off} run={run} /></TabsContent>
        <TabsContent value="steps">
          <Panel>
            {data.steps.length === 0 ? <EmptyState title="No steps yet" /> : (
              <ul className="divide-y">
                {data.steps.slice(-80).map((s) => (
                  <li key={s.key} className="py-2.5 first:pt-0 last:pb-0">
                    <div className="flex items-center gap-2 text-sm">
                      <Badge tone={stateTone(s.state)}>{label(s.state)}</Badge>
                      <span className="flex-1">{s.title}</span>
                      <span className="text-xs text-muted-foreground">{s.attempts > 1 ? `${s.attempts} tries ` : ""}{s.cost_usd ? `$${s.cost_usd.toFixed(4)}` : ""}</span>
                    </div>
                    {(s.error || s.evidence) && <p className={cn("mt-1 text-xs", s.error && s.state !== "completed" ? "text-warning" : "text-muted-foreground")}>{s.error || s.evidence}</p>}
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </TabsContent>
        <TabsContent value="leads"><Leads data={data} /></TabsContent>
        <TabsContent value="work"><Work data={data} off={off} run={run} /></TabsContent>
        <TabsContent value="history"><History data={data} /></TabsContent>
      </Tabs>
    </div>
  );
}

type Run = (body: Record<string, unknown>, ask?: { title: string; description?: string; confirmLabel: string; destructive?: boolean }) => Promise<void>;

function Section({ title, children }: { title: string; children: ReactNode }) {
  return <div className="mb-5 last:mb-0"><h3 className="mb-2 text-sm font-medium text-muted-foreground">{title}</h3>{children}</div>;
}

function Decisions({ data, off, run }: { data: MissionDetail; off: boolean; run: Run }) {
  const pending = data.approvals.filter((a) => a.status === "pending");
  const unknown = data.approvals.filter((a) => a.status === "unknown");
  const crit = data.goal?.criteria ?? [];
  return (
    <Panel>
      <Section title="Approvals">
        {pending.length + unknown.length === 0 && <p className="text-sm text-muted-foreground">Nothing waiting.</p>}
        <ul className="space-y-3">
          {unknown.map((a) => (
            <li key={a.id} className="rounded-lg border p-3 text-sm">
              <p>{a.summary}: outcome unknown ({a.result || "check Gmail's Drafts first"})</p>
              <Button className="mt-2" size="sm" variant="outline" disabled={off} onClick={() => run({ do: "approval_retry", approval_id: a.id }, { title: "Retry this draft?", description: "Only retry if the draft is not in Gmail's Drafts, or it may be created twice.", confirmLabel: "Retry" })}>Not in Gmail: retry</Button>
            </li>
          ))}
          {pending.map((a) => (
            <li key={a.id} className="rounded-lg border p-3 text-sm">
              <p>{a.summary}</p>
              <div className="mt-2 flex gap-2">
                <Button size="sm" disabled={off} onClick={() => run({ do: "approval_approve", approval_id: a.id }, { title: "Approve this?", description: `${a.summary}\n\nApproving only creates a Gmail draft. Nothing is sent.`, confirmLabel: "Approve" })}><Check /> Approve (Gmail draft, not sent)</Button>
                <Button size="sm" variant="ghost" disabled={off} onClick={() => run({ do: "approval_reject", approval_id: a.id })}><X /> Reject</Button>
              </div>
            </li>
          ))}
        </ul>
      </Section>

      <Section title="Needs your decision">
        {data.attention.length === 0 && <p className="text-sm text-muted-foreground">Nothing needs your decision.</p>}
        <ul className="space-y-3">
          {data.attention.map((x) => (
            <li key={`${x.type}-${x.id}`} className="rounded-lg border p-3 text-sm">
              <p className="font-medium">{x.type} {x.id}: {x.what}</p>
              <p className="mt-0.5 text-muted-foreground">{x.why}</p>
              <div className="mt-2 flex flex-wrap gap-2">
                {x.actions.map((a) => (
                  <Button key={a} size="sm" variant={RISKY_RESOLVE.includes(a) ? "destructive" : "outline"} disabled={off}
                    onClick={() => run({ do: "mission_resolve", item_type: x.type, item_id: x.id, action: a }, RISKY_RESOLVE.includes(a) ? { title: `${RESOLVE_LABEL[a] ?? a}?`, description: x.what, confirmLabel: RESOLVE_LABEL[a] ?? a, destructive: true } : undefined)}>
                    {RESOLVE_LABEL[a] ?? a}
                  </Button>
                ))}
              </div>
            </li>
          ))}
        </ul>
        <Button className="mt-3" size="sm" variant="outline" disabled={off} onClick={() => run({ do: "mission_reconcile" })}><RefreshCw /> Check now (read-only)</Button>
      </Section>

      {(data.goal?.understood || crit.length > 0) && (
        <Section title="Goal">
          {data.goal?.understood && <p className="mb-2 text-sm">Understood: {data.goal.understood}</p>}
          <ul className="space-y-1.5">
            {crit.map((c) => (
              <li key={c.id} className="flex items-center gap-2 text-sm">
                <Badge tone={c.met ? "success" : "warning"}>{c.met ? "Met" : "Not yet"}</Badge>
                <span className="flex-1">{c.what}</span>
                <span className="text-xs text-muted-foreground">{c.metric === "sent" ? `${c.got} sent` : `${c.got ?? "?"} / ${c.target}`}</span>
              </li>
            ))}
          </ul>
          {data.goal?.constraints?.length ? <p className="mt-2 text-xs text-muted-foreground">Your limits: {data.goal.constraints.join("; ")}</p> : null}
        </Section>
      )}
    </Panel>
  );
}

function Leads({ data }: { data: MissionDetail }) {
  if (data.leads.length === 0) return <Panel><EmptyState title="No leads yet" /></Panel>;
  return (
    <Panel>
      <ul className="divide-y">
        {data.leads.map((l) => (
          <li key={l.id} className="py-3 first:pt-0 last:pb-0">
            <details>
              <summary className="flex cursor-pointer items-center gap-2 text-sm">
                <Badge tone="info">{l.score}</Badge>
                <span className="flex-1 font-medium">{l.name}</span>
                <Badge tone={stateTone(l.status)}>{label(l.status)}</Badge>
              </summary>
              <div className="mt-2 space-y-1 pl-1 text-sm text-muted-foreground">
                {l.address && <p>{l.address}</p>}
                {l.phone && <p>{l.phone}</p>}
                {l.email && <p>{l.email}</p>}
                {l.website && <p className="break-all">{l.website} {l.website_status && `(${l.website_status})`}</p>}
                {l.missing?.length ? <p>Missing: {l.missing.join(", ")}</p> : null}
                {l.evidence?.map((e, i) => <p key={i} className="text-xs">“{e}”</p>)}
              </div>
            </details>
          </li>
        ))}
      </ul>
    </Panel>
  );
}

function Work({ data, off, run }: { data: MissionDetail; off: boolean; run: Run }) {
  return (
    <Panel>
      <Section title="Outreach drafts">
        {data.outreach.length === 0 ? <p className="text-sm text-muted-foreground">No drafts yet.</p> : (
          <ul className="space-y-2">
            {data.outreach.map((o) => (
              <li key={o.id}>
                <details className="rounded-lg border p-3 text-sm">
                  <summary className="flex cursor-pointer items-center gap-2"><span className="flex-1 font-medium">{o.subject || o.kind}</span><Badge tone={o.status === "draft" ? "neutral" : "success"}>{label(o.status)}</Badge></summary>
                  <p className="mt-2 text-muted-foreground">To: {o.recipient || "no public email found"}</p>
                  <pre className="mt-2 whitespace-pre-wrap font-sans">{o.body}</pre>
                  {o.problems?.length ? <ul className="mt-2 list-disc pl-5 text-warning">{o.problems.map((p, i) => <li key={i}>{p}</li>)}</ul> : null}
                </details>
              </li>
            ))}
          </ul>
        )}
      </Section>
      <Section title="Demo sites">
        {data.projects.length === 0 ? <p className="text-sm text-muted-foreground">No demo sites yet.</p> : (
          <ul className="space-y-2">
            {data.projects.map((p) => (
              <li key={p.id} className="flex items-center gap-3 rounded-lg border p-3 text-sm">
                <div className="min-w-0 flex-1"><div className="font-medium">{p.lead}</div><div className="truncate text-xs text-muted-foreground">{p.path}</div></div>
                <Button size="sm" variant="outline" disabled={off} onClick={() => run({ do: "mission_preview", project_id: p.id })}><ExternalLink /> Open preview</Button>
              </li>
            ))}
          </ul>
        )}
      </Section>
    </Panel>
  );
}

function History({ data }: { data: MissionDetail }) {
  return (
    <Panel>
      <Section title="Activity log">
        {data.events.length === 0 ? <p className="text-sm text-muted-foreground">No events yet.</p> : (
          <ul className="space-y-1.5">
            {data.events.map((e, i) => (
              <li key={i} className="flex gap-3 text-sm">
                <span className="w-16 shrink-0 text-xs text-muted-foreground">{timeAgo(e.at)}</span>
                <span className={cn(e.level === "error" && "text-destructive", e.level === "warn" && "text-warning")}>{e.text}</span>
              </li>
            ))}
          </ul>
        )}
      </Section>
      <Section title={`Operations (${data.operations.length})`}>
        {data.operations.length === 0 ? <p className="text-sm text-muted-foreground">No operations yet.</p> : (
          <ul className="space-y-1.5">
            {[...data.operations].reverse().map((o) => (
              <li key={o.id} className="text-sm">
                <div className="flex items-center gap-2"><Badge tone={o.state === "uncertain" ? "warning" : stateTone(o.state)}>{o.state}</Badge><span className="flex-1">{o.kind}: {o.what}</span><span className="text-xs text-muted-foreground">{o.id.slice(0, 12)}</span></div>
                {o.error && <p className="mt-0.5 text-xs text-warning">{o.error}</p>}
              </li>
            ))}
          </ul>
        )}
      </Section>
    </Panel>
  );
}
