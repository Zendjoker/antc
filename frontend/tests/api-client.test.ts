import { afterEach, describe, expect, it, vi } from "vitest";
import { callBackend } from "@/lib/api/actions";
import { apiPost } from "@/lib/api/client";
import { mockBackend } from "./helpers";

afterEach(() => vi.unstubAllGlobals());

describe("API client security and outcome handling", () => {
  it("sends mutations same-origin with the X-Jarvis marker and no credentials or tokens", async () => {
    const be = mockBackend({ "POST /api/action": { ok: true } });
    await apiPost("/api/action", { do: "undo" });
    const [url, init] = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.at(-1)!;
    expect(url).toBe("/api/action");
    expect(be.posts()[0].headers["X-Jarvis"]).toBe("1");
    expect(JSON.stringify(init.headers)).not.toMatch(/authorization|token|bearer/i);
    expect(init.credentials).toBe("same-origin");
  });

  it("treats an explicit rejection as a failure", async () => {
    mockBackend({ "POST /api/action": { ok: false, message: "Nope." } });
    expect(await callBackend("/api/action", {})).toMatchObject({ ok: false, outcome: "failed", message: "Nope." });
  });

  it("treats HTTP 202 as accepted, not completed", async () => {
    mockBackend({ "POST /api/action": () => new Response(JSON.stringify({ ok: true }), { status: 202 }) });
    expect(await callBackend("/api/action", {})).toMatchObject({ ok: true, outcome: "accepted" });
  });

  it("never reports success for a malformed response", async () => {
    mockBackend({ "POST /api/action": { surprise: 1 } });
    expect(await callBackend("/api/action", {})).toMatchObject({ ok: false, outcome: "unknown" });
  });

  it("reports an unknown outcome when the connection drops mid-request", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("network"); }));
    expect(await callBackend("/api/action", {})).toMatchObject({ ok: false, outcome: "unknown" });
  });

  it("surfaces a server refusal (403) as a failure with a clear message", async () => {
    mockBackend({ "POST /api/action": () => new Response(JSON.stringify({ error: "refused" }), { status: 403 }) });
    const r = await callBackend("/api/action", {});
    expect(r.outcome).toBe("failed");
    expect(r.message).toMatch(/refused/i);
  });

  it("requires a string reply for commands", async () => {
    mockBackend({ "POST /api/command": { ok: true } });
    expect((await callBackend("/api/command", { text: "hi" })).outcome).toBe("unknown");
  });
});
