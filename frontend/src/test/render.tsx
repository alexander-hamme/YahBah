import { QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { vi } from "vitest";
import { makeQueryClient } from "@/components/providers";
import { Toaster } from "@/components/toaster";

/** Render inside the app's real QueryClient setup (MutationCache error toasts), without retries. */
export function renderWithClient(ui: React.ReactElement) {
  const client = makeQueryClient();
  client.setDefaultOptions({ queries: { retry: false, staleTime: 0 }, mutations: { retry: false } });
  const result = render(
    <QueryClientProvider client={client}>
      {ui}
      <Toaster />
    </QueryClientProvider>
  );
  return { ...result, client };
}

export interface ApiCall {
  method: string;
  path: string;
  body: unknown;
}

type Reply = { status?: number; body: unknown };
type Handler = Reply | ((call: ApiCall) => Reply);

/**
 * Stub fetch for `/api/...` calls. Keys are "METHOD /path" (query string ignored unless the key
 * has one). Unmatched calls fail the test with a 599 so they're easy to spot.
 */
export function mockApi(handlers: Record<string, Handler>) {
  const calls: ApiCall[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    const method = (init?.method ?? "GET").toUpperCase();
    const path = url.pathname.replace(/^\/api/, "");
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
    const call = { method, path: path + url.search, body };
    calls.push(call);
    const handler = handlers[`${method} ${path}${url.search}`] ?? handlers[`${method} ${path}`];
    const reply: Reply = handler
      ? typeof handler === "function"
        ? handler(call)
        : handler
      : { status: 599, body: { detail: { code: "unmocked", message: `${method} ${path}` } } };
    return new Response(JSON.stringify(reply.body), {
      status: reply.status ?? 200,
      headers: { "Content-Type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, fetchMock };
}

export const SETTINGS_REVIEW = { submission_mode: "review", review_window_hours: 48 } as const;
