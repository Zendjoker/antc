import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AssistantView } from "@/features/assistant/assistant-view";
import { LIVE_ONLINE, mockBackend, renderApp } from "./helpers";

afterEach(() => vi.unstubAllGlobals());

describe("Assistant", () => {
  it("sends on Enter, clears the draft, and shows the reply", async () => {
    const be = mockBackend({ "POST /api/command": { reply: "Done." } });
    renderApp(<AssistantView />);
    const box = await screen.findByLabelText("Message");
    await waitFor(() => expect(screen.getByRole("button", { name: "Brief me" })).toBeEnabled());
    await userEvent.type(box, "turn on the lamp{Enter}");
    expect(await screen.findByText("Done.")).toBeInTheDocument();
    expect(be.posts().find((c) => c.path === "/api/command")?.body).toEqual({ text: "turn on the lamp" });
    expect(box).toHaveValue("");
  });

  it("Shift+Enter inserts a newline instead of sending", async () => {
    const be = mockBackend({ "POST /api/command": { reply: "x" } });
    renderApp(<AssistantView />);
    const box = await screen.findByLabelText("Message");
    await userEvent.type(box, "line one{Shift>}{Enter}{/Shift}line two");
    expect(box).toHaveValue("line one\nline two");
    expect(be.posts()).toHaveLength(0);
  });

  it("keeps the draft and warns when the request fails", async () => {
    mockBackend({ "POST /api/command": { ok: false, error: "model unavailable" } });
    renderApp(<AssistantView />);
    const box = await screen.findByLabelText("Message");
    await waitFor(() => expect(screen.getByRole("button", { name: "Brief me" })).toBeEnabled());
    await userEvent.type(box, "hello{Enter}");
    expect((await screen.findAllByText(/model unavailable.*draft is retained/i)).length).toBeGreaterThan(0);
    expect(box).toHaveValue("hello");
  });

  it("requires confirmation before re-sending a request whose outcome is unknown", async () => {
    let n = 0;
    const be = mockBackend({ "POST /api/command": () => (++n === 1 ? new Response("not json", { status: 200 }) : { reply: "ok" }) });
    renderApp(<AssistantView />);
    const box = await screen.findByLabelText("Message");
    await waitFor(() => expect(screen.getByRole("button", { name: "Brief me" })).toBeEnabled());
    await userEvent.type(box, "order pizza{Enter}");
    await screen.findAllByText(/draft is retained/i);
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText(/may already have run/i)).toBeInTheDocument();
    expect(be.posts()).toHaveLength(1);
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(be.posts()).toHaveLength(1);
  });

  it("does not send while ZendAgent is offline and keeps the draft", async () => {
    const be = mockBackend({ "GET /api/live": { ...LIVE_ONLINE, online: false } });
    renderApp(<AssistantView />);
    const box = await screen.findByLabelText("Message");
    await userEvent.type(box, "hello{Enter}");
    expect(be.posts()).toHaveLength(0);
    expect(box).toHaveValue("hello");
  });
});
