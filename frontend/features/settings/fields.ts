import type { EnvField, EnvSection } from "@/lib/api/types";

const NAMES: Record<string, string> = {
  MIC_DEVICE: "Microphone", SPEAKER_DEVICE: "Speaker", LLM_PROVIDER: "AI provider", LLM_DEFAULT: "Default AI", LLM_SMART: "Advanced AI",
  STT_PROVIDER: "Speech recognition", TTS_PROVIDER: "Voice provider", DAILY_BUDGET_USD: "Daily spending limit ($)",
  ANTHROPIC_API_KEY: "Anthropic API key", OPENAI_API_KEY: "OpenAI API key", ELEVENLABS_API_KEY: "ElevenLabs API key",
  DEEPGRAM_API_KEY: "Deepgram API key", ELEVENLABS_VOICE_ID: "ElevenLabs voice", PIPER_VOICE: "Local voice",
  HA_URL: "Home Assistant address", HA_TOKEN: "Home Assistant access token", SPEAKER_VERIFY: "Recognize your voice",
  BARGE_IN: "Allow interruptions", WEATHER_LOCATION: "Weather location", WAKE_WORD: "Wake word",
  WAKE_THRESHOLD: "Wake word sensitivity (threshold)",
};

export function fieldLabel(key: string) {
  if (NAMES[key]) return NAMES[key];
  const s = key.toLowerCase().replace(/_/g, " ");
  return s[0].toUpperCase() + s.slice(1);
}

export interface Option { value: string; label: string }

export function fieldOptions(f: EnvField): Option[] | null {
  if (!f.options) return null;
  return (f.options as (string | Option)[]).map((o) => (typeof o === "string" ? { value: o, label: o } : o));
}

export function matchesQuery(f: EnvField, q: string) {
  if (!q) return true;
  const hay = `${f.key} ${fieldLabel(f.key)} ${f.comment}`.toLowerCase();
  return q.toLowerCase().split(/\s+/).every((w) => hay.includes(w));
}

/** Sections with at least one field matching the query. */
export function filterSections(sections: EnvSection[], q: string) {
  return sections
    .map((s) => ({ ...s, fields: s.fields.filter((f) => matchesQuery(f, q.trim())) }))
    .filter((s) => s.fields.length > 0);
}

/** Only fields whose value differs from the loaded baseline are sent. Blank secrets are never sent. */
export function dirtyChanges(sections: EnvSection[], edits: Record<string, string>) {
  const base = new Map(sections.flatMap((s) => s.fields).map((f) => [f.key, f] as const));
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(edits)) {
    const f = base.get(key);
    if (!f) continue;
    if (f.secret ? value === "" : value === f.value) continue;
    out[key] = value;
  }
  return out;
}
