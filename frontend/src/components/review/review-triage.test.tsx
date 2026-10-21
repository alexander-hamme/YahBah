import { QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, renderHook, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { reviewQueryKey } from "@/components/review/review-panel";
import { ReviewWorkspace } from "@/components/review/review-workspace";
import { DWELL_MS, UNDO_MS, useTriageDecisions } from "@/components/review/use-triage-decisions";
import { makeQueryClient } from "@/components/providers";
import { REVIEW_QUEUE_KEY } from "@/lib/queries";
import { makeListItem, makeReview, makeRow } from "@/test/fixtures";
import { router, setLocation } from "@/test/navigation";
import { mockApi, renderWithClient, SETTINGS_REVIEW, type ApiCall } from "@/test/render";
import { getToasts } from "@/lib/toast";
import type { ReviewPayload } from "@/lib/types";

// Triage: quick yup / nope from the queue (contract §4). The window before a
// decision is sent is real (UNDO_MS) on a fake clock; the safety tests assert no
// decision request reaches fetch at all.

const soon = makeListItem({ run_id: "run-soon", company: "Acme", review_deadline: "2026-10-20T02:30:00Z", review_round: 1 });
const later = makeListItem({ run_id: "run-later", company: "Globex", review_deadline: "2026-10-21T12:00:00Z" });

const STORY =
  "At my last job I single-handedly rescued a failing launch over one weekend,\n" +
  "rewrote the billing service in Rust, and was promoted twice in a year. ".repeat(3);

function soonReview(overrides: Partial<ReviewPayload> = {}): ReviewPayload {
  return makeReview({
    run_id: "run-soon",
    answers: [
      makeRow(),
      makeRow({ key: "story", label: "Tell us about a hard problem", required: false, answer: STORY, source: "custom",
                basis: [], generated: true, written: STORY, page_value: STORY }),
    ],
    cover_letter: { text: "Dear Acme team, I build reliable backends.", artifact_id: null },
    ...overrides,
  });
}

let queueItems = [soon, later];
let soonRound = 1;

function api(extra = {}) {
  return mockApi({
    "GET /settings": { body: SETTINGS_REVIEW },
    "GET /applications": () => ({ body: { items: queueItems, total: queueItems.length, page: 1, per_page: 100 } }),
    "GET /runs/run-soon/review": () => ({ body: soonReview({ review_round: soonRound }) }),
    "GET /runs/run-later/review": {
      body: makeReview({ run_id: "run-later", job: { title: "SRE", company: "Globex", url: "https://g.example/1" } }),
    },
    "POST /runs/run-soon/approve": { body: { success: true, message: "ok" } },
    "POST /runs/run-soon/reject": { body: { success: true, message: "ok" } },
    ...extra,
  });
}

const decisions = (calls: ApiCall[]) => calls.filter((c) => c.method === "POST");

async function passWindow() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(UNDO_MS * 2);
  });
}

let user: ReturnType<typeof userEvent.setup>;

beforeEach(() => {
  vi.useFakeTimers({ now: new Date("2026-10-20T00:00:00Z"), shouldAdvanceTime: true });
  user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  setLocation("/review");
  queueItems = [soon, later];
  soonRound = 1;
});
afterEach(() => {
  vi.useRealTimers();
});

/** Acme's preview, armed: a preview takes decisions only after DWELL_MS on screen. */
async function previewOfAcme() {
  const button = await screen.findByRole("button", { name: /Yup, submit/ });
  await dwell();
  return button;
}

async function dwell() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(DWELL_MS);
  });
}

describe("Triage preview", () => {
  it("shows every flagged answer, generated text in full, and collapses the rest to a count", async () => {
    api();
    renderWithClient(<ReviewWorkspace />);
    await previewOfAcme();

    const flagged = screen.getByRole("list", { name: "Answers to check" });
    expect(within(flagged).getAllByRole("listitem")).toHaveLength(1);
    // the whole story, no clamp and no "Show all" to click
    expect(within(flagged).getByText((_, el) => el?.tagName === "P" && el.textContent === STORY)).toBeTruthy();
    expect(within(flagged).queryByRole("button", { name: "Show all" })).toBeNull();
    expect(within(flagged).getByText("Generated")).toBeTruthy();
    expect(screen.getByText(/1 more answer comes from stored facts/)).toBeTruthy();
    expect(screen.getByText("Dear Acme team, I build reliable backends.")).toBeTruthy();
    expect(screen.getByAltText(/Screenshot of the filled form/)).toBeTruthy();
    const preview = screen.getByRole("heading", { level: 2, name: /Acme/ }).closest("section")!;
    expect(within(preview).getByText(/3[56]h [0-9]+m left/)).toBeTruthy(); // the fake clock ticks along with real time
  });

  it("sends the round of the rendered payload, not the list row's, after the undo window", async () => {
    soonRound = 2; // the list row still says round 1
    const { calls } = api();
    renderWithClient(<ReviewWorkspace />);
    expect(await screen.findByText("Round 2 · new fields", { selector: "section span" })).toBeTruthy();

    await user.click(await previewOfAcme());
    // moved on to the next run straight away; nothing sent yet
    expect(router.replace).toHaveBeenCalledWith("/review?run=run-later", { scroll: false });
    expect(screen.getByRole("status").textContent).toMatch(/Approving Acme: sends in 5 seconds/);
    expect(decisions(calls)).toEqual([]);

    await passWindow();
    expect(decisions(calls)).toEqual([{ method: "POST", path: "/runs/run-soon/approve", body: { review_round: 2 } }]);
    const queue = screen.getByRole("navigation", { name: "Applications awaiting review" });
    await waitFor(() => expect(within(queue).getByText("Approved")).toBeTruthy());
  });

  it("sends nothing when undone inside the window", async () => {
    const { calls } = api();
    renderWithClient(<ReviewWorkspace />);
    await user.click(await previewOfAcme());
    await user.click(screen.getByRole("button", { name: /Undo/ }));

    await passWindow();
    expect(decisions(calls)).toEqual([]);
    expect(screen.getByRole("status").textContent).toMatch(/Undone: Acme was not approved/);
    expect(router.replace).toHaveBeenLastCalledWith("/review?run=run-soon", { scroll: false });
  });

  it("sends nothing after the page unmounts", async () => {
    const { calls } = api();
    const { unmount } = renderWithClient(<ReviewWorkspace />);
    await user.click(await previewOfAcme());
    unmount();

    await passWindow();
    expect(decisions(calls)).toEqual([]);
  });

  it("sends nothing when the page is hidden during the window", async () => {
    const { calls } = api();
    renderWithClient(<ReviewWorkspace />);
    await user.click(await previewOfAcme());
    const visibility = vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    visibility.mockRestore();

    await passWindow();
    expect(decisions(calls)).toEqual([]);
  });

  it("drops the pending send when the payload is refetched with a new round", async () => {
    const { calls } = api();
    const { client } = renderWithClient(<ReviewWorkspace />);
    await user.click(await previewOfAcme());

    soonRound = 2; // the workflow found new fields
    await act(() => client.refetchQueries({ queryKey: reviewQueryKey("run-soon") }));
    // dropped at once by the cache watch, not only at send time, and said so visibly
    expect(screen.getByRole("status").textContent).toMatch(/Not sent: Acme/);
    expect(screen.queryByRole("button", { name: /^Undo/ })).toBeNull();
    expect(screen.getByText("Not sent: Acme", { selector: "[data-undo-bar] p" })).toBeTruthy();
    await passWindow();

    expect(decisions(calls)).toEqual([]);
    // back at the top of the queue, explained, with the new round to decide on
    expect(await screen.findByText(/changed to round 2 during the undo window/)).toBeTruthy();
    expect(screen.getByRole("button", { name: /Yup, submit/ })).toBeTruthy();
  });

  it("drops the pending send when the run leaves the queue", async () => {
    const { calls } = api();
    const { client } = renderWithClient(<ReviewWorkspace />);
    await user.click(await previewOfAcme());

    queueItems = [later]; // expired, or decided elsewhere
    await act(() => client.refetchQueries({ queryKey: REVIEW_QUEUE_KEY }));
    await passWindow();
    expect(decisions(calls)).toEqual([]);
  });

  it("can't approve before the run's own payload has rendered", async () => {
    const { calls, fetchMock } = api();
    const reply = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((input, init) =>
      String(input).includes("/runs/run-soon/review") ? new Promise<Response>(() => {}) : reply(input, init)
    );
    renderWithClient(<ReviewWorkspace />);

    expect(await screen.findByText("You can decide once the preview has loaded.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Yup, submit/ })).toBeNull();
    await user.keyboard("y");
    // not even queued (the send-time guard would also stop it: no rendered payload in the cache)
    expect(screen.getByRole("status").textContent).toBe("");
    expect(screen.queryByText(/Approving/)).toBeNull();
    await passWindow();
    expect(decisions(calls)).toEqual([]);
  });

  it("handles a 409 per run: explains, refreshes it, and doesn't mark it decided", async () => {
    const { calls } = api({
      "POST /runs/run-soon/approve": () => {
        soonRound = 2;
        return { status: 409, body: { detail: { code: "stale_review_round", message: "Review round 1 is not current" } } };
      },
    });
    renderWithClient(<ReviewWorkspace />);
    await user.click(await previewOfAcme());
    await passWindow();

    expect(decisions(calls)).toHaveLength(1);
    expect(await screen.findByText(/it's now round 2/)).toBeTruthy();
    const queue = screen.getByRole("navigation", { name: "Applications awaiting review" });
    const acme = within(queue).getAllByRole("listitem")[0];
    expect(within(acme).getByText("Not sent")).toBeTruthy();
    expect(within(queue).queryByText("Approved")).toBeNull();
    expect(calls.filter((c) => c.path === "/runs/run-soon/review").length).toBeGreaterThan(1);
  });
});

describe("Triage keyboard", () => {
  it("y approves, n rejects, u undoes, j moves, o opens the full review", async () => {
    const { calls } = api();
    renderWithClient(<ReviewWorkspace />);
    await previewOfAcme();

    await user.keyboard("n");
    expect(screen.getByRole("status").textContent).toMatch(/Rejecting Acme/);
    await user.keyboard("u");
    expect(screen.getByRole("status").textContent).toMatch(/Undone/);

    await user.keyboard("j");
    expect(router.replace).toHaveBeenLastCalledWith("/review?run=run-later", { scroll: false });

    await user.keyboard("o");
    expect(await screen.findByRole("checkbox", { name: /submit this application to Acme/ })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: /Back to the quick preview/ }));

    await dwell();
    await user.keyboard("y");
    await passWindow();
    expect(decisions(calls)).toEqual([{ method: "POST", path: "/runs/run-soon/approve", body: { review_round: 1 } }]);
  });

  it("ignores shortcuts while typing a reason, and sends the reason with nope", async () => {
    const { calls } = api();
    renderWithClient(<ReviewWorkspace />);
    await previewOfAcme();

    await user.click(screen.getByRole("button", { name: "with a reason…" }));
    const box = screen.getByLabelText(/Why reject it/);
    await user.type(box, "nyoju");
    expect((box as HTMLTextAreaElement).value).toBe("nyoju");
    expect(screen.getByRole("status").textContent).toBe("");
    expect(router.replace).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Nope" }));
    await passWindow();
    expect(decisions(calls)).toEqual([
      { method: "POST", path: "/runs/run-soon/reject", body: { review_round: 1, reason: "nyoju" } },
    ]);
  });
});

describe("Triage arming and outcomes", () => {
  it("Yup arms only after the dwell: a click on a preview just shown is inert", async () => {
    const { calls } = api();
    renderWithClient(<ReviewWorkspace />);
    const yup = (await screen.findByRole("button", { name: /Yup, submit/ })) as HTMLButtonElement;
    expect(yup.disabled).toBe(true);
    await user.click(yup);
    expect(screen.getByRole("status").textContent).toBe("");

    await dwell();
    expect(yup.disabled).toBe(false);
    await user.click(yup);
    // the double-click's second click lands on the next run's Yup, which hasn't armed
    await screen.findByRole("heading", { level: 2, name: /Globex/ });
    await user.click(screen.getByRole("button", { name: /Yup, submit/ }));
    await passWindow();
    expect(decisions(calls).map((c) => c.path)).toEqual(["/runs/run-soon/approve"]);
  });

  it("a new round under an open preview is called out and re-arms before it can be approved", async () => {
    const { calls } = api();
    const { client } = renderWithClient(<ReviewWorkspace />);
    await previewOfAcme();

    soonRound = 2;
    await act(() => client.refetchQueries({ queryKey: reviewQueryKey("run-soon") }));
    expect(await screen.findByText(/moved to round 2 while you were looking/)).toBeTruthy();
    expect((screen.getByRole("button", { name: /Yup, submit/ }) as HTMLButtonElement).disabled).toBe(true);

    await dwell();
    await user.keyboard("y");
    await passWindow();
    expect(decisions(calls)).toEqual([{ method: "POST", path: "/runs/run-soon/approve", body: { review_round: 2 } }]);
  });

  it("an armed preview re-arms after the tab was hidden: an immediate y on return does nothing", async () => {
    const { calls } = api();
    renderWithClient(<ReviewWorkspace />);
    await previewOfAcme(); // armed
    const setVisibility = (state: DocumentVisibilityState) => {
      const spy = vi.spyOn(document, "visibilityState", "get").mockReturnValue(state);
      act(() => {
        document.dispatchEvent(new Event("visibilitychange"));
      });
      spy.mockRestore();
    };
    setVisibility("hidden");
    setVisibility("visible");
    await user.keyboard("y");
    expect(screen.getByRole("status").textContent).toBe("");
    await dwell();
    await user.keyboard("y");
    expect(screen.getByRole("status").textContent).toMatch(/Approving Acme/);
    await passWindow();
    expect(decisions(calls)).toHaveLength(1);
  });

  it("a key repeat never decides, even on an armed preview", async () => {
    const { calls } = api();
    renderWithClient(<ReviewWorkspace />);
    await previewOfAcme();
    act(() => {
      fireEvent.keyDown(document.body, { key: "y", repeat: true });
      fireEvent.keyDown(document.body, { key: "n", repeat: true });
    });
    expect(screen.getByRole("status").textContent).toBe("");
    await passWindow();
    expect(decisions(calls)).toEqual([]);
  });

  it("y stays inert while a load error shows, however long it shows", async () => {
    let fail = false;
    const { calls } = api({
      "GET /runs/run-soon/review": () =>
        fail ? { status: 503, body: { detail: { code: "unavailable", message: "Down" } } } : { body: soonReview() },
    });
    const { client } = renderWithClient(<ReviewWorkspace />);
    await previewOfAcme();

    fail = true;
    await act(() => client.refetchQueries({ queryKey: reviewQueryKey("run-soon") }));
    expect(await screen.findByText("Couldn't load the preview")).toBeTruthy();
    await dwell();
    await dwell();
    await user.keyboard("y");
    expect(screen.getByRole("status").textContent).toBe("");
    await passWindow();
    expect(decisions(calls)).toEqual([]);
  });

  it("Esc in a full review doesn't undo another run's pending decision; from the undo bar it does", async () => {
    const { calls } = api();
    renderWithClient(<ReviewWorkspace />);
    await user.click(await previewOfAcme());
    await screen.findByRole("heading", { level: 2, name: /Globex/ });

    await user.click(screen.getByRole("button", { name: /Open full review/ }));
    await screen.findByRole("checkbox", { name: /submit this application to Globex/ });
    await user.keyboard("{Escape}u");
    expect(screen.getByRole("status").textContent).toMatch(/Approving Acme/);

    screen.getByRole("button", { name: /^Undo/ }).focus();
    await user.keyboard("{Escape}");
    expect(screen.getByRole("status").textContent).toMatch(/Undone: Acme/);
    await passWindow();
    expect(decisions(calls)).toEqual([]);
  });

  it("a 4xx is reported as not sent; a 5xx or no response as unconfirmed, with a way to check", async () => {
    api({
      "POST /runs/run-soon/approve": { status: 422, body: { detail: { code: "invalid", message: "Bad round" } } },
      "POST /runs/run-later/approve": { status: 502, body: { detail: { code: "bad_gateway", message: "Upstream failed" } } },
    });
    renderWithClient(<ReviewWorkspace />);
    await user.click(await previewOfAcme());
    await screen.findByRole("heading", { level: 2, name: /Globex/ });
    await dwell();
    await user.keyboard("y");
    await passWindow();

    const queue = screen.getByRole("navigation", { name: "Applications awaiting review" });
    const [acme, globex] = within(queue).getAllByRole("listitem");
    await waitFor(() => expect(within(acme).getByText("Not sent")).toBeTruthy());
    expect(within(globex).getByText("Unconfirmed")).toBeTruthy();
    expect(within(globex).queryByText("Not sent")).toBeNull();
    expect(screen.getByText("Couldn't confirm: Globex")).toBeTruthy();
  });
});

describe("Triage reports that outlive the moment", () => {
  it("keeps every undismissed note: past the newest three, '+N more' shows the rest", async () => {
    const runs = ["a", "b", "c", "d"].map((id, i) =>
      makeListItem({ run_id: `run-${id}`, company: `Co ${id.toUpperCase()}`, review_deadline: `2026-10-2${i + 1}T00:00:00Z` })
    );
    const reviews = Object.fromEntries(
      runs.map((r) => [
        `GET /runs/${r.run_id}/review`,
        { body: makeReview({ run_id: r.run_id, job: { title: "Eng", company: r.company, url: "https://x.example/1" } }) },
      ])
    );
    const { calls } = mockApi({
      "GET /settings": { body: SETTINGS_REVIEW },
      "GET /applications": { body: { items: runs, total: 4, page: 1, per_page: 100 } },
      ...reviews,
    });
    renderWithClient(<ReviewWorkspace />);
    for (const r of runs) {
      await screen.findByRole("heading", { level: 2, name: new RegExp(r.company!) });
      await dwell();
      await user.keyboard("y");
    }
    const visibility = vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    visibility.mockRestore();

    const bar = () => within(document.querySelector<HTMLElement>("[data-undo-bar]")!);
    expect(bar().getAllByText(/^Not sent: Co/)).toHaveLength(3);
    expect(bar().queryByText("Not sent: Co A")).toBeNull();
    await user.click(bar().getByRole("button", { name: "+1 more not sent" }));
    expect(bar().getAllByText(/^Not sent: Co/)).toHaveLength(4);
    await passWindow();
    expect(decisions(calls)).toEqual([]);
  });

  it("an in-flight decision refused after the page unmounts is toasted as not recorded", async () => {
    const { fetchMock } = api();
    const reply = fetchMock.getMockImplementation()!;
    let respond!: (r: Response) => void;
    fetchMock.mockImplementation((input, init) =>
      String(input).includes("/runs/run-soon/approve")
        ? new Promise<Response>((resolve) => (respond = resolve))
        : reply(input, init)
    );
    const { unmount } = renderWithClient(<ReviewWorkspace />);
    await user.click(await previewOfAcme());
    await passWindow();
    unmount();
    await act(async () => {
      respond(
        new Response(JSON.stringify({ detail: { code: "not_awaiting_review", message: "Not in review" } }), { status: 409 })
      );
      await vi.advanceTimersByTimeAsync(10);
    });
    const toastItem = getToasts().find((t) => t.title === "Not recorded: Acme");
    expect(toastItem?.kind).toBe("error");
    expect(toastItem?.error?.code).toBe("not_awaiting_review");
  });

  it("an in-flight decision with no response after the page unmounts is toasted as unconfirmed, with the run", async () => {
    const { fetchMock } = api();
    const reply = fetchMock.getMockImplementation()!;
    let fail!: () => void;
    fetchMock.mockImplementation((input, init) =>
      String(input).includes("/runs/run-soon/approve")
        ? new Promise<Response>((_, reject) => (fail = () => reject(new TypeError("network"))))
        : reply(input, init)
    );
    const { unmount } = renderWithClient(<ReviewWorkspace />);
    await user.click(await previewOfAcme());
    await passWindow();
    unmount();
    await act(async () => {
      fail();
      await vi.advanceTimersByTimeAsync(10);
    });
    const toastItem = getToasts().find((t) => t.title === "Couldn't confirm: Acme");
    expect(toastItem?.error?.message).toMatch(/may or may not have been recorded/);
    expect(toastItem?.error?.runId).toBe("run-soon"); // the toast links to the run
  });

  it("an in-flight decision that succeeds after the page unmounts is confirmed briefly", async () => {
    const { fetchMock } = api();
    const reply = fetchMock.getMockImplementation()!;
    let respond!: (r: Response) => void;
    fetchMock.mockImplementation((input, init) =>
      String(input).includes("/runs/run-soon/approve")
        ? new Promise<Response>((resolve) => (respond = resolve))
        : reply(input, init)
    );
    const { unmount } = renderWithClient(<ReviewWorkspace />);
    await user.click(await previewOfAcme());
    await passWindow();
    unmount();
    await act(async () => {
      respond(new Response(JSON.stringify({ success: true, message: "ok" }), { status: 200 }));
      await vi.advanceTimersByTimeAsync(10);
    });
    expect(getToasts().map((t) => [t.kind, t.title])).toEqual([["success", "Approved: Acme"]]);
  });
});

describe("useTriageDecisions", () => {
  function hook() {
    const client = makeQueryClient();
    client.setQueryData(reviewQueryKey("r1"), makeReview({ run_id: "r1" }));
    client.setQueryData(REVIEW_QUEUE_KEY, { items: [makeListItem({ run_id: "r1" })], total: 1, page: 1, per_page: 100 });
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    return renderHook(() => useTriageDecisions({ onSent: () => {} }), { wrapper });
  }
  const base = { runId: "r1", round: 1, reason: null, company: "Acme" };

  it("ignores a second decision for a run already waiting: the first one stands", async () => {
    const { calls } = mockApi({
      "POST /runs/r1/approve": { body: { success: true, message: "ok" } },
      "POST /runs/r1/reject": { body: { success: true, message: "ok" } },
    });
    const { result } = hook();
    act(() => result.current.decide({ ...base, decision: "approved" }));
    act(() => result.current.decide({ ...base, decision: "rejected" }));
    await passWindow();
    expect(decisions(calls).map((c) => c.path)).toEqual(["/runs/r1/approve"]);
  });

  it("ignores a new decision while one is in flight", async () => {
    const { fetchMock } = mockApi({});
    fetchMock.mockImplementation(() => new Promise<Response>(() => {}));
    const { result } = hook();
    act(() => result.current.decide({ ...base, decision: "approved" }));
    await passWindow();
    expect(result.current.pending.map((p) => p.phase)).toEqual(["sending"]);
    act(() => result.current.decide({ ...base, decision: "approved" }));
    await passWindow();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
