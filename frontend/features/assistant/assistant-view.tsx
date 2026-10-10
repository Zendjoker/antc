"use client";

import { Loader2, SendHorizonal } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { PageHeader } from "@/components/layout/primitives";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { useChat } from "./use-chat";

// Same prompts the original dashboard suggests.
export const STARTERS = [
  "Brief me",
  "What's on my calendar today?",
  "Any important emails?",
  "What's the weather?",
  "What have you learned about me?",
];

function dayLabel(time: string) {
  const day = time.slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return "";
  const d = new Date(`${day}T12:00:00`);
  return d.toDateString() === new Date().toDateString() ? "Today" : d.toLocaleDateString([], { weekday: "long", month: "short", day: "numeric" });
}

export function AssistantView() {
  const { conversation, online, pending, error, reply, send } = useChat();
  const [draft, setDraft] = useState("");
  const scroller = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const stick = useRef(true);

  useEffect(() => {
    const s = scroller.current;
    if (s && stick.current) s.scrollTo({ top: s.scrollHeight });
  }, [conversation.length, pending, reply, error]);

  useEffect(() => {
    const t = input.current;
    if (!t) return;
    t.style.height = "auto";
    t.style.height = `${Math.min(180, t.scrollHeight)}px`;
  }, [draft]);

  async function submit(text = draft) {
    if (await send(text) && text === draft) setDraft("");
  }

  const empty = conversation.length === 0 && !pending && !reply;

  return (
    <div className="flex h-[calc(100dvh-8.5rem)] min-h-96 flex-col">
      <PageHeader title="Assistant" description="Talk to ZendAgent by text. Voice conversations appear here too." />
      <div
        ref={scroller}
        onScroll={(e) => { const s = e.currentTarget; stick.current = s.scrollHeight - s.scrollTop - s.clientHeight < 80; }}
        className="flex-1 space-y-4 overflow-y-auto rounded-xl border bg-card p-4"
        aria-label="Conversation"
        role="log"
      >
        {empty && (
          <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
            <p className="text-sm text-muted-foreground">{online ? "Start a conversation, or try one of these." : "ZendAgent isn't running. Start it to chat."}</p>
            <div className="flex flex-wrap justify-center gap-2">
              {STARTERS.map((s) => (
                <Button key={s} variant="outline" size="sm" disabled={!online} onClick={() => submit(s)}>{s}</Button>
              ))}
            </div>
          </div>
        )}
        {conversation.map((m, i) => {
          const day = dayLabel(m.time ?? "");
          const showDay = day && day !== dayLabel(conversation[i - 1]?.time ?? "");
          return (
            <div key={i} className="space-y-4">
              {showDay && <div className="text-center text-xs text-muted-foreground">{day}</div>}
              <Message role={m.role} text={m.text} time={m.time} />
            </div>
          );
        })}
        {pending && <Message role="user" text={pending} time="" pending />}
        {reply && <Message role="assistant" text={reply} time="" />}
        {pending && (
          <div className="flex items-center gap-2 text-sm text-muted-foreground" role="status"><Loader2 className="size-4 animate-spin" aria-hidden /> Thinking…</div>
        )}
        {error && <Message role="assistant" text={error} time="" error />}
      </div>

      <form
        className="mt-3 flex items-end gap-2"
        onSubmit={(e) => { e.preventDefault(); submit(); }}
      >
        <Textarea
          ref={input}
          rows={1}
          value={draft}
          aria-label="Message"
          placeholder={online ? "Message ZendAgent…" : "ZendAgent is offline"}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submit(); }
          }}
        />
        <Button type="submit" size="icon" aria-label="Send" disabled={!draft.trim() || !!pending}><SendHorizonal /></Button>
      </form>
    </div>
  );
}

function Message({ role, text, time, pending, error }: { role: string; text: string; time: string; pending?: boolean; error?: boolean }) {
  const mine = role === "user";
  return (
    <div className={cn("flex", mine ? "justify-end" : "justify-start")}>
      <div
        title={time || undefined}
        className={cn(
          "max-w-[85%] whitespace-pre-wrap rounded-2xl px-3.5 py-2 text-sm",
          mine ? "bg-primary text-primary-foreground" : "bg-muted text-foreground",
          pending && "opacity-70",
          error && "border border-destructive/50 bg-destructive/10 text-destructive",
        )}
      >
        {!mine && <div className="mb-0.5 text-xs font-medium text-muted-foreground">ZendAgent{time ? ` · ${time.slice(11, 16) || time}` : ""}</div>}
        {text}
      </div>
    </div>
  );
}
