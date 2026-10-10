"use client";

import { ChevronDown, Loader2, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { EmptyState, ErrorState, PageHeader, Panel, Skeleton } from "@/components/layout/primitives";
import { useFeedback } from "@/components/providers/feedback-provider";
import { useTheme, type ThemeChoice } from "@/components/providers/theme-provider";
import { Button } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch-tabs";
import { ApiError, apiPost } from "@/lib/api/client";
import { useEnv } from "@/lib/api/queries";
import type { EnvField, EnvSaveResult } from "@/lib/api/types";
import { useQueryClient } from "@tanstack/react-query";
import { cn } from "@/lib/utils";
import { dirtyChanges, fieldLabel, fieldOptions, filterSections } from "./fields";

interface Banner { kind: "ok" | "err"; text: string }

export function SettingsView() {
  const { data, error, isPending, refetch } = useEnv();
  const qc = useQueryClient();
  const { toast } = useFeedback();
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [changing, setChanging] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState("");
  const [closed, setClosed] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState(false);
  const [banner, setBanner] = useState<Banner | null>(null);

  const sections = useMemo(() => data?.sections ?? [], [data?.sections]);
  const changes = useMemo(() => dirtyChanges(sections, edits), [sections, edits]);
  const dirtyCount = Object.keys(changes).length;
  const shown = useMemo(() => filterSections(sections, query), [sections, query]);

  const setValue = (f: EnvField, value: string) => {
    setBanner(null);
    setEdits((e) => {
      const next = { ...e };
      if (!f.secret && value === f.value) delete next[f.key];
      else next[f.key] = value;
      return next;
    });
  };

  async function save() {
    setSaving(true);
    setBanner(null);
    try {
      const r = await apiPost<EnvSaveResult>("/api/env", changes, 10000);
      if (!r.ok) throw new ApiError("http", "The server didn't accept the settings.");
      const parts = [r.applied?.length && !r.restart_required ? "Audio devices switched" : "Settings saved"];
      if (r.restart_required) parts.push("Restart ZendAgent for the rest to take effect.");
      if (r.live_error) parts.push(r.live_error);
      const text = parts.filter(Boolean).join(". ");
      setBanner({ kind: "ok", text });
      toast(r.applied?.length && !r.restart_required ? "Audio devices switched" : "Settings saved");
      setEdits({});
      setChanging(new Set());
      await Promise.all([qc.invalidateQueries({ queryKey: ["env"] }), qc.invalidateQueries({ queryKey: ["status"] })]);
    } catch (e) {
      // A timeout or lost connection doesn't prove the save failed, so don't claim it did.
      const unsure = e instanceof ApiError && (e.kind === "timeout" || e.kind === "offline");
      const text = unsure ? "Couldn't confirm the save. Reload the settings to check the current values." : (e as Error).message;
      setBanner({ kind: "err", text });
      toast(text, "err");
    } finally {
      setSaving(false);
    }
  }

  if (isPending) return (<><PageHeader title="Settings" /><div className="space-y-3">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-20" />)}</div></>);
  if (error) return (<><PageHeader title="Settings" /><Panel><ErrorState title="Can't load settings" description={error.message} onRetry={() => refetch()} /></Panel></>);

  return (
    <>
      <PageHeader title="Settings" description="Models, voice, audio and integrations. Changes are written to the server's configuration." />
      <AppearancePanel />

      <div className="sticky top-14 z-20 -mx-1 mb-4 mt-4 flex flex-wrap items-center gap-3 rounded-xl border bg-card/95 p-3 backdrop-blur">
        <div className="relative min-w-48 flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-2.5 size-4 text-muted-foreground" aria-hidden />
          <Input className="pl-8" aria-label="Search settings" placeholder="Search settings" value={query} onChange={(e) => setQuery(e.target.value)} />
        </div>
        <span className="text-sm text-muted-foreground" aria-live="polite">{dirtyCount ? `${dirtyCount} unsaved change${dirtyCount > 1 ? "s" : ""}` : "No changes"}</span>
        <Button variant="outline" disabled={!dirtyCount || saving} onClick={() => { setEdits({}); setChanging(new Set()); }}>Discard</Button>
        <Button disabled={!dirtyCount || saving} onClick={save}>{saving && <Loader2 className="animate-spin" />} Save changes</Button>
      </div>
      {banner && (
        <div role="status" className={cn("mb-4 rounded-lg border px-3 py-2 text-sm", banner.kind === "ok" ? "border-success/40 bg-success/10 text-success" : "border-destructive/40 bg-destructive/10 text-destructive")}>
          {banner.text}
        </div>
      )}

      {shown.length === 0 && <Panel><EmptyState title="No settings match" description="Try a different search." /></Panel>}
      <div className="space-y-3">
        {shown.map((s) => {
          const open = query.trim() ? true : !closed.has(s.title);
          return (
            <section key={s.title} className="rounded-xl border bg-card">
              <button
                aria-expanded={open}
                className="flex w-full items-center justify-between px-4 py-3 text-left text-sm font-medium"
                onClick={() => setClosed((c) => { const n = new Set(c); if (n.has(s.title)) n.delete(s.title); else n.add(s.title); return n; })}
              >
                {s.title}
                <ChevronDown className={cn("size-4 text-muted-foreground transition-transform", open && "rotate-180")} aria-hidden />
              </button>
              {open && (
                <div className="divide-y border-t">
                  {s.fields.map((f) => (
                    <FieldRow
                      key={f.key}
                      field={f}
                      value={edits[f.key] ?? f.value}
                      changing={changing.has(f.key)}
                      onChange={(v) => setValue(f, v)}
                      onStartChange={() => { setChanging((c) => new Set(c).add(f.key)); setEdits((e) => ({ ...e, [f.key]: "" })); }}
                    />
                  ))}
                </div>
              )}
            </section>
          );
        })}
      </div>
    </>
  );
}

function FieldRow({ field: f, value, changing, onChange, onStartChange }: {
  field: EnvField; value: string; changing: boolean; onChange: (v: string) => void; onStartChange: () => void;
}) {
  const id = `setting-${f.key}`;
  const options = fieldOptions(f);
  let control;
  if (f.secret) {
    control = (
      <div className="flex gap-2">
        <Input id={id} type={changing ? "password" : "text"} readOnly={!changing} autoComplete="off" value={value} placeholder={changing ? "Paste the new value" : f.has_value ? "" : "Not set"} onChange={(e) => onChange(e.target.value)} />
        {!changing && <Button type="button" variant="outline" onClick={onStartChange}>{f.has_value ? "Change" : "Set"}</Button>}
      </div>
    );
  } else if (f.type === "bool") {
    control = <Switch id={id} checked={value === "1"} onCheckedChange={(on) => onChange(on ? "1" : "0")} aria-label={fieldLabel(f.key)} />;
  } else if (options) {
    const has = options.some((o) => o.value === value);
    control = (
      <Select id={id} value={value} onChange={(e) => onChange(e.target.value)}>
        {!has && <option value={value}>{value || "(default)"}</option>}
        {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </Select>
    );
  } else {
    control = <Input id={id} type={f.type === "number" ? "number" : "text"} step={f.type === "number" ? "any" : undefined} value={value} onChange={(e) => onChange(e.target.value)} />;
  }
  return (
    <div className="grid gap-2 px-4 py-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,18rem)] sm:items-center">
      <div>
        <label htmlFor={id} className="text-sm font-medium">{fieldLabel(f.key)}</label>
        {f.comment && <p className="mt-0.5 text-xs text-muted-foreground">{f.comment}</p>}
      </div>
      <div className={f.type === "bool" ? "sm:justify-self-end" : ""}>{control}</div>
    </div>
  );
}

function AppearancePanel() {
  const { theme, setTheme } = useTheme();
  const choices: { id: ThemeChoice; label: string }[] = [{ id: "light", label: "Light" }, { id: "dark", label: "Dark" }, { id: "system", label: "System" }];
  return (
    <Panel title="Appearance">
      <div role="radiogroup" aria-label="Theme" className="inline-flex rounded-lg border p-0.5">
        {choices.map((c) => (
          <button
            key={c.id}
            role="radio"
            aria-checked={theme === c.id}
            onClick={() => setTheme(c.id)}
            className={cn("rounded-md px-3 py-1.5 text-sm font-medium", theme === c.id ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground")}
          >
            {c.label}
          </button>
        ))}
      </div>
    </Panel>
  );
}
