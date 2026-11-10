import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { UnansweredQuestions } from "@/components/unanswered-questions";
import type { Artifact, UnresolvedQuestion } from "@/lib/types";
import { mockApi, renderWithClient } from "@/test/render";

const RUN = "run-1";
const HEADING = "Questions YahBah couldn't answer";
const FAILED = "Couldn't load the unanswered questions.";

function plan(id: string, created_at: string, stage = "map_answers"): Artifact {
  return {
    id,
    artifact_type: "answer_plan",
    path: `artifacts/${RUN}/answer_plan_${stage}_1-1.json`,
    metadata: { stage, review_round: null },
    created_at,
  };
}

const download = (a: Artifact) => `GET /runs/${RUN}/artifacts/${a.id}/download`;

function question(over: Partial<UnresolvedQuestion>): UnresolvedQuestion {
  return {
    key: "q",
    label: "Question",
    type: "radio",
    required: true,
    status: "missing_fact",
    reason: "No stored fact covers this.",
    requested_info: "",
    fact_template: "",
    ...over,
  };
}

const TEMPLATE = "- id: work_authorization_de\n  kind: work_authorization_de\n  value: ''\n  description: Whether you may work in Germany\n  jurisdiction: DE\n";

const UNRESOLVED: UnresolvedQuestion[] = [
  question({
    key: "auth_de",
    label: "Are you authorized to work in Germany?",
    status: "missing_fact",
    reason: "No stored fact covers work authorization in Germany.",
    requested_info: "Whether you may work in Germany",
    fact_template: TEMPLATE,
  }),
  question({
    key: "team",
    label: "Which team?",
    status: "needs_clarification",
    reason: "The question doesn't say which team it means.",
  }),
  question({ key: "eeo", label: "Ethnicity", status: "unsupported", reason: "The cited fact is about another attribute." }),
  question({ key: "start", label: "Start date", type: "date", status: "invalid_value", reason: "A date must come from a stored date." }),
  question({ key: "hobby", label: "Favourite hobby", required: false, reason: "Optional and unanswered." }),
];

const body = (unresolved: UnresolvedQuestion[]) => ({
  stage: "map_answers",
  review_round: null,
  fields: [],
  unresolved,
  placeholders: [],
  calls: [],
});

describe("UnansweredQuestions", () => {
  it("lists each required question with its status, reason, what's needed and a copyable fact template", async () => {
    const a = plan("p1", "2026-10-20T10:00:00Z");
    mockApi({ [download(a)]: { body: body(UNRESOLVED) } });
    renderWithClient(<UnansweredQuestions artifacts={[a]} runId={RUN} />);

    expect(await screen.findByText("Are you authorized to work in Germany?")).toBeTruthy();
    expect(screen.getByRole("heading", { name: HEADING })).toBeTruthy();
    expect(screen.getByText("No stored fact")).toBeTruthy();
    expect(screen.getByText("Unclear question")).toBeTruthy();
    expect(screen.getByText("Couldn't be backed by your facts")).toBeTruthy();
    expect(screen.getByText("No valid answer for this field")).toBeTruthy();
    expect(screen.getByText("No stored fact covers work authorization in Germany.")).toBeTruthy();
    expect(screen.getByText("Needed: Whether you may work in Germany")).toBeTruthy();
    expect(screen.getByLabelText("Fact template for Are you authorized to work in Germany?").textContent).toBe(TEMPLATE);
    expect(screen.getAllByLabelText(/^Fact template for/)).toHaveLength(1); // only rows that have one
    expect(screen.getByText(/then retry\.$/).textContent).toBe(
      "Add the facts to the facts: list in config/personal.yaml, then retry."
    );
    expect(screen.queryByText("Favourite hobby")).toBeNull(); // optional: left blank, doesn't block

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Copy fact template for Are you authorized to work in Germany?" }));
    expect(await navigator.clipboard.readText()).toBe(TEMPLATE);
  });

  it("reads the newest answer_plan", async () => {
    const older = plan("p1", "2026-10-20T10:00:00Z");
    const newer = plan("p2", "2026-10-20T10:05:00Z", "fill");
    const { calls } = mockApi({
      [download(older)]: { body: body([question({ label: "Old question" })]) },
      [download(newer)]: { body: body([question({ label: "New question" })]) },
    });
    renderWithClient(<UnansweredQuestions artifacts={[newer, older]} runId={RUN} />);

    expect(await screen.findByText("New question")).toBeTruthy();
    expect(screen.queryByText("Old question")).toBeNull();
    expect(calls.map((c) => c.path)).toEqual([`/runs/${RUN}/artifacts/p2/download`]);
  });

  it("says it couldn't load when the download fails", async () => {
    const a = plan("p1", "2026-10-20T10:00:00Z");
    mockApi({ [download(a)]: { status: 500, body: { detail: "boom" } } });
    renderWithClient(<UnansweredQuestions artifacts={[a]} runId={RUN} />);

    expect(await screen.findByText(FAILED)).toBeTruthy();
  });

  it("says it couldn't load when the run has no answer_plan", () => {
    const { calls } = mockApi({});
    renderWithClient(<UnansweredQuestions artifacts={[]} runId={RUN} />);

    expect(screen.getByText(FAILED)).toBeTruthy();
    expect(calls).toHaveLength(0);
  });

  it("shows a skeleton, not a failure, while the artifact list loads", () => {
    mockApi({});
    renderWithClient(<UnansweredQuestions artifacts={undefined} runId={RUN} />);

    expect(screen.getByRole("heading", { name: HEADING }).closest("section")?.getAttribute("aria-busy")).toBe("true");
    expect(screen.queryByText(FAILED)).toBeNull();
  });

  it("renders nothing when no required question is unresolved", async () => {
    const a = plan("p1", "2026-10-20T10:00:00Z");
    const { calls } = mockApi({ [download(a)]: { body: body([question({ required: false })]) } });
    renderWithClient(<UnansweredQuestions artifacts={[a]} runId={RUN} />);

    await waitFor(() => expect(calls).toHaveLength(1));
    await waitFor(() => expect(screen.queryByRole("heading", { name: HEADING })).toBeNull());
    expect(screen.queryByText(FAILED)).toBeNull();
  });
});
