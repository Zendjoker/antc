import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { dirtyChanges, filterSections } from "@/features/settings/fields";
import { SettingsView } from "@/features/settings/settings-view";
import type { EnvSection } from "@/lib/api/types";
import { mockBackend, renderApp } from "./helpers";

afterEach(() => vi.unstubAllGlobals());

const sections: EnvSection[] = [
  { title: "Brains", fields: [
    { key: "LLM_PROVIDER", value: "claude", secret: false, has_value: true, comment: "", type: "text", options: [{ value: "claude", label: "Claude" }, { value: "openai", label: "OpenAI" }] },
    { key: "DAILY_BUDGET_USD", value: "4.5", secret: false, has_value: true, comment: "spend cap", type: "number", options: null },
    { key: "BARGE_IN", value: "1", secret: false, has_value: true, comment: "", type: "bool", options: null },
  ] },
  { title: "API keys", fields: [
    { key: "OPENAI_API_KEY", value: "sk-a…EwAA", secret: true, has_value: true, comment: "", type: "text", options: null },
  ] },
];

describe("settings helpers", () => {
  it("only reports fields that differ from the baseline", () => {
    expect(dirtyChanges(sections, { LLM_PROVIDER: "claude", DAILY_BUDGET_USD: "6" })).toEqual({ DAILY_BUDGET_USD: "6" });
  });
  it("never sends a blank secret", () => {
    expect(dirtyChanges(sections, { OPENAI_API_KEY: "" })).toEqual({});
    expect(dirtyChanges(sections, { OPENAI_API_KEY: "sk-new" })).toEqual({ OPENAI_API_KEY: "sk-new" });
  });
  it("filters by key, label and comment", () => {
    expect(filterSections(sections, "spend").map((s) => s.title)).toEqual(["Brains"]);
    expect(filterSections(sections, "zzz")).toEqual([]);
  });
});

describe("Settings page", () => {
  it("saves only changed fields, masks secrets, and reports the restart requirement", async () => {
    const be = mockBackend({
      "GET /api/env": { sections },
      "POST /api/env": { ok: true, changed: ["DAILY_BUDGET_USD"], applied: [], live_error: "", restart_required: true },
    });
    renderApp(<SettingsView />);
    const budget = await screen.findByLabelText("Daily spending limit ($)");
    const save = screen.getByRole("button", { name: /save changes/i });
    expect(save).toBeDisabled();

    await userEvent.clear(budget);
    await userEvent.type(budget, "6");
    expect(screen.getByText("1 unsaved change")).toBeInTheDocument();
    await userEvent.click(save);

    const post = be.posts().find((c) => c.path === "/api/env")!;
    expect(post.body).toEqual({ DAILY_BUDGET_USD: "6" });
    expect(post.headers["X-Jarvis"]).toBe("1");
    expect(await screen.findAllByText(/restart zendagent/i)).not.toHaveLength(0);
  });

  it("shows a secret masked and read-only until Change is pressed", async () => {
    mockBackend({ "GET /api/env": { sections } });
    renderApp(<SettingsView />);
    const key = await screen.findByLabelText("OpenAI API key");
    expect(key).toHaveAttribute("readonly");
    expect(key).toHaveValue("sk-a…EwAA");
    await userEvent.click(screen.getByRole("button", { name: "Change" }));
    expect(key).toHaveAttribute("type", "password");
    expect(key).toHaveValue("");
  });

  it("does not claim failure when the save can't be confirmed", async () => {
    mockBackend({ "GET /api/env": { sections }, "POST /api/env": () => { throw new TypeError("network"); } });
    renderApp(<SettingsView />);
    await userEvent.type(await screen.findByLabelText("Daily spending limit ($)"), "1");
    await userEvent.click(screen.getByRole("button", { name: /save changes/i }));
    expect((await screen.findAllByText(/couldn.t confirm the save/i)).length).toBeGreaterThan(0);
  });

  it("search narrows the list", async () => {
    mockBackend({ "GET /api/env": { sections } });
    renderApp(<SettingsView />);
    await userEvent.type(await screen.findByLabelText("Search settings"), "wake");
    expect(screen.getByText("No settings match")).toBeInTheDocument();
    await waitFor(() => expect(within(document.body).queryByLabelText("Daily spending limit ($)")).toBeNull());
  });
});
