"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useMemo, useRef, useState } from "react";
import { useConfirm } from "@/components/providers/confirm-provider";
import { useFeedback } from "@/components/providers/feedback-provider";
import { sendCommand } from "@/lib/api/actions";
import { useLive } from "@/lib/api/queries";
import type { ConversationTurn } from "@/lib/api/types";

const lastAssistant = (c: ConversationTurn[] = []) => [...c].reverse().find((m) => m.role === "assistant") ?? null;

/**
 * Conversation state machine. Mirrors the original dashboard: a draft is never lost on failure, an uncertain
 * outcome requires an explicit re-send confirmation, and the reply is shown until the live feed catches up.
 */
export function useChat() {
  const { data: live } = useLive();
  const qc = useQueryClient();
  const confirm = useConfirm();
  const { toast } = useFeedback();
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [reply, setReply] = useState<{ text: string; baseline: string } | null>(null);
  const uncertain = useRef("");
  const busy = useRef(false);

  const online = !!live?.online;
  const conversation = useMemo(() => live?.conversation ?? [], [live?.conversation]);

  /** Resolves true when the draft was sent and can be cleared. */
  const send = useCallback(async (raw: string): Promise<boolean> => {
    const text = raw.trim();
    if (!text) return false;
    if (busy.current) { toast("A request is already pending. Your draft is retained.", "pending"); return false; }
    if (!online) { toast("ZendAgent is unavailable. Your draft is retained; send it after reconnecting.", "err"); return false; }
    if (uncertain.current === text) {
      const again = await confirm({ title: "Send again?", description: "The previous request may already have run. Send it again anyway?", confirmLabel: "Send again" });
      if (!again) return false;
    }
    const baseline = JSON.stringify(lastAssistant(conversation));
    busy.current = true;
    setPending(text);
    setError("");
    const r = await sendCommand(text);
    let sent = false;
    if (r.outcome === "completed") {
      uncertain.current = "";
      setReply({ text: String(r.reply ?? ""), baseline });
      sent = true;
    } else {
      if (r.outcome === "unknown" || r.outcome === "accepted") uncertain.current = text;
      const msg = r.outcome === "accepted"
        ? "Request accepted, completion unconfirmed. Your draft is retained. Check the conversation before resending."
        : `${r.message} Your draft is retained.`;
      setError(msg);
      toast(msg, "err");
    }
    busy.current = false;
    setPending(null);
    await qc.invalidateQueries({ queryKey: ["live"] });
    return sent;
  }, [online, conversation, confirm, toast, qc]);

  // The reply is shown until the live conversation contains a newer assistant message.
  const showReply = reply && JSON.stringify(lastAssistant(conversation)) === reply.baseline ? reply.text : null;

  return { conversation, online, pending, error, reply: showReply, send };
}
