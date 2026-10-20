import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ApiErrorMessage } from "@/components/api-error-message";
import { OutcomeBadge } from "@/components/outcome-badge";
import { ApiError } from "@/lib/api";
import { FAILURE_REASON_INFO, failureReasonInfo } from "@/lib/failure-reasons";
import { OUTCOME_INFO } from "@/lib/outcomes";
import { OUTCOMES } from "@/lib/types";

describe("OutcomeBadge", () => {
  it.each(OUTCOMES)("labels %s distinctly, with its meaning as the tooltip", (outcome) => {
    render(<OutcomeBadge outcome={outcome} />);
    const badge = screen.getByText(OUTCOME_INFO[outcome].label);
    expect(badge.getAttribute("title")).toBe(OUTCOME_INFO[outcome].description);
  });

  it("never reuses a label, so test runs and uncertain runs can't pass for submissions", () => {
    const labels = OUTCOMES.map((o) => OUTCOME_INFO[o].label);
    expect(new Set(labels).size).toBe(labels.length);
    expect(OUTCOME_INFO.test_only.label).not.toMatch(/submitted/i);
    expect(OUTCOME_INFO.submit_uncertain.label).toBe("May be submitted");
  });
});

describe("failure reasons", () => {
  it("gives every reason a human title and explanation", () => {
    for (const info of Object.values(FAILURE_REASON_INFO)) {
      expect(info.title).toMatch(/^[A-Z]/);
      expect(info.detail.length).toBeGreaterThan(10);
    }
  });

  it("still reads an unknown reason as words", () => {
    expect(failureReasonInfo("some_new_reason")?.title).toBe("Some new reason");
    expect(failureReasonInfo(null)).toBeNull();
  });
});

describe("ApiErrorMessage", () => {
  it.each([
    ["already_applied", "See the earlier application"],
    ["run_in_flight", "See the active run"],
  ])("links %s to the run in detail.run_id", (code, label) => {
    render(<ApiErrorMessage error={new ApiError(409, { code, message: "Blocked.", run_id: "run-9" })} />);
    expect(screen.getByRole("link", { name: label }).getAttribute("href")).toBe("/applications/run-9");
    expect(screen.getByText(code)).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain("Blocked.");
  });

  it("has no run link when the error names no run", () => {
    render(<ApiErrorMessage error={new ApiError(400, { code: "invalid_url", message: "Not a job URL." })} />);
    expect(screen.queryByRole("link")).toBeNull();
  });
});
