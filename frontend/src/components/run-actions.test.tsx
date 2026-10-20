import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { RunActions } from "@/components/run-actions";
import { router } from "@/test/navigation";
import { mockApi, renderWithClient, SETTINGS_REVIEW } from "@/test/render";

const RUN = "00000000-0000-0000-0000-0000000000e1";

describe("RunActions", () => {
  it("renders exactly the actions the server lists", () => {
    mockApi({ "GET /settings": { body: SETTINGS_REVIEW } });
    renderWithClient(<RunActions runId={RUN} actions={["retry", "delete"]} />);

    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Delete" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();
  });

  it("renders nothing for a run with no actions", () => {
    mockApi({ "GET /settings": { body: SETTINGS_REVIEW } });
    const { container } = renderWithClient(<RunActions runId={RUN} actions={[]} compact />);
    expect(container.querySelector("button, a")).toBeNull();
  });

  it("links a reviewable run to its review instead of approving from the list", () => {
    mockApi({ "GET /settings": { body: SETTINGS_REVIEW } });
    renderWithClient(<RunActions runId={RUN} actions={["approve", "reject", "cancel"]} compact />);

    expect(screen.getByRole("link", { name: "Review" }).getAttribute("href")).toBe(`/review?run=${RUN}`);
    expect(screen.queryByRole("button", { name: /approve/i })).toBeNull();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeTruthy();
  });

  it("links an uncertain run to its resolution panel", () => {
    mockApi({ "GET /settings": { body: SETTINGS_REVIEW } });
    renderWithClient(<RunActions runId={RUN} actions={["confirm_submitted", "confirm_not_submitted"]} compact />);

    expect(screen.getByRole("link", { name: "Resolve" }).getAttribute("href")).toBe(`/applications/${RUN}#resolve`);
    expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
  });

  it("retry confirms with the mode the new run gets, then opens the new run", async () => {
    const { calls } = mockApi({
      "GET /settings": { body: SETTINGS_REVIEW },
      [`POST /runs/${RUN}/retry`]: { body: { success: true, message: "Retry started", run_id: "new-run-id" } },
    });
    renderWithClient(<RunActions runId={RUN} actions={["retry", "delete"]} />);

    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Start a new run in Review mode?")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Start new run" }));

    await waitFor(() => expect(router.push).toHaveBeenCalledWith("/applications/new-run-id"));
    expect(calls.filter((c) => c.method === "POST").map((c) => c.path)).toEqual([`/runs/${RUN}/retry`]);
  });

  it("toasts the §8 error when the server refuses", async () => {
    mockApi({
      "GET /settings": { body: SETTINGS_REVIEW },
      [`POST /runs/${RUN}/cancel`]: {
        status: 409,
        body: { detail: { code: "action_not_allowed", message: "This run can't be cancelled any more." } },
      },
    });
    renderWithClient(<RunActions runId={RUN} actions={["cancel"]} compact />);

    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await userEvent.click(screen.getByRole("button", { name: "Yes, cancel" }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("This run can't be cancelled any more.");
    expect(alert.textContent).toContain("action_not_allowed");
  });
});
