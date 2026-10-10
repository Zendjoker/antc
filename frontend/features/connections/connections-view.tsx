"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Loader2, Plug, ShieldAlert } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { EmptyState, ErrorState, PageHeader, Panel, Skeleton } from "@/components/layout/primitives";
import { useConfirm } from "@/components/providers/confirm-provider";
import { useFeedback } from "@/components/providers/feedback-provider";
import { Badge, stateTone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch-tabs";
import { ApiError, apiGet, apiPost } from "@/lib/api/client";
import { useConnections } from "@/lib/api/queries";
import type { ConnectionAccount, ConnectionProvider, Flow } from "@/lib/api/types";
import { timeAgo } from "@/lib/format";

const FLOW_POLL_MS = 1500;
const FLOW_MAX_MS = 120000;
const DEFAULT_LEVELS = { gmail: ["read"], calendar: ["read"] };

/** Sign-in runs in the system browser. The dashboard only polls the flow; it never sees a password or token. */
function useFlow(onSettled: () => void) {
  const [flow, setFlow] = useState<{ provider: string; message: string } | null>(null);
  const alive = useRef(true);
  const { toast } = useFeedback();
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  const follow = useCallback(async (provider: string, id: string) => {
    setFlow({ provider, message: "Waiting for you to sign in in your browser…" });
    const started = Date.now();
    try {
      while (alive.current && Date.now() - started < FLOW_MAX_MS) {
        await new Promise((r) => setTimeout(r, FLOW_POLL_MS));
        if (!alive.current) return;
        const f = await apiGet<Flow>(`/api/connections/flows/${encodeURIComponent(id)}`);
        if (f.state === "pending") { setFlow({ provider, message: f.message }); continue; }
        toast(f.message || (f.state === "done" ? "Connected." : "Sign-in didn't finish."), f.state === "done" ? "ok" : "err");
        return;
      }
      if (alive.current) toast("Still waiting for sign-in. Check the connection status again.", "pending");
    } catch (e) {
      if (alive.current) toast((e as Error).message, "err");
    } finally {
      if (alive.current) setFlow(null);
      onSettled();
    }
  }, [toast, onSettled]);

  return { flow, follow };
}

export function ConnectionsView() {
  const { data, error, isPending, refetch } = useConnections();
  const qc = useQueryClient();
  const confirm = useConfirm();
  const { toast } = useFeedback();
  const refresh = useCallback(() => qc.invalidateQueries({ queryKey: ["connections"] }), [qc]);
  const { flow, follow } = useFlow(refresh);
  const [busy, setBusy] = useState<string | null>(null);

  async function run(label: string, fn: () => Promise<void>) {
    setBusy(label);
    try { await fn(); } catch (e) { toast(e instanceof ApiError ? e.message : "Something went wrong.", "err"); } finally { setBusy(null); await refresh(); }
  }

  const startFlow = (p: ConnectionProvider, path: string, body: unknown) =>
    run(`${p.id}:flow`, async () => {
      const r = await apiPost<{ flow?: string }>(path, body);
      if (!r.flow) throw new ApiError("invalid", "The server didn't start the sign-in.");
      void follow(p.id, r.flow);
    });

  if (isPending) return (<><PageHeader title="Connections" /><Skeleton className="h-40" /></>);
  if (error) return (<><PageHeader title="Connections" /><Panel><ErrorState title="Can't load connections" description={error.message} onRetry={() => refetch()} /></Panel></>);
  const providers = data.providers ?? [];
  const locked = !!flow || !!busy;

  return (
    <>
      <PageHeader title="Connections" description="Accounts ZendAgent can use. Sign-in happens in your browser; passwords and tokens never pass through this page." />
      {flow && (
        <div role="status" className="mb-4 flex items-center gap-2 rounded-lg border border-primary/30 bg-primary/10 px-3 py-2 text-sm">
          <Loader2 className="size-4 animate-spin" aria-hidden /> {flow.message}
        </div>
      )}
      {providers.length === 0 && <Panel><EmptyState title="No integrations available" /></Panel>}
      <div className="space-y-4">
        {providers.map((p) => (
          <Panel key={p.id}>
            <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
              <div className="flex items-center gap-2">
                <Plug className="size-4 text-muted-foreground" aria-hidden />
                <h2 className="text-base font-semibold">{p.name}</h2>
                {!p.configured && <Badge tone="warning">Not configured</Badge>}
              </div>
              {p.configured && (
                <Button size="sm" disabled={locked} onClick={() => startFlow(p, `/api/connections/${p.id}/connect`, { levels: DEFAULT_LEVELS })}>
                  {p.accounts.length ? "Add another account" : "Connect"}
                </Button>
              )}
            </div>
            {!p.configured && <p className="text-sm text-muted-foreground">{p.setup}</p>}
            {p.configured && p.accounts.length === 0 && <EmptyState title="No account connected" description="Connect an account to let ZendAgent use this service." />}
            <div className="divide-y">
              {p.accounts.map((a) => (
                <AccountRow
                  key={a.account}
                  provider={p}
                  account={a}
                  locked={locked}
                  onReconnect={() => startFlow(p, `/api/connections/${p.id}/reconnect`, { account: a.account })}
                  onActive={() => run(`${p.id}:active`, async () => { await apiPost(`/api/connections/${p.id}/active`, { account: a.account }); toast("Active account changed."); })}
                  onAccess={(service, level, on) => run(`${p.id}:access`, async () => {
                    const r = await apiPost<{ flow?: string }>(`/api/connections/${p.id}/access`, { account: a.account, service, level, on });
                    if (r.flow) void follow(p.id, r.flow); else toast(on ? "Access enabled." : "Access turned off.");
                  })}
                  onDisconnect={async () => {
                    const ok = await confirm({
                      title: `Disconnect ${a.account}?`,
                      description: "ZendAgent loses access and its saved key is deleted.",
                      confirmLabel: "Disconnect",
                      destructive: true,
                    });
                    if (!ok) return;
                    await run(`${p.id}:disconnect`, async () => {
                      const r = await apiPost<{ ok?: boolean; revoked?: unknown }>(`/api/connections/${p.id}/disconnect`, { account: a.account });
                      if (typeof r.revoked !== "boolean") throw new ApiError("invalid", "Couldn't confirm the disconnect. Check the status before retrying.");
                      toast(r.revoked ? "Disconnected and access revoked." : "Disconnected here, but access may still be active at the provider. Remove it in the provider's account settings.", r.revoked ? "ok" : "pending");
                    });
                  }}
                />
              ))}
            </div>
          </Panel>
        ))}
      </div>
    </>
  );
}

function AccountRow({ account: a, locked, onReconnect, onActive, onAccess, onDisconnect }: {
  provider: ConnectionProvider; account: ConnectionAccount; locked: boolean;
  onReconnect: () => void; onActive: () => void; onDisconnect: () => void;
  onAccess: (service: string, level: string, on: boolean) => void;
}) {
  const needsSignIn = a.status !== "connected";
  return (
    <div className="py-4 first:pt-0 last:pb-0">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{a.account}</span>
        <Badge tone={stateTone(a.status)}>{a.status}</Badge>
        {a.active && <Badge tone="info">Active</Badge>}
        <div className="ml-auto flex gap-2">
          {!a.active && <Button size="sm" variant="outline" disabled={locked} onClick={onActive}>Make active</Button>}
          {needsSignIn && <Button size="sm" variant="outline" disabled={locked} onClick={onReconnect}>Sign in again</Button>}
          <Button size="sm" variant="outline" disabled={locked} onClick={onDisconnect}>Disconnect</Button>
        </div>
      </div>
      {a.last_success ? <p className="mt-1 text-xs text-muted-foreground">Last used {timeAgo(a.last_success)}</p> : null}
      {a.last_error && <p className="mt-1 flex items-center gap-1 text-xs text-destructive"><ShieldAlert className="size-3.5" aria-hidden /> {a.last_error}</p>}
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        {a.services.map((s) => (
          <div key={s.id} className="rounded-lg border p-3">
            <div className="mb-2 text-sm font-medium">{s.name}</div>
            <ul className="space-y-2">
              {s.levels.map((l) => (
                <li key={l.id} className="flex items-start justify-between gap-3 text-sm">
                  <span className="flex-1">
                    {l.label}
                    {l.consequential && <Badge tone="warning" className="ml-2">Needs your OK</Badge>}
                    {!l.granted && <span className="block text-xs text-muted-foreground">Not granted yet. Turning this on asks you to sign in.</span>}
                  </span>
                  <Switch
                    aria-label={`${s.name}: ${l.label}`}
                    checked={l.granted && l.enabled}
                    disabled={locked}
                    onCheckedChange={(on) => onAccess(s.id, l.id, on)}
                  />
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </div>
  );
}
