import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConnectionsView } from "@/features/connections/connections-view";
import { filterKnowledge, MemoryView } from "@/features/memory/memory-view";
import type { Knowledge } from "@/lib/api/types";
import { mockBackend, renderApp } from "./helpers";

afterEach(() => vi.unstubAllGlobals());

const knowledge: Knowledge = {
  profile: [{ key: "name", label: "Name", value: "Sam" }],
  preferences: [{ key: "pref:lamp", text: "keep the lamp warm", kind: "habit", source: "x", confidence: 0.8, applied: true, auto: false, because: "", evidence: 3 }],
  facts: [{ id: 7, text: "Sam's dog is called Rex", saved: "2026-10-01", category: "fact" }, { id: 8, text: "Prefers tea", saved: "2026-10-02", category: "preference" }],
  summaries: [{ date: "2026-10-03", summary: "Talked about the garden" }],
};

describe("Memory", () => {
  it("searches across facts, preferences and conversations", () => {
    const r = filterKnowledge(knowledge, "rex");
    expect(r.facts.map((f) => f.id)).toEqual([7]);
    expect(r.preferences).toEqual([]);
    expect(filterKnowledge(knowledge, "garden").summaries).toHaveLength(1);
  });

  it("asks before forgetting, and does nothing when cancelled", async () => {
    const be = mockBackend({ "GET /api/knowledge": knowledge, "POST /api/action": { ok: true, message: "Forgotten." } });
    renderApp(<MemoryView />);
    await userEvent.click(await screen.findByRole("button", { name: /Forget: Sam's dog/ }));
    expect(await screen.findByText("Forget this?")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(be.posts()).toHaveLength(0);
  });

  it("forgets through the protected action endpoint once confirmed", async () => {
    const be = mockBackend({ "GET /api/knowledge": knowledge, "POST /api/action": { ok: true, message: "Forgotten." } });
    renderApp(<MemoryView />);
    await userEvent.click(await screen.findByRole("button", { name: /Forget: Sam's dog/ }));
    await userEvent.click(await screen.findByRole("button", { name: "Forget" }));
    await screen.findByText("Forgotten.");
    expect(be.posts()[0].body).toEqual({ do: "forget_fact", id: 7 });
    expect(be.posts()[0].headers["X-Jarvis"]).toBe("1");
  });

  it("disables changes while ZendAgent is offline", async () => {
    mockBackend({ "GET /api/live": { online: false }, "GET /api/knowledge": knowledge });
    renderApp(<MemoryView />);
    expect(await screen.findByRole("button", { name: /Forget: Sam's dog/ })).toBeDisabled();
  });
});

const providers = { providers: [{ id: "google", name: "Google", icon: "google", configured: true, setup: "", accounts: [{
  account: "a@example.com", active: true, status: "connected", last_success: null, last_error: "", permissions: [],
  services: [{ id: "gmail", name: "Gmail", levels: [{ id: "read", label: "Read your email", granted: true, enabled: true, consequential: false }] }],
}] }] };

describe("Connections", () => {
  it("confirms before disconnecting and does not call the server when cancelled", async () => {
    const be = mockBackend({ "GET /api/connections": providers });
    renderApp(<ConnectionsView />);
    await userEvent.click(await screen.findByRole("button", { name: "Disconnect" }));
    expect(await screen.findByText("Disconnect a@example.com?")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(be.posts()).toHaveLength(0);
  });

  it("disconnects after confirmation and requires a boolean revoked result", async () => {
    const be = mockBackend({ "GET /api/connections": providers, "POST /api/connections/google/disconnect": { ok: true, revoked: true } });
    renderApp(<ConnectionsView />);
    await userEvent.click(await screen.findByRole("button", { name: "Disconnect" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Disconnect" }));
    expect(await screen.findByText(/revoked/i)).toBeInTheDocument();
    expect(be.posts()[0].body).toEqual({ account: "a@example.com" });
  });

  it("turning access off posts the exact service and level", async () => {
    const be = mockBackend({ "GET /api/connections": providers, "POST /api/connections/google/access": { ok: true } });
    renderApp(<ConnectionsView />);
    await userEvent.click(await screen.findByRole("switch", { name: "Gmail: Read your email" }));
    await screen.findByText("Access turned off.");
    expect(be.posts()[0].body).toEqual({ account: "a@example.com", service: "gmail", level: "read", on: false });
  });
});
