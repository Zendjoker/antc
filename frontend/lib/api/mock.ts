import type { LiveSnapshot, StatusInfo } from "./types";

const now = () => Date.now() / 1000;

const live: LiveSnapshot = {
  online: true,
  state: { name: "listening", label: "Listening" },
  user: "Demo",
  location: "Living room",
  wake_word: "hey_jarvis",
  activity: [
    { at: now() - 60, kind: "light", text: "Turned on the desk lamp" },
    { at: now() - 600, kind: "timer", text: "Timer finished: tea" },
  ],
  conversation: [
    { role: "user", text: "Turn on the desk lamp", time: "10:41" },
    { role: "assistant", text: "Done. The desk lamp is on.", time: "10:41" },
  ],
  tasks: [],
  timers: [],
  missions: [],
  home: { online: true, devices: [] },
  connections: { google: "connected", phone: "ready" },
  spend: { limit: 4.5, today: 0.05 },
  volume: { level: 40, muted: false },
};

const status: StatusInfo = {
  daily_budget: 4.5, llm_default: "openai", llm_provider: "claude", model: "mock-model", ollama_model: "",
  openai_model: "", mic_device: "Mock microphone", speaker_device: "Mock speaker", stt_provider: "whisper",
  tts_provider: "piper", whisper_model: "base", wake_word: "hey_jarvis", speaker_verify: false,
  voiceprint_enrolled: false, spend: { calls: 3, date: "2026-01-01", usd: { openai: 0.05 } },
};

// Fixtures for development without the Python backend (NEXT_PUBLIC_ZEND_MOCK=1). Display data only.
export async function mockGet<T>(path: string): Promise<T> {
  if (path.startsWith("/api/live")) return live as T;
  if (path.startsWith("/api/status")) return status as T;
  throw new Error(`No mock for ${path}`);
}

export async function mockPost<T>(path: string, body: unknown): Promise<T> {
  if (path === "/api/command") return { reply: "This is a mock reply." } as T;
  return { ok: true, message: `Mock: ${JSON.stringify(body)}` } as T;
}
