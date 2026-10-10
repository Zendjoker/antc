"use client";

import { Search } from "lucide-react";
import { Dialog } from "radix-ui";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { useTheme } from "@/components/providers/theme-provider";
import { useFeedback } from "@/components/providers/feedback-provider";
import { useLinkState, useLive } from "@/lib/api/queries";
import { cn } from "@/lib/utils";
import { NAV } from "./nav";
import { useEmergencyStop } from "./stop-all";

interface Command { id: string; group: string; label: string; keys?: string; disabled?: boolean; run: () => void }

export function matchCommands(commands: Command[], q: string) {
  const words = q.trim().toLowerCase().split(/\s+/).filter(Boolean);
  return commands.filter((c) => !c.disabled && words.every((w) => `${c.label} ${c.keys ?? ""}`.toLowerCase().includes(w)));
}

export function CommandPalette({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const router = useRouter();
  const { act } = useFeedback();
  const { theme, setTheme } = useTheme();
  const stop = useEmergencyStop();
  const online = useLinkState() === "online";
  const { data: live } = useLive();
  const [query, setQuery] = useState("");
  const [sel, setSel] = useState(0);

  const quiet = live?.state?.name === "quiet";
  const undo = typeof live?.undo === "string" ? live.undo : null;

  const commands = useMemo<Command[]>(() => [
    ...NAV.map((n) => ({ id: n.href, group: "Go to", label: n.label, run: () => router.push(n.href) })),
    { id: "quiet", group: "Actions", label: quiet ? "Turn off quiet mode" : "Turn on quiet mode", keys: "silent sleep dnd", disabled: !online, run: () => void act({ do: "quiet", on: !quiet }) },
    { id: "undo", group: "Actions", label: undo ? `Undo: ${undo}` : "Undo last change", keys: "revert back", disabled: !online || !undo, run: () => void act({ do: "undo" }) },
    { id: "timer5", group: "Actions", label: "Start a 5 minute timer", keys: "timer", disabled: !online, run: () => void act({ do: "timer", seconds: 300, label: "" }) },
    { id: "timer25", group: "Actions", label: "Start a 25 minute focus timer", keys: "timer focus pomodoro", disabled: !online, run: () => void act({ do: "timer", seconds: 1500, label: "Focus" }) },
    { id: "stop", group: "Actions", label: "Stop everything", keys: "emergency halt", disabled: !online, run: () => void stop() },
    { id: "theme", group: "Appearance", label: theme === "dark" ? "Switch to light theme" : "Switch to dark theme", keys: "theme dark light", run: () => setTheme(theme === "dark" ? "light" : "dark") },
  ], [router, act, quiet, undo, online, stop, theme, setTheme]);

  const shown = useMemo(() => matchCommands(commands, query), [commands, query]);
  const index = Math.min(sel, Math.max(0, shown.length - 1));

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); onOpenChange(!open); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onOpenChange]);

  const choose = (c?: Command) => { if (!c) return; onOpenChange(false); c.run(); };

  return (
    <Dialog.Root open={open} onOpenChange={(o) => { onOpenChange(o); if (!o) { setQuery(""); setSel(0); } }}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/50" />
        <Dialog.Content className="fixed left-1/2 top-[15%] z-50 w-[calc(100vw-2rem)] max-w-lg -translate-x-1/2 overflow-hidden rounded-xl border bg-popover text-popover-foreground shadow-xl">
          <Dialog.Title className="sr-only">Command palette</Dialog.Title>
          <Dialog.Description className="sr-only">Search pages and actions. Use arrow keys and Enter.</Dialog.Description>
          <div className="flex items-center gap-2 border-b px-3">
            <Search className="size-4 text-muted-foreground" aria-hidden />
            <input
              autoFocus
              role="combobox"
              aria-expanded
              aria-controls="palette-list"
              aria-label="Search commands"
              placeholder="Type a command or page…"
              className="h-11 flex-1 bg-transparent text-sm outline-none"
              value={query}
              onChange={(e) => { setQuery(e.target.value); setSel(0); }}
              onKeyDown={(e) => {
                if (e.key === "ArrowDown") { e.preventDefault(); setSel(Math.min(shown.length - 1, index + 1)); }
                else if (e.key === "ArrowUp") { e.preventDefault(); setSel(Math.max(0, index - 1)); }
                else if (e.key === "Enter") { e.preventDefault(); choose(shown[index]); }
              }}
            />
          </div>
          <ul id="palette-list" role="listbox" className="max-h-80 overflow-y-auto p-1">
            {shown.length === 0 && <li className="px-3 py-6 text-center text-sm text-muted-foreground">No matches</li>}
            {shown.map((c, i) => (
              <li
                key={c.id}
                role="option"
                aria-selected={i === index}
                onMouseMove={() => setSel(i)}
                onClick={() => choose(c)}
                className={cn("flex cursor-pointer items-center justify-between rounded-md px-3 py-2 text-sm", i === index && "bg-accent text-accent-foreground")}
              >
                {c.label}<span className="text-xs text-muted-foreground">{c.group}</span>
              </li>
            ))}
          </ul>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
