"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Play, RefreshCw, Search, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import { EmptyState, ErrorState, PageHeader, Panel, Skeleton } from "@/components/layout/primitives";
import { useConfirm } from "@/components/providers/confirm-provider";
import { useFeedback } from "@/components/providers/feedback-provider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch-tabs";
import { useKnowledge, useLinkState } from "@/lib/api/queries";
import type { Knowledge } from "@/lib/api/types";
import { cap } from "@/lib/format";

export function filterKnowledge(k: Knowledge, q: string) {
  const words = q.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const hit = (text: string) => words.every((w) => text.toLowerCase().includes(w));
  return {
    preferences: k.preferences.filter((p) => hit(p.text)),
    facts: k.facts.filter((f) => hit(`${f.text} ${f.category}`)),
    summaries: k.summaries.filter((s) => hit(s.summary)),
  };
}

export function MemoryView() {
  const { data, error, isPending, refetch, isFetching } = useKnowledge();
  const qc = useQueryClient();
  const confirm = useConfirm();
  const { act, toast } = useFeedback();
  const link = useLinkState();
  const [query, setQuery] = useState("");
  const shown = useMemo(() => (data ? filterKnowledge(data, query) : null), [data, query]);
  const canAct = link === "online";

  if (isPending) return (<><PageHeader title="Memory" /><Skeleton className="h-48" /></>);
  if (error || !data || !shown) return (<><PageHeader title="Memory" /><Panel><ErrorState title="Memory is unavailable" description={error?.message} onRetry={() => refetch()} /></Panel></>);

  const reload = () => qc.invalidateQueries({ queryKey: ["knowledge"] });

  async function forgetPreference(key: string, text: string) {
    if (!(await confirm({ title: "Forget this preference?", description: cap(text), confirmLabel: "Forget", destructive: true }))) return;
    await act({ do: "forget_preference", key });
    await reload();
  }
  async function forgetFact(id: number, text: string) {
    if (!(await confirm({ title: "Forget this?", description: text, confirmLabel: "Forget", destructive: true }))) return;
    await act({ do: "forget_fact", id });
    await reload();
  }

  const searching = query.trim().length > 0;

  return (
    <>
      <PageHeader
        title="Memory"
        description="What ZendAgent has learned about you. Forget anything you don't want it to keep."
        actions={
          <Button variant="outline" size="sm" disabled={isFetching} onClick={async () => { const r = await refetch(); toast(r.error ? "Couldn't refresh memory." : "Memory refreshed.", r.error ? "err" : "ok"); }}>
            <RefreshCw className={isFetching ? "animate-spin" : ""} /> Refresh
          </Button>
        }
      />
      <div className="relative mb-4">
        <Search className="pointer-events-none absolute left-2.5 top-2.5 size-4 text-muted-foreground" aria-hidden />
        <Input className="pl-8" aria-label="Search memory" placeholder="Search preferences, facts and past conversations" value={query} onChange={(e) => setQuery(e.target.value)} />
      </div>
      {!canAct && <p role="status" className="mb-4 rounded-lg border border-warning/40 bg-warning/10 px-3 py-2 text-sm text-warning">ZendAgent isn&apos;t running, so changes are disabled.</p>}

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="space-y-4">
          {!searching && data.profile.length > 0 && (
            <Panel title="About you">
              <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
                {data.profile.map((p) => (<div key={p.key} className="contents"><dt className="text-muted-foreground">{p.label}</dt><dd>{p.value}</dd></div>))}
              </dl>
            </Panel>
          )}
          <Panel title={`Preferences${shown.preferences.length ? ` (${shown.preferences.length})` : ""}`}>
            {shown.preferences.length === 0 ? (
              <EmptyState title={searching ? "No matches" : "Nothing learned yet"} description={searching ? undefined : "ZendAgent picks up preferences as you use it."} />
            ) : (
              <ul className="divide-y">
                {shown.preferences.map((p) => (
                  <li key={p.key} className="flex flex-wrap items-start gap-3 py-3 first:pt-0 last:pb-0">
                    <div className="min-w-0 flex-1">
                      <p className="text-sm">{cap(p.text)}</p>
                      <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                        <Badge tone={p.applied ? "success" : "warning"}>{p.applied ? "Learned" : "Still learning"}</Badge>
                        <span>{Math.round(p.confidence * 100)}% sure</span>
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      {p.applied && (
                        <label className="flex items-center gap-1.5 text-xs text-muted-foreground" title="Apply this automatically">
                          Auto
                          <Switch aria-label={`Apply automatically: ${p.text}`} checked={p.auto} disabled={!canAct} onCheckedChange={(on) => act({ do: "auto_preference", key: p.key, on }, { quiet: true }).then(reload)} />
                        </label>
                      )}
                      {p.kind === "routine" && (
                        <Button size="sm" variant="outline" disabled={!canAct} onClick={() => act({ do: "routine", trigger: p.key.split(":").slice(1).join(":") })}><Play /> Run</Button>
                      )}
                      <Button size="icon" variant="ghost" aria-label={`Forget preference: ${p.text}`} disabled={!canAct} onClick={() => forgetPreference(p.key, p.text)}><Trash2 /></Button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </div>

        <div className="space-y-4">
          <Panel title={`Remembered facts${shown.facts.length ? ` (${shown.facts.length})` : ""}`}>
            {shown.facts.length === 0 ? (
              <EmptyState title={searching ? "No matches" : "Nothing remembered yet"} description={searching ? undefined : "ZendAgent remembers useful things you mention."} />
            ) : (
              <ul className="max-h-[60vh] divide-y overflow-y-auto pr-1">
                {shown.facts.slice(0, 200).map((f) => (
                  <li key={f.id} className="flex items-start gap-3 py-3 first:pt-0 last:pb-0">
                    <div className="min-w-0 flex-1">
                      <p className="text-sm">{f.text}</p>
                      <div className="mt-1 flex items-center gap-2 text-xs text-muted-foreground">
                        <span>Saved {f.saved}</span>
                        {f.category && f.category !== "fact" && <Badge>{cap(f.category)}</Badge>}
                      </div>
                    </div>
                    <Button size="icon" variant="ghost" aria-label={`Forget: ${f.text}`} disabled={!canAct} onClick={() => forgetFact(f.id, f.text)}><Trash2 /></Button>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
          <Panel title="Past conversations">
            {shown.summaries.length === 0 ? (
              <EmptyState title={searching ? "No matches" : "No past conversations"} description={searching ? undefined : "Short notes about your talks show up here."} />
            ) : (
              <ul className="space-y-3">
                {shown.summaries.map((s, i) => (
                  <li key={i} className="text-sm"><div className="text-xs text-muted-foreground">{s.date}</div>{s.summary}</li>
                ))}
              </ul>
            )}
          </Panel>
        </div>
      </div>
    </>
  );
}
