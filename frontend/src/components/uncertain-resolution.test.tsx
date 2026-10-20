import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { UncertainResolution } from "@/components/uncertain-resolution";
import { makeRun } from "@/test/fixtures";
import { mockApi, renderWithClient } from "@/test/render";

const uncertain = makeRun({
  status: "SUBMIT_UNCERTAIN",
  outcome: "submit_uncertain",
  actions: ["confirm_submitted", "confirm_not_submitted"],
  failure_reason: "submit_unverified",
  submit_attempted_at: "2026-10-19T14:02:00Z",
});

describe("UncertainResolution", () => {
  it("explains why and offers both resolutions", () => {
    renderWithClient(<UncertainResolution run={uncertain} />);

    expect(screen.getByRole("heading", { name: "This application may have been submitted" })).toBeTruthy();
    expect(screen.getByText(/Submit not verified/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "It was submitted" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "It wasn't submitted" })).toBeTruthy();
    expect(screen.getByText(/re-enables applying to this job/)).toBeTruthy();
  });

  it("confirms 'not submitted' with the consequence before calling the API", async () => {
    const { calls } = mockApi({
      [`POST /runs/${uncertain.id}/confirm-not-submitted`]: { body: { success: true, message: "ok" } },
    });
    renderWithClient(<UncertainResolution run={uncertain} />);

    await userEvent.click(screen.getByRole("button", { name: "It wasn't submitted" }));
    expect(calls).toHaveLength(0);
    expect(screen.getByText(/This re-enables applying to this job/)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: /Confirm: it wasn't submitted/ }));

    await waitFor(() =>
      expect(calls.map((c) => `${c.method} ${c.path}`)).toContain(`POST /runs/${uncertain.id}/confirm-not-submitted`)
    );
    expect(await screen.findByText(/You can apply to this job again/)).toBeTruthy();
  });

  it("shows only the resolutions the run lists", () => {
    const { container } = renderWithClient(<UncertainResolution run={makeRun({ actions: ["delete"] })} />);
    expect(container.textContent).toBe("");
  });
});
