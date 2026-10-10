// Shapes of the existing Python backend (UI/server.py proxying room_agent/control.py). Observed from a live /api/live.
export type Phase =
  | "offline" | "disconnected" | "connecting" | "starting" | "wake_word_only" | "quiet"
  | "listening" | "processing" | "speaking" | "call" | (string & {});

export interface ActivityItem { at: number; kind: string; text: string }
export interface ConversationTurn { role: string; text: string; time: string }
export interface TaskStep { [key: string]: unknown }
export interface TaskItem {
  id: string; goal: string; kind: string; state: string; cost_usd: number; seconds: number;
  updated: number; steps: TaskStep[]; notes: unknown[]; recovered: unknown[]; supervisor: unknown[];
}
export interface MissionSummary {
  id: string; kind: string; title?: string; state: string; current: string; error: string; summary: string; workspace?: string;
  budget_usd: number; spent_usd: number; reserved_usd?: number; uncertain_usd?: number; target?: number;
  approvals_pending: number; approvals_unknown: number;
  steps_done: number; steps_failed: number; steps_total: number;
  leads_found: number; leads_researched: number; leads_qualifying: number; demos: number; drafts: number; requests: number;
  tokens_in?: number; tokens_out?: number; errors: string[]; abandoned_runs: number;
  [key: string]: unknown;
}
export interface MissionStep { key: string; title: string; kind: string; state: string; attempts: number; cost_usd: number; error: string; evidence: string }
export interface MissionLead {
  id: number; name: string; score: number; status: string; address: string; phone: string; email: string; website: string;
  website_status: string; confidence: number; missing: string[]; evidence: string[]; sources: string[]; category: string;
}
export interface MissionEvent { at: number; level: string; text: string }
export interface MissionApproval { id: number; lead_id: number; action: string; summary: string; status: string; result: string }
export interface MissionOutreach { id: number; kind: string; recipient: string; subject: string; body: string; status: string; problems: string[] }
export interface MissionProject { id: number; lead: string; path: string; status: string }
export interface MissionOperation { id: string; kind: string; what: string; state: string; error: string }
export interface MissionAttention { type: string; id: string; what: string; why: string; actions: string[] }
export interface MissionDetail {
  mission: MissionSummary;
  steps: MissionStep[];
  leads: MissionLead[];
  outreach: MissionOutreach[];
  approvals: MissionApproval[];
  projects: MissionProject[];
  operations: MissionOperation[];
  attention: MissionAttention[];
  events: MissionEvent[];
  goal?: {
    understood?: string; constraints?: string[]; defaults?: string[];
    criteria?: { id: string; met: boolean; what: string; got: number | null; target: number; metric?: string }[];
    plan?: Record<string, { done: number; failed: number; running: number; total: number }>;
  };
  owner?: { owner: boolean };
  error?: string;
}
export interface ConnectionLevel { id: string; label: string; granted: boolean; enabled: boolean; consequential: boolean }
export interface ConnectionService { id: string; name: string; levels: ConnectionLevel[] }
export interface ConnectionAccount {
  account: string; active: boolean; status: string; last_success: number | null; last_error: string;
  permissions: string[]; services: ConnectionService[];
}
export interface ConnectionProvider { id: string; name: string; icon: string; configured: boolean; setup: string; accounts: ConnectionAccount[] }
export interface Flow { state: "pending" | "done" | "canceled" | "error"; message: string; at: number }

export interface EnvField { key: string; value: string; secret: boolean; has_value: boolean; comment: string; type: string; options: { value: string; label: string }[] | string[] | null }
export interface EnvSection { title: string; fields: EnvField[] }
export interface EnvSaveResult { ok: boolean; changed: string[]; applied: string[]; live_error: string; restart_required: boolean }

export interface VoiceCatalogue {
  enabled: boolean; fallback: boolean; name: string; provider: string; selected: string;
  providers: { id: string; name: string; voices: { id: string; name: string; description?: string }[] }[];
}

export interface HomeDevice { [key: string]: unknown }
export interface VoiceProvider { id: string; name: string; voices?: { id: string; name: string; description?: string }[] }

export interface LiveSnapshot {
  online: boolean;
  state?: { name: Phase; label: string };
  user?: string;
  location?: string;
  brain?: string;
  hearing?: string;
  voice?: string;
  wake_word?: string;
  driving?: boolean;
  in_call?: boolean;
  activity?: ActivityItem[];
  conversation?: ConversationTurn[];
  tasks?: TaskItem[];
  timers?: unknown[];
  missions?: MissionSummary[];
  connections?: { google?: string; phone?: string };
  health?: { heartbeat_age_s: number; pid: number; state: string; uptime_s: number };
  home?: { devices: HomeDevice[]; online: boolean };
  lists?: { lists?: Record<string, string[]>; moments?: unknown[] };
  now_playing?: { app: string; artist: string; playing: boolean; title: string } | null;
  phone?: { driving: boolean; in_call: boolean; mode: boolean; number: string; ready: boolean };
  research?: { at: number; failed: string[]; question: string; seconds: number; status: string; sources: unknown[] } | null;
  spend?: { limit: number; today: number };
  timing?: Record<string, number>;
  volume?: { level: number; muted: boolean };
  voice_settings?: { enabled: boolean; fallback: boolean; name: string; provider: string; providers: VoiceProvider[]; selected: string };
  ringing?: unknown;
  undo?: unknown;
}

export interface StatusInfo {
  daily_budget: number;
  llm_default: string;
  llm_provider: string;
  model: string;
  ollama_model: string;
  openai_model: string;
  mic_device: string;
  speaker_device: string;
  stt_provider: string;
  tts_provider: string;
  whisper_model: string;
  wake_word: string;
  speaker_verify: boolean;
  voiceprint_enrolled: boolean;
  spend: { calls: number; date: string; usd: Record<string, number> };
}

export interface ActionResult { ok?: boolean; outcome?: string; message?: string; error?: string; url?: string }
export interface CommandResult { reply?: string; ok?: boolean; error?: string; message?: string }

export interface KnowledgeFact { id: number; text: string; saved: string; category: string }
export interface KnowledgePreference { key: string; text: string; kind: string; source: string; confidence: number; applied: boolean; auto: boolean; because: string; evidence: number }
export interface Knowledge {
  facts: KnowledgeFact[];
  preferences: KnowledgePreference[];
  profile: { key: string; label: string; value: string }[];
  summaries: { date: string; summary: string }[];
}