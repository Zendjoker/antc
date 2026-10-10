"use client";

import { ExternalLink, Menu, Moon, Search, ShieldCheck, Sun, X } from "lucide-react";
import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, type ReactNode } from "react";
import { MiniOrb } from "@/components/brand/water-orb";
import { useFeedback } from "@/components/providers/feedback-provider";
import { useTheme } from "@/components/providers/theme-provider";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch-tabs";
import { useLinkState, useLive } from "@/lib/api/queries";
import { cn } from "@/lib/utils";
import { usd } from "@/lib/format";
import { CommandPalette } from "./command-palette";
import { CLASSIC_URL, NAV } from "./nav";
import { orbState } from "./orb-state";
import { StatusDot } from "./status-dot";
import { StopAllButton } from "./stop-all";

function SidebarNav({ onNavigate }: { onNavigate?: () => void }) {
  const path = usePathname();
  return (
    <nav aria-label="Main" className="flex flex-col">
      {(["Workspace", "Manage"] as const).map((group) => (
        <div key={group}>
          <div className="px-3 pb-2 pt-4 text-[10.5px] font-bold uppercase tracking-[0.12em] text-muted-foreground/80">{group}</div>
          <div className="flex flex-col gap-0.5">
            {NAV.filter((n) => n.group === group).map(({ href, label, icon: Icon }) => {
              const active = path === href || path.startsWith(href + "/");
              return (
                <Link
                  key={href}
                  href={href}
                  onClick={onNavigate}
                  aria-current={active ? "page" : undefined}
                  className={cn(
                    "flex h-[42px] items-center gap-3 rounded-xl px-3 text-sm font-medium transition-colors",
                    active ? "bg-primary font-semibold text-primary-foreground shadow-[0_6px_16px_rgba(8,102,245,0.24)]" : "text-sidebar-foreground hover:bg-accent hover:text-foreground",
                  )}
                >
                  <Icon className={cn("size-[18px]", active ? "text-white" : "text-muted-foreground")} aria-hidden />
                  {label}
                </Link>
              );
            })}
          </div>
        </div>
      ))}
    </nav>
  );
}

function Brand() {
  return (
    <div className="flex items-center gap-3 px-2 pb-4">
      <Image src="/brand/zend-logo.png" alt="" width={38} height={38} priority />
      <div>
        <div className="text-[19px] font-extrabold leading-none tracking-[0.14em]">ZEND</div>
        <div className="mt-1 text-xs text-muted-foreground">{"Your assistant"}</div>
      </div>
    </div>
  );
}

function PresenceCard() {
  const { data } = useLive();
  const link = useLinkState();
  const { act } = useFeedback();
  const online = link === "online";
  const quiet = data?.state?.name === "quiet";
  const spend = data?.spend;
  const pct = spend && spend.limit ? Math.min(100, (spend.today / spend.limit) * 100) : 0;
  return (
    <div className="mt-auto space-y-2 border-t pt-3.5">
      <div className="flex items-center gap-3 rounded-[14px] border bg-background/60 p-3">
        <MiniOrb state={orbState(link, data?.state?.name)} />
        <div className="min-w-0">
          <div className="truncate text-[13.5px] font-semibold">{online ? (data?.state?.label ?? "Ready") : link === "offline" ? "Offline" : link === "connecting" ? "Connecting" : "Disconnected"}</div>
          <div className="truncate text-xs text-muted-foreground">{online ? "Ready when you are" : "Start ZEND to talk"}</div>
        </div>
      </div>
      <label className="flex h-[38px] items-center justify-between rounded-[10px] px-2.5 text-[13.5px] text-sidebar-foreground">
        Quiet mode
        <Switch aria-label="Quiet mode" checked={quiet} disabled={!online} onCheckedChange={(on) => act({ do: "quiet", on })} />
      </label>
      <div className="px-2.5 pb-0.5">
        <div className="flex justify-between text-xs text-muted-foreground"><span>Spend today</span><span className="font-medium text-foreground">{spend ? usd(spend.today) : "—"}</span></div>
        <div className="mt-1.5 h-[5px] overflow-hidden rounded-full bg-muted" role="progressbar" aria-label="Spend today" aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100}>
          <div className={cn("h-full", pct >= 100 ? "bg-destructive" : "bg-primary")} style={{ width: `${pct}%` }} />
        </div>
      </div>
      <a href={CLASSIC_URL} className="flex items-center gap-2 rounded-[10px] px-2.5 py-2 text-xs text-muted-foreground hover:bg-accent hover:text-foreground">
        <ExternalLink className="size-3.5" aria-hidden /> Original dashboard
      </a>
    </div>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const [palette, setPalette] = useState(false);
  const { busy } = useFeedback();
  const { setTheme } = useTheme();
  const path = usePathname();
  const current = NAV.find((n) => path === n.href || path.startsWith(n.href + "/"));

  const SearchBox = (
    <button
      onClick={() => setPalette(true)}
      aria-label="Open command palette"
      className="mb-1 flex h-10 w-full items-center gap-2 rounded-xl border border-transparent bg-muted px-3 text-left text-[13px] text-muted-foreground transition-colors hover:border-input hover:bg-card"
    >
      <Search className="size-4" aria-hidden /> <span className="flex-1">Search or ask</span>
      <kbd className="rounded border bg-card px-1.5 text-[10px]">Ctrl K</kbd>
    </button>
  );

  return (
    <div className="min-h-dvh lg:grid lg:grid-cols-[17rem_1fr]">
      <aside className="hidden lg:sticky lg:top-3 lg:m-3 lg:mr-0 lg:flex lg:h-[calc(100dvh-1.5rem)] lg:flex-col lg:rounded-[22px] lg:border lg:bg-sidebar lg:px-3.5 lg:pb-3.5 lg:pt-5 lg:shadow-[0_1px_2px_rgba(16,24,40,.04),0_10px_30px_rgba(16,40,90,.06)]">
        <Brand />
        {SearchBox}
        <SidebarNav />
        <PresenceCard />
      </aside>

      {open && (
        <div className="fixed inset-0 z-40 lg:hidden" role="dialog" aria-modal="true" aria-label="Navigation">
          <button className="absolute inset-0 bg-black/40" aria-label="Close navigation" onClick={() => setOpen(false)} />
          <div className="absolute inset-y-0 left-0 flex w-72 flex-col bg-sidebar px-3.5 pb-3.5 pt-5 shadow-xl">
            <Brand />
            {SearchBox}
            <SidebarNav onNavigate={() => setOpen(false)} />
            <PresenceCard />
          </div>
        </div>
      )}

      <div className="flex min-w-0 flex-col">
        <header className="sticky top-0 z-30 flex min-h-16 items-center gap-3 bg-background/85 px-4 py-3 backdrop-blur sm:px-8">
          <Button variant="ghost" size="icon" className="lg:hidden" aria-label={open ? "Close navigation" : "Open navigation"} onClick={() => setOpen((v) => !v)}>
            {open ? <X /> : <Menu />}
          </Button>
          <div className="flex items-center gap-2 text-[13px] text-muted-foreground">
            <StatusDot className="[&>span:last-child]:hidden sm:[&>span:last-child]:inline" />
            {current && <><span aria-hidden>/</span><b className="font-semibold text-foreground">{current.label}</b></>}
          </div>
          {busy && <span role="status" className="text-xs text-muted-foreground">Sending request…</span>}
          <div className="ml-auto flex items-center gap-2">
            <span className="hidden h-[34px] items-center gap-1.5 rounded-full border bg-card px-3 text-[12.5px] text-muted-foreground md:inline-flex"><ShieldCheck className="size-3.5" aria-hidden /> Runs on your PC</span>
            <Button variant="ghost" size="icon" className="sm:hidden" aria-label="Open command palette" onClick={() => setPalette(true)}><Search /></Button>
            <StopAllButton />
            <Button variant="outline" size="icon" className="rounded-full bg-card" aria-label="Toggle theme" onClick={() => setTheme(document.documentElement.classList.contains("dark") ? "light" : "dark")}>
              <Sun className="hidden dark:block" /><Moon className="dark:hidden" />
            </Button>
          </div>
        </header>
        <main className="mx-auto w-full max-w-[1200px] flex-1 px-4 pb-10 pt-2 sm:px-8">{children}</main>
      </div>
      <CommandPalette open={palette} onOpenChange={setPalette} />
    </div>
  );
}
