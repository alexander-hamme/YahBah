import { StrictMode } from "react";
import { QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { Toaster } from "@/components/toaster";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { reviewQueryKey } from "@/components/review/review-panel";
import { ReviewWorkspace } from "@/components/review/review-workspace";
import { DWELL_MS, UNDO_MS } from "@/components/review/use-triage-decisions";
import { REVIEW_QUEUE_KEY } from "@/lib/queries";
import { getToasts } from "@/lib/toast";
import { makeListItem, makeReview, makeRow } from "@/test/fixtures";
import { router, setLocation } from "@/test/navigation";
import { mockApi, renderWithClient, SETTINGS_REVIEW, type ApiCall } from "@/test/render";
import type { ReviewPayload } from "@/lib/types";

// Adversarial QA of triage (contract §4, "Triage"). Each test is a defect
// reproduction: it FAILS against the implementation it was written for and should
// pass once the defect is fixed. Same fake-clock setup as review-triage.test.tsx.

const soon = makeListItem({ run_id: "run-soon", company: "Acme", review_deadline: "2026-10-20T02:30:00Z", review_round: 1 });
const later = makeListItem({ run_id: "run-later", company: "Globex", review_deadline: "2026-10-21T12:00:00Z" });

const VISA = makeRow({
  key: "visa",
  label: "Will you now or in the future require sponsorship?",
  required: true,
  answer: "No",
  source: "answer",
  basis: ["sponsorship_required_us"],
});

let queueItems = [soon, later];
let soonRound = 1;
let soonReviewFails = false;
let coverLetter: string = "Dear Acme team, I build reliable backends.";

function soonReview(): ReviewPayload {
  return makeReview({
    run_id: "run-soon",
    review_round: soonRound,
    answers: [makeRow()],
    new_fields: soonRound > 1 ? [VISA] : [],
    cover_letter: { text: coverLetter, artifact_id: null },
  });
}

function api(extra = {}) {
  return mockApi({
    "GET /settings": { body: SETTINGS_REVIEW },
    "GET /applications": () => ({ body: { items: queueItems, total: queueItems.length, page: 1, per_page: 100 } }),
    "GET /runs/run-soon/review": () =>
      soonReviewFails
        ? { status: 503, body: { detail: { code: "unavailable", message: "Review store unavailable" } } }
        : { body: soonReview() },
    "GET /runs/run-later/review": {
      body: makeReview({ run_id: "run-later", job: { title: "SRE", company: "Globex", url: "https://g.example/1" } }),
    },
    "POST /runs/run-soon/approve": { body: { success: true, message: "ok" } },
    "POST /runs/run-later/approve": { body: { success: true, message: "ok" } },
    ...extra,
  });
}

const decisions = (calls: ApiCall[]) => calls.filter((c) => c.method === "POST");

async function passWindow() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(UNDO_MS * 2);
  });
}

/** Text a sighted user can see: anything outside the sr-only live region. */
function visibleText(pattern: RegExp) {
  return screen.queryAllByText(pattern).filter((el) => !el.closest(".sr-only"));
}

let user: ReturnType<typeof userEvent.setup>;

beforeEach(() => {
  vi.useFakeTimers({ now: new Date("2026-10-20T00:00:00Z"), shouldAdvanceTime: true });
  user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  setLocation("/review");
  queueItems = [soon, later];
  soonRound = 1;
  soonReviewFails = false;
  coverLetter = "Dear Acme team, I build reliable backends.";
});
afterEach(() => {
  vi.useRealTimers();
});

// Adjusted by the implementer: contract §4 now requires a dwell before a preview takes a
// decision, so the helper waits it out instead of clicking the instant Yup appears.
async function acmeYup() {
  const button = await screen.findByRole("button", { name: /Yup, submit/ });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(DWELL_MS);
  });
  return button;
}

async function globexPrefetched(calls: ApiCall[]) {
  await waitFor(() => expect(calls.some((c) => c.path === "/runs/run-later/review")).toBe(true));
}

describe("QA: approvals the user did not make", () => {
  // QA-1 (high). Auto-advance puts the next (prefetched) preview under the same key
  // at once; a held-down Y auto-repeats into it. isShortcutBlocked ignores event.repeat.
  it("a held-down y approves only the run the press started on, not the next one", async () => {
    const { calls } = api();
    renderWithClient(<ReviewWorkspace />);
    await acmeYup();
    await globexPrefetched(calls);

    await user.keyboard("y");
    await screen.findByRole("heading", { level: 2, name: /Globex/ });
    act(() => {
      fireEvent.keyDown(document.activeElement ?? document.body, { key: "y", repeat: true });
    });
    await passWindow();

    expect(decisions(calls).map((c) => c.path)).toEqual(["/runs/run-soon/approve"]);
  });

  // QA-1b (high, proposed requirement). Two presses 50 ms apart: the second approves
  // a run whose preview was on screen for 50 ms. Same for a double-click on Yup, whose
  // second click lands on the next run's Yup in the same place.
  it("a second y within the first instant of the next preview does not approve it", async () => {
    const { calls } = api();
    renderWithClient(<ReviewWorkspace />);
    await acmeYup();
    await globexPrefetched(calls);

    await user.keyboard("y");
    await screen.findByRole("heading", { level: 2, name: /Globex/ });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(50);
    });
    await user.keyboard("y");
    await passWindow();

    expect(decisions(calls).map((c) => c.path)).toEqual(["/runs/run-soon/approve"]);
  });

  // QA-2 (high). The full review re-arms on a new round (DecisionBar is keyed by round,
  // so the tick resets). Triage doesn't: a y right after a refetch to round 2 approves
  // the new field the user had no time to read.
  it("a round change under an open preview re-arms it: an immediate y doesn't approve the new round", async () => {
    const { calls, client } = { ...api(), ...renderWithClient(<ReviewWorkspace />) };
    await acmeYup();

    soonRound = 2;
    await act(() => client.refetchQueries({ queryKey: reviewQueryKey("run-soon") }));
    expect(await screen.findByText("Round 2 · new fields", { selector: "section span" })).toBeTruthy();
    expect(screen.getAllByText(/require sponsorship/).length).toBeGreaterThan(0);
    await user.keyboard("y");
    await passWindow();

    expect(decisions(calls)).toEqual([]);
  });

  // QA-3 (high). A refetch error keeps `data`: the body shows only the error, yet
  // canApprove and the y listener still read the old payload.
  it("y does nothing while the preview shows a load error instead of the payload", async () => {
    const { calls, client } = { ...api(), ...renderWithClient(<ReviewWorkspace />) };
    await acmeYup();

    soonReviewFails = true;
    await act(() => client.refetchQueries({ queryKey: reviewQueryKey("run-soon") }));
    expect(await screen.findByText("Couldn't load the preview")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Yup, submit/ })).toBeNull();
    await user.keyboard("y");
    await passWindow();

    expect(decisions(calls)).toEqual([]);
  });
});

describe("QA: drops the user isn't told about", () => {
  // QA-4 (medium). The run left the queue (expired, decided elsewhere): the drop is
  // only announced in the sr-only status, the row is gone, and the undo bar entry just
  // vanishes, exactly like a successful send.
  it("a run that leaves the queue during the window is reported as not sent, visibly", async () => {
    const { calls, client } = { ...api(), ...renderWithClient(<ReviewWorkspace />) };
    await user.click(await acmeYup());

    queueItems = [later];
    await act(() => client.refetchQueries({ queryKey: REVIEW_QUEUE_KEY }));
    await passWindow();

    expect(decisions(calls)).toEqual([]);
    expect(screen.getByRole("status").textContent).toMatch(/Not sent: Acme/); // dropped, but only announced
    expect(visibleText(/not sent|wasn't sent|was not approved/i).length).toBeGreaterThan(0);
  });

  // QA-5 (medium). Client-side navigation inside the window drops every pending
  // decision with no message at all; the app-wide Toaster outlives the page.
  it("leaving the page inside the window says what was not sent", async () => {
    const { calls, unmount } = { ...api(), ...renderWithClient(<ReviewWorkspace />) };
    await user.click(await acmeYup());
    unmount();
    await passWindow();

    expect(decisions(calls)).toEqual([]);
    expect(getToasts().some((t) => /Acme/.test(`${t.title} ${t.body ?? ""}`))).toBe(true);
  });
});

describe("QA: preview completeness", () => {
  // QA-6 (medium). The cover letter is generated text that gets submitted, but the
  // preview clamps it to 4 lines with no way to read the rest (the full review has
  // "Read the whole letter").
  it("the generated cover letter can be read whole from the preview", async () => {
    coverLetter = Array.from({ length: 12 }, (_, i) => `Paragraph ${i + 1}: a claim about my experience.`).join("\n\n");
    api();
    renderWithClient(<ReviewWorkspace />);
    await acmeYup();

    const letter = screen.getByText((_, el) => el?.tagName === "P" && el.textContent === coverLetter);
    const clamped = /line-clamp/.test(letter.className);
    const canExpand = !!screen.queryByRole("button", { name: /whole letter|show all|read more/i });
    expect(clamped && !canExpand).toBe(false);
  });
});

describe("QA: in-flight decisions", () => {
  // QA-7 (low). send() removes the entry from `live` before the POST resolves, so for
  // the request's duration the run counts as undecided again and offers Yup/Nope.
  it("a run whose approval is in flight is not offered for a second decision", async () => {
    const { fetchMock } = api();
    const reply = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((input, init) =>
      String(input).includes("/runs/run-soon/approve") ? new Promise<Response>(() => {}) : reply(input, init)
    );
    renderWithClient(<ReviewWorkspace />);
    await user.click(await acmeYup());
    await passWindow(); // the POST is now in flight, forever

    // Adjusted by the implementer: the router stand-in doesn't navigate, so the click never
    // selected Acme and the Yup found was Globex's. Make it navigate, and check it's Acme.
    router.replace.mockImplementation((url: string) => {
      const [path, query] = url.split("?");
      setLocation(path, query ? `?${query}` : "");
    });
    const queue = screen.getByRole("navigation", { name: "Applications awaiting review" });
    await user.click(within(queue).getByText("Acme"));
    expect(await screen.findByRole("heading", { level: 2, name: /Acme/ })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Yup, submit/ })).toBeNull();
  });

  // QA-8 (low). A network error after the POST left the browser is an unknown outcome,
  // not "Not sent": the server may have recorded the approval.
  it("a network error on the decision is not labelled 'Not sent'", async () => {
    const { fetchMock } = api();
    const reply = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((input, init) =>
      String(input).includes("/runs/run-soon/approve") ? Promise.reject(new TypeError("network")) : reply(input, init)
    );
    renderWithClient(<ReviewWorkspace />);
    await user.click(await acmeYup());
    await passWindow();

    const queue = screen.getByRole("navigation", { name: "Applications awaiting review" });
    const acme = within(queue).getAllByRole("listitem")[0];
    await waitFor(() => expect(within(acme).queryByText(/Approving/)).toBeNull());
    expect(within(acme).queryByText("Not sent")).toBeNull();
  });
});

// Guards that PASS today. Each closes a mutation the existing suite let survive
// (see the QA report): removing the guard named in the test makes it fail.
describe("QA guards (pass today)", () => {
  // Kills: preview treats new_fields as ordinary answers (a basis-backed new field vanished).
  it("a round-2 preview lists every new field as New, even one with a stored basis", async () => {
    soonRound = 2;
    api();
    renderWithClient(<ReviewWorkspace />);
    await acmeYup();

    const flagged = screen.getByRole("list", { name: "Answers to check" });
    const visa = within(flagged).getByText(/require sponsorship/).closest("li")!;
    expect(within(visa).getByText("New")).toBeTruthy();
  });

  // Kills: send() without its own pendingDropReason check (the cache subscription alone
  // misses a change that fired no event, e.g. a decision made from a stale closure).
  it("the send-time check drops a decision whose round no longer matches the cache, without a cache event", async () => {
    const { calls, client } = { ...api(), ...renderWithClient(<ReviewWorkspace />) };
    await user.click(await acmeYup());

    const query = client.getQueryCache().find({ queryKey: reviewQueryKey("run-soon") })!;
    query.state.data = { ...(query.state.data as ReviewPayload), review_round: 2 }; // silent: no listeners run
    await passWindow();

    expect(decisions(calls)).toEqual([]);
    expect(screen.getByRole("status").textContent).toMatch(/Not sent: Acme/);
  });

  // Kills: the y listener ignoring canApprove (relying on the send-time row check alone).
  it("y queues nothing for a run the list doesn't allow to be approved", async () => {
    queueItems = [{ ...soon, actions: ["reject", "cancel"] }, later];
    const { calls } = api();
    renderWithClient(<ReviewWorkspace />);
    await screen.findByRole("button", { name: /Nope/ });

    await user.keyboard("y");
    expect(screen.getByRole("status").textContent).toBe("");
    await passWindow();
    expect(decisions(calls)).toEqual([]);
  });

  // Kills: isShortcutBlocked without its dialog check.
  it("y inside the open screenshot lightbox does nothing", async () => {
    const { calls } = api();
    renderWithClient(<ReviewWorkspace />);
    await acmeYup();

    await user.click(screen.getByRole("button", { name: "Open the screenshot full size" }));
    const dialog = await screen.findByRole("dialog");
    act(() => {
      fireEvent.keyDown(dialog, { key: "y" });
    });
    expect(screen.getByRole("status", { hidden: true }).textContent).toBe(""); // aria-modal hides the page
    await passWindow();
    expect(decisions(calls)).toEqual([]);
  });
});

// Second pass, after the fixes (contract §4 now has the dwell, error-free gating,
// visible drops and the unknown outcome). Guards pass; R2-* reproductions fail.
describe("QA pass 2", () => {
  const status = () => screen.getByRole("status").textContent ?? "";
  const wait = (ms: number) =>
    act(async () => {
      await vi.advanceTimersByTimeAsync(ms);
    });
  const hide = (state: "hidden" | "visible") => {
    vi.spyOn(document, "visibilityState", "get").mockReturnValue(state);
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
  };

  // Guard: QA-1 now passes through the dwell alone; this isolates "a held key never decides".
  it("a key held past the next preview's dwell still doesn't decide it", async () => {
    const { calls } = api();
    renderWithClient(<ReviewWorkspace />);
    await acmeYup();
    await globexPrefetched(calls);
    await user.keyboard("y");
    await screen.findByRole("heading", { level: 2, name: /Globex/ });
    await wait(DWELL_MS * 2);

    act(() => {
      fireEvent.keyDown(document.activeElement ?? document.body, { key: "y", repeat: true });
    });
    expect(status()).not.toMatch(/Approving Globex/);
  });

  // Guard: the dwell doesn't carry over a manual switch (j) to an already-loaded preview.
  it("j to a prefetched run restarts the dwell", async () => {
    const { calls } = api();
    router.replace.mockImplementation((url: string) => {
      const [path, query] = url.split("?");
      setLocation(path, query ? `?${query}` : "");
    });
    renderWithClient(<ReviewWorkspace />);
    await acmeYup();
    await globexPrefetched(calls);

    await user.keyboard("j");
    await screen.findByRole("heading", { level: 2, name: /Globex/ });
    await user.keyboard("y");
    expect(status()).not.toMatch(/Approving/);
    await wait(DWELL_MS);
    await user.keyboard("y");
    expect(status()).toMatch(/Approving Globex/);
  });

  // Guard: recovering from a refetch error with identical data (same object, thanks to
  // structural sharing) still re-arms through a fresh dwell.
  it("after a load error clears, the preview re-arms only after a fresh dwell", async () => {
    const { client } = { ...api(), ...renderWithClient(<ReviewWorkspace />) };
    await acmeYup();
    soonReviewFails = true;
    await act(() => client.refetchQueries({ queryKey: reviewQueryKey("run-soon") }));
    await screen.findByText("Couldn't load the preview");

    soonReviewFails = false;
    await act(() => client.refetchQueries({ queryKey: reviewQueryKey("run-soon") }));
    await screen.findByRole("button", { name: /Yup, submit/ });
    await user.keyboard("y");
    expect(status()).not.toMatch(/Approving/);
    await wait(DWELL_MS);
    await user.keyboard("y");
    expect(status()).toMatch(/Approving Acme/);
  });

  // Guard for the implementer's claim: a key handled by the listener of the previous
  // render (round 1, armed) after the cache moved to round 2 is queued with round 1,
  // then dropped by the send-time check, visibly.
  it("a y caught by the round-1 listener after a refetch to round 2 is dropped and reported", async () => {
    const live = new Set<EventListenerOrEventListenerObject>();
    const add = window.addEventListener.bind(window);
    const remove = window.removeEventListener.bind(window);
    vi.spyOn(window, "addEventListener").mockImplementation((type: string, fn: EventListenerOrEventListenerObject, o?: boolean | AddEventListenerOptions) => {
      if (type === "keydown") live.add(fn);
      add(type, fn, o);
    });
    vi.spyOn(window, "removeEventListener").mockImplementation((type: string, fn: EventListenerOrEventListenerObject, o?: boolean | EventListenerOptions) => {
      if (type === "keydown") live.delete(fn);
      remove(type, fn, o);
    });
    const { calls, client } = { ...api(), ...renderWithClient(<ReviewWorkspace />) };
    await acmeYup();
    const before = new Set(live);

    soonRound = 2;
    await act(() => client.refetchQueries({ queryKey: reviewQueryKey("run-soon") }));
    await screen.findByText("Round 2 · new fields", { selector: "section span" });
    const stale = [...before].filter((fn) => !live.has(fn));
    expect(stale).toHaveLength(1); // the preview's round-1 listener, now replaced
    act(() => {
      const fn = stale[0];
      const event = new KeyboardEvent("keydown", { key: "y" });
      if (typeof fn === "function") fn(event);
      else fn.handleEvent(event);
    });
    await passWindow();

    expect(decisions(calls)).toEqual([]);
    expect(visibleText(/Not sent: Acme/).length).toBeGreaterThan(0);
  });

  // R2-1 (medium). The dwell counts time the tab was hidden. Approve Acme, switch tabs at
  // once (Acme is dropped, as it should be); Globex's preview mounts and "dwells" in the
  // background; on return the first y, meant to redo Acme, approves Globex, never seen.
  it("time with the tab hidden doesn't count toward the dwell", async () => {
    const { calls } = api();
    router.replace.mockImplementation((url: string) => {
      const [path, query] = url.split("?");
      setLocation(path, query ? `?${query}` : "");
    });
    renderWithClient(<ReviewWorkspace />);
    await acmeYup();
    await globexPrefetched(calls);
    await user.keyboard("y");
    await screen.findByRole("heading", { level: 2, name: /Globex/ });
    hide("hidden");
    await wait(DWELL_MS * 3);
    hide("visible");

    await user.keyboard("y");
    await passWindow();
    expect(decisions(calls).map((c) => c.path)).toEqual([]);
  });

  // R2-2 (medium). A request already in flight when the page unmounts: its failure is
  // reported to nobody (setState on an unmounted hook). The user left believing it was
  // approved; the run stays in review and can expire.
  it("an in-flight decision that fails after the page unmounts is still reported", async () => {
    const { fetchMock } = api();
    const reply = fetchMock.getMockImplementation()!;
    let respond!: (r: Response) => void;
    fetchMock.mockImplementation((input, init) =>
      String(input).includes("/runs/run-soon/approve")
        ? new Promise<Response>((resolve) => (respond = resolve))
        : reply(input, init)
    );
    const { unmount } = renderWithClient(<ReviewWorkspace />);
    await user.click(await acmeYup());
    await passWindow(); // sending
    unmount();
    await act(async () => {
      respond(new Response(JSON.stringify({ detail: { code: "unavailable", message: "down" } }), { status: 503 }));
      await vi.advanceTimersByTimeAsync(10);
    });

    const acme = getToasts().filter((t) => /Acme/.test(`${t.title} ${t.body ?? ""}`));
    expect(acme).toHaveLength(1); // once, and as unknown (a 503), pointing at the run
    expect(acme[0].title).toMatch(/Couldn't confirm: Acme/);
    expect(acme[0].error?.runId).toBe("run-soon");
  });

  // Guard: "o" on a run whose quick approval is in flight doesn't open a second way to
  // approve it (the sending bar has no Open button; the key still works).
  it("the full review of a run with a decision in flight doesn't offer another one", async () => {
    const { fetchMock } = api();
    const reply = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((input, init) =>
      String(input).includes("/runs/run-soon/approve") ? new Promise<Response>(() => {}) : reply(input, init)
    );
    renderWithClient(<ReviewWorkspace />);
    await user.click(await acmeYup());
    await passWindow();
    router.replace.mockImplementation((url: string) => {
      const [path, query] = url.split("?");
      setLocation(path, query ? `?${query}` : "");
    });
    const queue = screen.getByRole("navigation", { name: "Applications awaiting review" });
    await user.click(within(queue).getByText("Acme"));
    await screen.findByRole("heading", { level: 2, name: /Acme/ });
    await user.keyboard("o"); // the sending bar has no button; o still opens it

    expect(await screen.findByRole("button", { name: /Back to the quick preview/ })).toBeTruthy();
    await screen.findByRole("heading", { level: 2, name: /Acme/ });
    expect(screen.queryByRole("checkbox", { name: /submit this application/ })).toBeNull();
  });
});

// Third pass: the visibility epoch and reporting after unmount.
describe("QA pass 3", () => {
  const status = () => screen.getByRole("status").textContent ?? "";
  const wait = (ms: number) =>
    act(async () => {
      await vi.advanceTimersByTimeAsync(ms);
    });
  const setVisibility = (state: "hidden" | "visible") => {
    vi.spyOn(document, "visibilityState", "get").mockReturnValue(state);
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
  };
  const navigate = () =>
    router.replace.mockImplementation((url: string) => {
      const [path, query] = url.split("?");
      setLocation(path, query ? `?${query}` : "");
    });
  /** A send that stays in flight until the test answers it. */
  function holdApprove(fetchMock: ReturnType<typeof api>["fetchMock"]) {
    const reply = fetchMock.getMockImplementation()!;
    const answer: { respond?: (r: Response) => void } = {};
    fetchMock.mockImplementation((input, init) =>
      String(input).includes("/runs/run-soon/approve")
        ? new Promise<Response>((resolve) => (answer.respond = resolve))
        : reply(input, init)
    );
    return answer;
  }

  // Guard: a quick hide/show burst on an armed preview disarms it; only a full dwell
  // after the last show re-arms (no timer from an earlier epoch survives).
  it("rapid hide/show re-arms only after a full dwell from the last show", async () => {
    api();
    renderWithClient(<ReviewWorkspace />);
    await acmeYup(); // armed
    for (let i = 0; i < 3; i++) {
      setVisibility("hidden");
      await wait(DWELL_MS / 2);
      setVisibility("visible");
      await wait(DWELL_MS / 2 - 50);
    }
    await user.keyboard("y");
    expect(status()).not.toMatch(/Approving/);
    await wait(DWELL_MS);
    await user.keyboard("y");
    expect(status()).toMatch(/Approving Acme/);
  });

  // Guard: StrictMode (Next dev) runs effects mount, unmount, mount. The mounted ref and
  // the unmount cleanup must not treat that as leaving: the outcome lands in the page.
  it("under StrictMode a sent approval is shown in the page, not only as a toast", async () => {
    api();
    renderWithClient(
      <StrictMode>
        <ReviewWorkspace />
      </StrictMode>
    );
    await user.click(await acmeYup());
    await passWindow();
    const queue = screen.getByRole("navigation", { name: "Applications awaiting review" });
    await waitFor(() => expect(within(queue).getByText("Approved")).toBeTruthy());
  });

  // R3-1 (low). Remounting /review while a send is in flight: the new page doesn't know
  // the send exists, so it offers a second approval of the same run; the old instance
  // then toasts "Approved" while the new one may report a 409 "Not sent" for the same run.
  it("a remounted queue doesn't offer a run whose approval is still in flight", async () => {
    const { fetchMock } = api();
    const answer = holdApprove(fetchMock);
    navigate();
    const { client, rerender } = renderWithClient(<ReviewWorkspace key="a" />);
    await user.click(await acmeYup());
    await passWindow(); // sending
    setLocation("/review", "?run=run-soon"); // come back straight to Acme
    rerender(
      <QueryClientProvider client={client}>
        <ReviewWorkspace key="b" />
        <Toaster />
      </QueryClientProvider>
    );
    await screen.findByRole("heading", { level: 2, name: /Acme/ });
    await wait(DWELL_MS);
    const offered = screen.queryByRole("button", { name: /Yup, submit/ });
    answer.respond?.(new Response(JSON.stringify({ success: true, message: "ok" }), { status: 200 }));
    expect(offered).toBeNull();
  });

  // Guard: one outcome, one report. A success after unmount is one toast, no "Not sent".
  it("a success after unmount is reported once, as approved", async () => {
    const { fetchMock } = api();
    const answer = holdApprove(fetchMock);
    const { unmount } = renderWithClient(<ReviewWorkspace />);
    await user.click(await acmeYup());
    await passWindow();
    unmount();
    await act(async () => {
      answer.respond?.(new Response(JSON.stringify({ success: true, message: "ok" }), { status: 200 }));
      await vi.advanceTimersByTimeAsync(10);
    });
    const acme = getToasts().filter((t) => /Acme/.test(`${t.title} ${t.body ?? ""}`));
    expect(acme.map((t) => [t.kind, t.title])).toEqual([["success", "Approved: Acme"]]);
  });
});
