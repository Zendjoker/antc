import { ApiError, apiPostRaw } from "./client";

export type Outcome = "completed" | "accepted" | "failed" | "unknown";

export interface BackendResult {
  ok: boolean;
  outcome: Outcome;
  message: string;
  reply?: string;
  url?: string;
  [key: string]: unknown;
}

const UNKNOWN = "Outcome unknown. The connection failed or the response was invalid. Check current state before retrying.";
const flights = new Map<string, Promise<BackendResult>>();

/**
 * Posts to the backend and classifies the result like the original dashboard does: an explicit rejection is a
 * failure, HTTP 202 is "accepted" (not confirmed), and anything we can't interpret is "unknown", never success.
 */
export async function callBackend(path: string, body: unknown, timeoutMs?: number): Promise<BackendResult> {
  try {
    const { data, status } = await apiPostRaw<Record<string, unknown>>(path, body, timeoutMs);
    if (data.ok === false || data.error) {
      return { ...data, ok: false, outcome: "failed", message: String(data.message || data.error || "Request rejected.") };
    }
    const valid = path === "/api/command" ? typeof data.reply === "string" : typeof data.ok === "boolean";
    if (!valid) return { ok: false, outcome: "unknown", message: "Unexpected response. Outcome unknown; check current state before retrying." };
    return { ...data, ok: true, outcome: status === 202 ? "accepted" : "completed", message: String(data.message ?? "") };
  } catch (e) {
    if (e instanceof ApiError && (e.kind === "http" || e.kind === "refused")) return { ok: false, outcome: "failed", message: e.message };
    return { ok: false, outcome: "unknown", message: UNKNOWN };
  }
}

/** Identical in-flight actions share one request, so a double click can't run an action twice. */
export function dedupedAction(body: unknown): Promise<BackendResult> {
  const key = JSON.stringify(body);
  const existing = flights.get(key);
  if (existing) return existing;
  const p = callBackend("/api/action", body).finally(() => flights.delete(key));
  flights.set(key, p);
  return p;
}

export const sendCommand = (text: string) => callBackend("/api/command", { text }, 95000);
