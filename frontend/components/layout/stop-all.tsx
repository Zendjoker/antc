"use client";

import { OctagonX } from "lucide-react";
import { useCallback, useEffect } from "react";
import { useFeedback } from "@/components/providers/feedback-provider";
import { Button } from "@/components/ui/button";
import { useLinkState } from "@/lib/api/queries";

/** Same behavior as the original dashboard's Stop All: report what the server actually confirmed. */
export function useEmergencyStop() {
  const { act, toast } = useFeedback();
  return useCallback(async () => {
    const r = await act({ do: "emergency_stop" }, { quiet: true });
    if (r.ok) toast(r.outcome === "completed" ? r.message || "ZendAgent confirmed the stop request." : "Stop request accepted; completion is unconfirmed.", r.outcome === "completed" ? "ok" : "pending");
    else toast(r.message || "Stop request failed.", "err");
  }, [act, toast]);
}

export function StopAllButton() {
  const stop = useEmergencyStop();
  const online = useLinkState() === "online";
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey && e.altKey && e.key.toLowerCase() === "j") { e.preventDefault(); void stop(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [stop]);
  return (
    <Button variant="outline" size="sm" className="rounded-full border-destructive/40 bg-card text-destructive hover:bg-destructive/10 hover:text-destructive" disabled={!online} onClick={() => void stop()} title="Stop everything (Ctrl+Alt+J)">
      <OctagonX /> <span className="hidden sm:inline">Stop all</span>
    </Button>
  );
}
