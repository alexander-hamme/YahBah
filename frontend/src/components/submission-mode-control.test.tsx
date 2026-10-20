import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { MODE_OPTIONS, SubmissionModeControl } from "@/components/submission-mode-control";
import { mockApi, renderWithClient, SETTINGS_REVIEW } from "@/test/render";

describe("SubmissionModeControl", () => {
  it("names the current mode for new runs", async () => {
    mockApi({ "GET /settings": { body: SETTINGS_REVIEW } });
    renderWithClient(<SubmissionModeControl />);

    const group = await screen.findByRole("radiogroup", { name: "New runs" });
    expect(group).toBeTruthy();
    await waitFor(() => expect((screen.getByRole("radio", { name: "Review" }) as HTMLInputElement).checked).toBe(true));
    expect((screen.getByRole("radio", { name: "Testing" }) as HTMLInputElement).checked).toBe(false);
    expect(screen.getByText(/Changing it affects new runs only/)).toBeTruthy();
  });

  it("offers only the modes that work today: testing and review", () => {
    expect(MODE_OPTIONS.map((o) => o.value)).toEqual(["testing", "review"]);
  });

  it("writes the chosen mode and says started runs keep theirs", async () => {
    const { calls } = mockApi({
      "GET /settings": { body: SETTINGS_REVIEW },
      "PUT /settings/submission_mode": { body: { ...SETTINGS_REVIEW, submission_mode: "testing" } },
    });
    renderWithClient(<SubmissionModeControl />);

    await waitFor(() => expect((screen.getByRole("radio", { name: "Review" }) as HTMLInputElement).checked).toBe(true));
    await userEvent.click(screen.getByRole("radio", { name: "Testing" }));

    await waitFor(() => expect((screen.getByRole("radio", { name: "Testing" }) as HTMLInputElement).checked).toBe(true));
    expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ value: "testing" });
    expect(await screen.findByText("New runs: Testing")).toBeTruthy();
    expect(screen.getByText(/keep the mode they started with/)).toBeTruthy();
  });

  it("shows the server's rejection and keeps the effective mode", async () => {
    mockApi({
      "GET /settings": { body: SETTINGS_REVIEW },
      "PUT /settings/submission_mode": {
        status: 422,
        body: { detail: { code: "invalid_setting", message: "submission_mode must be testing or review." } },
      },
    });
    renderWithClient(<SubmissionModeControl />);

    await waitFor(() => expect((screen.getByRole("radio", { name: "Review" }) as HTMLInputElement).checked).toBe(true));
    await userEvent.click(screen.getByRole("radio", { name: "Testing" }));

    expect(await screen.findByText("submission_mode must be testing or review.")).toBeTruthy();
    expect(screen.getByText("invalid_setting")).toBeTruthy();
    expect((screen.getByRole("radio", { name: "Review" }) as HTMLInputElement).checked).toBe(true);
  });

  it("says the mode is unknown, never live, when settings can't load", async () => {
    mockApi({ "GET /settings": { status: 500, body: { detail: "boom" } } });
    renderWithClient(<SubmissionModeControl />);

    expect(await screen.findByRole("button", { name: /Mode unknown/ })).toBeTruthy();
    expect(screen.queryByRole("radio")).toBeNull();
    expect(screen.queryByText(/live/i)).toBeNull();
  });
});
