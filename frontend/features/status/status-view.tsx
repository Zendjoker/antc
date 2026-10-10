"use client";

import { Phone } from "lucide-react";
import { ErrorState, PageHeader, Panel, Skeleton, Stat } from "@/components/layout/primitives";
import { useConfirm } from "@/components/providers/confirm-provider";
import { useFeedback } from "@/components/providers/feedback-provider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useLinkState, useLive, useStatus } from "@/lib/api/queries";
import { cap, usd } from "@/lib/format";

function duration(s: number) {
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
  return `${Math.floor(s / 86400)}d ${Math.floor((s % 86400) / 3600)}h`;
}

export function StatusView() {
  const status = useStatus();
  const live = useLive();
  const link = useLinkState();
  const confirm = useConfirm();
  const { act } = useFeedback();

  if (status.isPending) return (<><PageHeader title="Status" /><div className="grid gap-4 md:grid-cols-2">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-32" />)}</div></>);
  if (status.error || !status.data) return (<><PageHeader title="Status" /><Panel><ErrorState title="Can't load status" description={status.error?.message} onRetry={() => status.refetch()} /></Panel></>);

  const s = status.data;
  const l = live.data;
  const spent = Object.values(s.spend.usd).reduce((a, b) => a + b, 0);
  const pct = s.daily_budget ? Math.min(100, (spent / s.daily_budget) * 100) : 0;
  const timing = l?.timing ?? {};
  const phone = l?.phone;
  const LINK = { online: ["Running", "success"], offline: ["Agent offline", "warning"], disconnected: ["Server unreachable", "danger"], connecting: ["Connecting", "neutral"] } as const;
  const [linkLabel, linkTone] = LINK[link];

  async function callMe() {
    const ok = await confirm({ title: "Call your phone?", description: `ZendAgent will call ${phone?.number || "your number"} now. Twilio charges a few cents per call.`, confirmLabel: "Call me" });
    if (ok) await act({ do: "call_me" });
  }

  return (
    <>
      <PageHeader title="Status" description="How ZendAgent is configured and how it's doing." actions={<Badge tone={linkTone}>{linkLabel}</Badge>} />
      <div className="grid gap-4 md:grid-cols-2">
        <Panel title="Runtime">
          {l?.health ? (
            <div className="grid grid-cols-2 gap-4">
              <Stat label="State" value={cap((l.state?.name ?? l.health.state).replace(/_/g, " "))} hint={l.state?.label} />
              <Stat label="Uptime" value={duration(l.health.uptime_s)} />
              <Stat label="Heartbeat" value={`${l.health.heartbeat_age_s.toFixed(1)}s ago`} />
              <Stat label="Process" value={`PID ${l.health.pid}`} />
            </div>
          ) : <p className="text-sm text-muted-foreground">The assistant isn&apos;t running, so there is no runtime information.</p>}
        </Panel>
        <Panel title="Brain & speech">
          <div className="grid grid-cols-2 gap-4">
            <Stat label="Model" value={s.model} hint={`Provider: ${s.llm_provider}`} />
            <Stat label="Speech recognition" value={s.stt_provider} hint={s.whisper_model} />
            <Stat label="Voice" value={s.tts_provider} />
            <Stat label="Wake word" value={s.wake_word} />
          </div>
        </Panel>
        <Panel title="Audio">
          <div className="grid grid-cols-2 gap-4">
            <Stat label="Microphone" value={s.mic_device} />
            <Stat label="Speaker" value={s.speaker_device} />
            <Stat label="Voice verification" value={s.speaker_verify ? (s.voiceprint_enrolled ? "On" : "On, not enrolled") : "Off"} />
          </div>
        </Panel>
        <Panel title="Spending today">
          <div className="flex items-baseline justify-between"><span className="text-lg font-semibold">{usd(spent)}</span><span className="text-sm text-muted-foreground">of {usd(s.daily_budget)} · {s.spend.calls} API calls</span></div>
          <div className="mt-2 h-2 overflow-hidden rounded-full bg-muted" role="progressbar" aria-label="Daily budget used" aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100}>
            <div className={pct >= 90 ? "h-full bg-destructive" : pct >= 70 ? "h-full bg-warning" : "h-full bg-primary"} style={{ width: `${pct}%` }} />
          </div>
          {Object.keys(s.spend.usd).length > 0 && (
            <ul className="mt-3 space-y-1 text-sm">
              {Object.entries(s.spend.usd).map(([k, v]) => <li key={k} className="flex justify-between"><span className="text-muted-foreground">{k}</span><span>{usd(v)}</span></li>)}
            </ul>
          )}
        </Panel>
        {Object.keys(timing).length > 0 && (
          <Panel title="Last response timing">
            <div className="grid grid-cols-2 gap-4">
              {Object.entries(timing).map(([k, v]) => <Stat key={k} label={k.replace(/_/g, " ")} value={`${v.toFixed(2)}s`} />)}
            </div>
          </Panel>
        )}
        <Panel title="Connections & phone">
          <div className="grid grid-cols-2 gap-4">
            <Stat label="Google" value={l?.connections?.google ?? "—"} />
            <Stat label="Phone" value={!l?.online ? "Offline" : phone?.in_call ? "On a call" : phone?.ready ? "Ready" : phone?.mode ? "Needs setup" : "Off"} />
            <Stat label="Driving" value={!l?.online ? "—" : phone?.driving ? "Yes" : "No"} />
          </div>
          <Button className="mt-4" size="sm" variant="outline" disabled={link !== "online" || !phone?.ready || !!phone?.in_call} onClick={callMe}><Phone /> Call me</Button>
        </Panel>
      </div>
    </>
  );
}
