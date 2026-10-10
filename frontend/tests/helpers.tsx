import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { vi } from "vitest";
import { ConfirmProvider } from "@/components/providers/confirm-provider";
import { FeedbackProvider } from "@/components/providers/feedback-provider";
import { ThemeProvider } from "@/components/providers/theme-provider";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/overview",
}));

export interface Call { method: string; path: string; headers: Record<string, string>; body: unknown }
type Handler = unknown | ((call: Call) => unknown | Response);

export const LIVE_ONLINE = { online: true, state: { name: "listening", label: "Listening" }, user: "Test", conversation: [], activity: [], tasks: [], missions: [], timers: [], home: { online: true, devices: [] } };

/** Stubs fetch with a route table keyed "METHOD /path". Unknown routes return 404 so a stray call is visible. */
export function mockBackend(routes: Record<string, Handler>) {
  const calls: Call[] = [];
  const table: Record<string, Handler> = { "GET /api/live": LIVE_ONLINE, ...routes };
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    const [path] = String(url).split("?");
    const method = init.method ?? "GET";
    const call: Call = { method, path, headers: (init.headers ?? {}) as Record<string, string>, body: init.body ? JSON.parse(String(init.body)) : undefined };
    calls.push(call);
    const h = table[`${method} ${path}`];
    if (h === undefined) return new Response(JSON.stringify({ error: "not mocked" }), { status: 404 });
    const out = typeof h === "function" ? (h as (c: Call) => unknown)(call) : h;
    if (out instanceof Response) return out;
    return new Response(JSON.stringify(out), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
  return { calls, posts: () => calls.filter((c) => c.method === "POST") };
}

export function renderApp(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ThemeProvider>
        <FeedbackProvider>
          <ConfirmProvider>{ui}</ConfirmProvider>
        </FeedbackProvider>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}
