import { Suspense } from "react";
import { act } from "@testing-library/react";
import { hydrateRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ApplicationsPage from "@/app/applications/page";
import ReviewPage from "@/app/review/page";
import { makeQueryClient } from "@/components/providers";
import { REVIEW_QUEUE_KEY, RUNTIME_SETTINGS_KEY } from "@/lib/queries";
import { makeListItem } from "@/test/fixtures";
import { setLocation } from "@/test/navigation";
import { mockApi, SETTINGS_REVIEW } from "@/test/render";

// SSR + hydration of whole pages. The server renders with an empty cache; on the client,
// components outside a page's Suspense boundary (the nav badges, the mode control) can
// fetch the shared queries before the boundary hydrates. The boundary's first client
// render must still match the server's HTML.

const STATS = {
  counts: { awaiting_review: 1, submit_uncertain: 0, submitted: 337 },
  total: 338,
};
const QUEUE = { items: [makeListItem({ run_id: "run-1" })], total: 1, page: 1, per_page: 100 };

function prefilled(): QueryClient {
  const client = makeQueryClient();
  client.setQueryData(["application-stats"], STATS);
  client.setQueryData(RUNTIME_SETTINGS_KEY, SETTINGS_REVIEW);
  client.setQueryData(REVIEW_QUEUE_KEY, QUEUE);
  return client;
}

async function hydrateAfterServer(page: React.ReactElement) {
  const tree = (client: QueryClient) => (
    <QueryClientProvider client={client}>
      <Suspense>{page}</Suspense>
    </QueryClientProvider>
  );
  const html = renderToString(tree(makeQueryClient()));
  const container = document.createElement("div");
  container.innerHTML = html;
  document.body.appendChild(container);

  const errors: unknown[] = [];
  const consoleError = vi.spyOn(console, "error").mockImplementation((...args) => errors.push(args));
  const root = await act(async () =>
    hydrateRoot(container, tree(prefilled()), { onRecoverableError: (e) => errors.push(e) })
  );
  await act(async () => {
    await vi.advanceTimersByTimeAsync(50);
  });
  consoleError.mockRestore();
  return { container, errors, unmount: () => act(() => root.unmount()) };
}

beforeEach(() => {
  vi.useFakeTimers({ now: new Date("2026-10-20T00:00:00Z"), shouldAdvanceTime: true });
  mockApi({
    "GET /settings": { body: SETTINGS_REVIEW },
    "GET /applications/stats": { body: STATS },
    "GET /applications": { body: QUEUE },
    "GET /runs/run-1/review": { status: 503, body: { detail: { code: "unavailable", message: "x" } } },
  });
});
afterEach(() => {
  vi.useRealTimers();
});

const hydrationErrors = (errors: unknown[]) =>
  errors.filter((e) => /hydrat|didn't match|did not match/i.test(String(Array.isArray(e) ? e.join(" ") : e)));

describe("hydration with shared queries already cached", () => {
  it("/applications hydrates without a mismatch, then shows the cached counts", async () => {
    setLocation("/applications");
    const { container, errors, unmount } = await hydrateAfterServer(<ApplicationsPage />);
    expect(hydrationErrors(errors)).toEqual([]);
    expect(container.textContent).toContain("338");
    await unmount();
  });

  it("/review hydrates without a mismatch", async () => {
    setLocation("/review");
    const { errors, unmount } = await hydrateAfterServer(<ReviewPage />);
    expect(hydrationErrors(errors)).toEqual([]);
    await unmount();
  });
});
