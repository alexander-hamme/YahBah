import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ReviewPanel } from "@/components/review/review-panel";
import { makeReview, makeRow } from "@/test/fixtures";
import { mockApi, renderWithClient } from "@/test/render";
import type { ReviewPayload } from "@/lib/types";

const RUN = "00000000-0000-0000-0000-0000000000b1";
const REVIEW_PATH = `GET /runs/${RUN}/review`;

const answers = [
  makeRow(),
  makeRow({
    key: "auth",
    label: "Are you authorized to work in the US?",
    answer: "Yes",
    source: "known_answers.work_authorization",
    basis: ["known_answers.work_authorization"],
    written: "Yes",
    page_value: "No",
    ok: false,
  }),
  makeRow({
    key: "fruit",
    label: "What kind of fruit are you?",
    required: false,
    answer: "A pineapple: tough outside, sweet once you get through.",
    source: "custom",
    basis: [],
    generated: true,
    written: "A pineapple: tough outside, sweet once you get through.",
    page_value: "A pineapple: tough outside, sweet once you get through.",
  }),
];

function review(overrides: Partial<ReviewPayload> = {}): ReviewPayload {
  return makeReview({
    answers,
    cover_letter: { text: "Dear Acme team, I build reliable backends.", artifact_id: "00000000-0000-0000-0000-0000000000d1" },
    ...overrides,
  });
}

beforeEach(() => {
  vi.useFakeTimers({ now: new Date("2026-10-20T00:00:00Z"), toFake: ["Date"] });
});
afterEach(() => {
  vi.useRealTimers();
});

describe("ReviewPanel evidence", () => {
  it("shows each answer with its basis, the value read back from the page, and the ok flag", async () => {
    mockApi({ [REVIEW_PATH]: { body: review() } });
    renderWithClient(<ReviewPanel runId={RUN} actions={["approve", "reject", "cancel"]} />);

    const list = await screen.findByRole("list", { name: "Answers" });
    const rows = within(list).getAllByRole("listitem");
    expect(rows).toHaveLength(3);

    // basis + matching read-back
    expect(within(rows[0]).getAllByText("profile.email").length).toBeGreaterThan(0);
    expect(within(rows[0]).getByText(/Read back from the page, matches/)).toBeTruthy();
    expect(within(rows[0]).getAllByText("applicant@example.com")).toHaveLength(2); // answer + page value

    // ok = false: the page value is shown and flagged
    expect(within(rows[1]).getByText("known_answers.work_authorization")).toBeTruthy();
    expect(within(rows[1]).getByText(/Differs on the page/)).toBeTruthy();
    expect(within(rows[1]).getByText("No")).toBeTruthy();
    expect(rows[1].dataset.flagged).toBe("true");

    // model-written prose is labelled, and has no stored basis by design
    expect(within(rows[2]).getByText("Generated")).toBeTruthy();
    expect(within(rows[2]).queryByText("No stored basis")).toBeNull();
  });

  it("summarises what to check and can narrow the list to it", async () => {
    mockApi({ [REVIEW_PATH]: { body: review() } });
    renderWithClient(<ReviewPanel runId={RUN} actions={["approve", "reject"]} />);

    expect(await screen.findByText("3 answers")).toBeTruthy();
    expect(screen.getByText(/differ on the page/).textContent).toBe("· 1 differ on the page");
    await userEvent.click(screen.getByRole("button", { name: "Only the 2 to check" }));
    expect(within(screen.getByRole("list", { name: "Answers" })).getAllByRole("listitem")).toHaveLength(2);
  });

  it("flags a factual answer without a stored basis", async () => {
    mockApi({ [REVIEW_PATH]: { body: review({ answers: [makeRow({ basis: [], source: "custom" })] }) } });
    renderWithClient(<ReviewPanel runId={RUN} actions={["approve"]} />);
    expect(await screen.findByText("No stored basis")).toBeTruthy();
  });

  it("flags a required field left blank, not an optional one nobody mapped", async () => {
    const rows = [
      makeRow({ key: "phone", label: "Phone", required: true, answer: null, source: null, basis: [], written: null, page_value: "" }),
      makeRow({ key: "blog", label: "Blog", required: false, answer: null, source: null, basis: [], written: null, page_value: "" }),
      makeRow({ key: "cv", label: "Resume", source: "resume", answer: "resume.pdf", basis: [], page_value: "resume.pdf" }),
    ];
    mockApi({ [REVIEW_PATH]: { body: review({ answers: rows }) } });
    renderWithClient(<ReviewPanel runId={RUN} actions={["approve"]} />);

    const [phone, blog, cv] = within(await screen.findByRole("list", { name: "Answers" })).getAllByRole("listitem");
    expect(within(phone).getByText("Required, blank")).toBeTruthy();
    expect(blog.dataset.flagged).toBeUndefined();
    expect(within(cv).getByText("Résumé file: resume.pdf")).toBeTruthy();
    expect(cv.dataset.flagged).toBeUndefined();
  });

  it("highlights new_fields added in a later round, separately from the approved answers", async () => {
    // new_fields aren't filled yet: written, page_value and ok arrive null
    const visa = makeRow({ key: "visa", label: "Visa type", answer: "H-1B", basis: ["profile.visa"], written: null,
                           page_value: null, ok: null as unknown as boolean });
    mockApi({ [REVIEW_PATH]: { body: review({ review_round: 2, answers: [...answers, visa], new_fields: [visa] }) } });
    renderWithClient(<ReviewPanel runId={RUN} actions={["approve", "reject"]} />);

    expect(await screen.findByText("Round 2")).toBeTruthy();
    const added = screen.getByRole("list", { name: "New fields" });
    expect(within(added).getByText("Visa type")).toBeTruthy();
    expect(within(added).getByText("New")).toBeTruthy();
    expect(within(added).getByText("Filled after you approve")).toBeTruthy();
    expect(within(added).queryByText(/Differs on the page/)).toBeNull();
    // not repeated among the previously approved answers
    expect(within(screen.getByRole("list", { name: "Previously approved answers" })).queryByText("Visa type")).toBeNull();
  });

  it("shows the full-page screenshot, the cover letter and the deadline", async () => {
    mockApi({ [REVIEW_PATH]: { body: review() } });
    renderWithClient(<ReviewPanel runId={RUN} actions={["approve", "reject"]} />);

    const shot = (await screen.findByAltText(/Screenshot of the filled form, full page/)) as HTMLImageElement;
    expect(shot.getAttribute("src")).toBe(`/api/runs/${RUN}/artifacts/00000000-0000-0000-0000-0000000000c1/download`);
    expect(screen.getByText("Dear Acme team, I build reliable backends.")).toBeTruthy();
    expect(screen.getByText(/36h 0m left/)).toBeTruthy();
  });
});

describe("ReviewPanel decision", () => {
  it("approves only after the box is ticked, sending the round the user saw", async () => {
    const { calls } = mockApi({
      [REVIEW_PATH]: { body: review({ review_round: 2 }) },
      [`POST /runs/${RUN}/approve`]: { body: { success: true, message: "ok" } },
    });
    const onDecided = vi.fn();
    renderWithClient(<ReviewPanel runId={RUN} actions={["approve", "reject"]} onDecided={onDecided} />);

    const approve = await screen.findByRole("button", { name: "Approve and submit" });
    expect((approve as HTMLButtonElement).disabled).toBe(true);
    await userEvent.click(screen.getByRole("checkbox", { name: /submit this application to Acme/ }));
    await userEvent.click(approve);

    await waitFor(() => expect(onDecided).toHaveBeenCalledWith(RUN, "approved"));
    expect(calls.find((c) => c.method === "POST")).toEqual({
      method: "POST",
      path: `/runs/${RUN}/approve`,
      body: { review_round: 2 },
    });
  });

  it("on 409 stale_review_round, refreshes the review, explains, and asks again", async () => {
    let round = 1;
    const { calls } = mockApi({
      [REVIEW_PATH]: () => ({
        body: review({ review_round: round, new_fields: round > 1 ? [makeRow({ key: "visa", label: "Visa type" })] : [] }),
      }),
      [`POST /runs/${RUN}/approve`]: (call) => {
        if ((call.body as { review_round: number }).review_round === 2) return { body: { success: true, message: "ok" } };
        round = 2; // the workflow moved on while the user was looking
        return {
          status: 409,
          body: { detail: { code: "stale_review_round", message: "Review round 1 is no longer current." } },
        };
      },
    });
    renderWithClient(<ReviewPanel runId={RUN} actions={["approve", "reject"]} />);

    await userEvent.click(await screen.findByRole("checkbox", { name: /submit this application/ }));
    await userEvent.click(screen.getByRole("button", { name: "Approve and submit" }));

    expect(await screen.findByText(/it's now round 2/)).toBeTruthy();
    expect(await screen.findByRole("list", { name: "New fields" })).toBeTruthy();
    // the tick doesn't carry over to a round the user hasn't seen
    const box = screen.getByRole("checkbox", { name: /submit this application/ }) as HTMLInputElement;
    expect(box.checked).toBe(false);

    await userEvent.click(box);
    await userEvent.click(screen.getByRole("button", { name: "Approve and submit" }));
    await waitFor(() =>
      expect(calls.filter((c) => c.method === "POST").map((c) => c.body)).toEqual([
        { review_round: 1 },
        { review_round: 2 },
      ])
    );
  });

  it("rejects with an optional reason and the round", async () => {
    const { calls } = mockApi({
      [REVIEW_PATH]: { body: review() },
      [`POST /runs/${RUN}/reject`]: { body: { success: true, message: "ok" } },
    });
    renderWithClient(<ReviewPanel runId={RUN} actions={["approve", "reject"]} />);

    await userEvent.click(await screen.findByRole("button", { name: "Reject…" }));
    await userEvent.type(screen.getByLabelText(/Why reject it/), "salary too low");
    await userEvent.click(screen.getByRole("button", { name: "Reject application" }));

    expect(await screen.findByText(/Rejected. Nothing was submitted/)).toBeTruthy();
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ review_round: 1, reason: "salary too low" });
  });

  it("shows the §8 error inline when a decision fails for another reason", async () => {
    mockApi({
      [REVIEW_PATH]: { body: review() },
      [`POST /runs/${RUN}/approve`]: {
        status: 503,
        body: { detail: { code: "temporal_unavailable", message: "Temporal is not reachable." } },
      },
    });
    renderWithClient(<ReviewPanel runId={RUN} actions={["approve", "reject"]} />);

    await userEvent.click(await screen.findByRole("checkbox", { name: /submit this application/ }));
    await userEvent.click(screen.getByRole("button", { name: "Approve and submit" }));
    expect(await screen.findByText("Temporal is not reachable.")).toBeTruthy();
    expect(screen.getByText("temporal_unavailable")).toBeTruthy();
  });

  it("offers no decision the run's actions don't list", async () => {
    mockApi({ [REVIEW_PATH]: { body: review({ status: "EXPIRED" }) } });
    renderWithClient(<ReviewPanel runId={RUN} actions={["retry", "delete"]} />);

    expect(await screen.findByText(/Read only/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Approve and submit" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Reject…" })).toBeNull();
  });
});
