import { mockGet, mockPost } from "./mock";

/** Marker header the Python server requires on every mutation. It is not a secret. */
const MUTATION_HEADERS = { "Content-Type": "application/json", "X-Jarvis": "1" } as const;

export const MOCK = process.env.NEXT_PUBLIC_ZEND_MOCK === "1";

export type ApiErrorKind = "offline" | "timeout" | "refused" | "http" | "invalid";

export class ApiError extends Error {
  constructor(public kind: ApiErrorKind, message: string, public status?: number) {
    super(message);
  }
}

export interface Raw<T> { data: T; status: number }

async function send<T>(path: string, init: RequestInit, timeoutMs: number): Promise<Raw<T>> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    // Same-origin relative URL: no credentials, no cross-origin calls, no tokens in the bundle.
    const res = await fetch(path, { ...init, signal: controller.signal, credentials: "same-origin" });
    let body: unknown = null;
    try { body = await res.json(); } catch { /* non-JSON body */ }
    if (res.status === 403) throw new ApiError("refused", "The server refused this request.", 403);
    if (!res.ok && res.status !== 202) {
      const msg = (body as { error?: string } | null)?.error ?? `Request failed (${res.status})`;
      throw new ApiError("http", msg, res.status);
    }
    if (body === null) throw new ApiError("invalid", "The server returned an unreadable response.", res.status);
    return { data: body as T, status: res.status };
  } catch (e) {
    if (e instanceof ApiError) throw e;
    if ((e as Error).name === "AbortError") throw new ApiError("timeout", "The server took too long to respond.");
    throw new ApiError("offline", "Can't reach the ZendAgent server.");
  } finally {
    clearTimeout(timer);
  }
}

export function apiGet<T>(path: string, timeoutMs = 5000): Promise<T> {
  if (MOCK) return mockGet<T>(path);
  return send<T>(path, { method: "GET", cache: "no-store" }, timeoutMs).then((r) => r.data);
}

/** Mutations always go through the backend's own guards (X-Jarvis, Origin, executor safety checks). */
export function apiPostRaw<T>(path: string, body: unknown = {}, timeoutMs = 35000): Promise<Raw<T>> {
  if (MOCK) return mockPost<T>(path, body).then((data) => ({ data, status: 200 }));
  return send<T>(path, { method: "POST", headers: MUTATION_HEADERS, body: JSON.stringify(body) }, timeoutMs);
}

export function apiPost<T>(path: string, body: unknown = {}, timeoutMs = 35000): Promise<T> {
  return apiPostRaw<T>(path, body, timeoutMs).then((r) => r.data);
}