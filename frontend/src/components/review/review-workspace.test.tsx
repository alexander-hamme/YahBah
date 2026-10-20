import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ReviewWorkspace } from "@/components/review/review-workspace";
import { makeListItem, makeReview } from "@/test/fixtures";
import { router, setLocation } from "@/test/navigation";
import { mockApi, renderWithClient, SETTINGS_REVIEW } from "@/test/render";

const soon = makeListItem({ run_id: "run-soon", company: "Acme", review_deadline: "2026-10-20T02:30:00Z" });
const later = makeListItem({
  run_id: "run-later",
  company: "Globex",
  review_deadline: "2026-10-21T12:00:00Z",
  review_round: 2,
});

function api(extra = {}) {
  return mockApi({
    "GET /settings": { body: SETTINGS_REVIEW },
    "GET /applications": { body: { items: [soon, later], total: 2, page: 1, per_page: 100 } },
    "GET /runs/run-soon/review": { body: makeReview({ run_id: "run-soon" }) },
    "GET /runs/run-later/review": { body: makeReview({ run_id: "run-later", job: { title: "SRE", company: "Globex", url: "https://g.example/1" } }) },
    ...extra,
  });
}

beforeEach(() => {
  vi.useFakeTimers({ now: new Date("2026-10-20T00:00:00Z"), toFake: ["Date"] });
  setLocation("/review");
});
afterEach(() => {
  vi.useRealTimers();
});

describe("ReviewWorkspace", () => {
  it("asks for awaiting_review runs, soonest deadline first, each with a countdown", async () => {
    const { calls } = api();
    renderWithClient(<ReviewWorkspace />);

    const queue = await screen.findByRole("navigation", { name: "Applications awaiting review" });
    const items = within(queue).getAllByRole("listitem");
    expect(items.map((li) => within(li).getByText(/Acme|Globex/).textContent)).toEqual(["Acme", "Globex"]);
    expect(within(items[0]).getByText(/2h 30m left/)).toBeTruthy();
    expect(within(items[1]).getByText(/36h 0m left/)).toBeTruthy();
    expect(within(items[1]).getByText("Round 2 · new fields")).toBeTruthy();
    expect(within(items[0]).queryByText(/Round/)).toBeNull();
    expect(calls.find((c) => c.path.startsWith("/applications"))?.path).toBe(
      "/applications?per_page=100&outcome=awaiting_review&sort=review_deadline_asc"
    );
    expect(screen.getByText("2")).toBeTruthy();
  });

  it("opens the soonest one and moves to the next after a decision", async () => {
    api({ "POST /runs/run-soon/approve": { body: { success: true, message: "ok" } } });
    renderWithClient(<ReviewWorkspace />);

    expect(await screen.findByRole("heading", { name: /Acme/ })).toBeTruthy();
    await userEvent.click(await screen.findByRole("checkbox", { name: /submit this application to Acme/ }));
    await userEvent.click(screen.getByRole("button", { name: "Approve and submit" }));

    await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/review?run=run-later", { scroll: false }));
    const queue = screen.getByRole("navigation", { name: "Applications awaiting review" });
    expect(within(queue).getByText("Approved")).toBeTruthy();
  });

  it("explains an empty queue", async () => {
    mockApi({
      "GET /settings": { body: SETTINGS_REVIEW },
      "GET /applications": { body: { items: [], total: 0, page: 1, per_page: 100 } },
    });
    renderWithClient(<ReviewWorkspace />);
    expect(await screen.findByText("Nothing to review")).toBeTruthy();
  });
});
