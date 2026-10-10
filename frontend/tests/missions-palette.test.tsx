import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { matchCommands } from "@/components/layout/command-palette";
import { MissionsView } from "@/features/missions/missions-view";
import { LIVE_ONLINE, mockBackend, renderApp } from "./helpers";

afterEach(() => vi.unstubAllGlobals());

const summary = { id: "m1", kind: "business", title: "Find clients", state: "running", current: "Researching", error: "", summary: "", budget_usd: 1, spent_usd: 0.2, steps_done: 2, steps_failed: 0, steps_total: 10, leads_found: 5, leads_researched: 2, leads_qualifying: 1, demos: 0, drafts: 0, requests: 3, errors: [], abandoned_runs: 0, approvals_pending: 1, approvals_unknown: 0 };
const detail = { mission: summary, steps: [{ key: "s1", title: "Discover", kind: "discover", state: "completed", attempts: 1, cost_usd: 0, error: "", evidence: "" }], leads: [], outreach: [], projects: [], operations: [], attention: [], events: [], approvals: [{ id: 9, lead_id: 1, action: "email", summary: "Email Acme", status: "pending", result: "" }], goal: { criteria: [] }, owner: { owner: true } };

function backend() {
  return mockBackend({
    "GET /api/live": { ...LIVE_ONLINE, missions: [summary] },
    "GET /api/missions": detail,
    "POST /api/action": { ok: true, message: "Done." },
  });
}

describe("Missions", () => {
  it("shows live progress and what is waiting for the user", async () => {
    backend();
    renderApp(<MissionsView />);
    expect(await screen.findByRole("heading", { name: "Find clients" })).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "Mission progress" })).toHaveAttribute("aria-valuenow", "20");
    expect(await screen.findByText("Email Acme")).toBeInTheDocument();
  });

  it("requires confirmation to stop a mission", async () => {
    const be = backend();
    renderApp(<MissionsView />);
    await userEvent.click(await screen.findByRole("button", { name: /^Stop$/ }));
    expect(await screen.findByText("Stop this mission?")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(be.posts()).toHaveLength(0);
    await userEvent.click(screen.getByRole("button", { name: /^Stop$/ }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Stop mission" }));
    await screen.findByText("Done.");
    expect(be.posts()[0].body).toEqual({ id: "m1", do: "mission_stop" });
  });

  it("approving an outreach item needs explicit confirmation and sends the approval id", async () => {
    const be = backend();
    renderApp(<MissionsView />);
    await userEvent.click(await screen.findByRole("button", { name: /Approve \(Gmail draft, not sent\)/ }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/Nothing is sent/)).toBeInTheDocument();
    expect(be.posts()).toHaveLength(0);
    await userEvent.click(within(dialog).getByRole("button", { name: "Approve" }));
    await screen.findByText("Done.");
    expect(be.posts()[0].body).toEqual({ id: "m1", do: "approval_approve", approval_id: 9 });
  });

  it("shows an empty state when there are no missions", async () => {
    mockBackend({});
    renderApp(<MissionsView />);
    expect(await screen.findByText("No missions yet")).toBeInTheDocument();
  });
});

describe("Command palette", () => {
  const cmds = [
    { id: "a", group: "Go to", label: "Settings", run: () => {} },
    { id: "b", group: "Actions", label: "Stop everything", keys: "emergency halt", run: () => {} },
    { id: "c", group: "Actions", label: "Undo last change", disabled: true, run: () => {} },
  ];
  it("matches labels and hidden keywords, and hides disabled commands", () => {
    expect(matchCommands(cmds, "halt").map((c) => c.id)).toEqual(["b"]);
    expect(matchCommands(cmds, "undo")).toEqual([]);
    expect(matchCommands(cmds, "").map((c) => c.id)).toEqual(["a", "b"]);
  });
});
