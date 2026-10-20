import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApplicationList } from "@/components/application-list";
import { AttentionStrip } from "@/components/attention-strip";
import { makeListItem } from "@/test/fixtures";
import { mockApi, renderWithClient, SETTINGS_REVIEW } from "@/test/render";

describe("ApplicationList", () => {
  it("renders the server's outcome, not the raw status", () => {
    mockApi({ "GET /settings": { body: SETTINGS_REVIEW } });
    const testRun = makeListItem({ run_id: "t1", status: "COMPLETED", outcome: "test_only", actions: ["retry", "delete"] });
    renderWithClient(<ApplicationList items={[testRun]} sort="created_at_desc" onSort={() => {}} />);

    const row = screen.getAllByRole("row")[1];
    expect(within(row).getByText("Test only")).toBeTruthy();
    expect(within(row).queryByText(/^Submitted$/)).toBeNull();
    expect(within(row).getByText("Filled, not submitted")).toBeTruthy();
  });

  it("gives failed runs their reason in words, and uncertain runs a resolve link", () => {
    mockApi({ "GET /settings": { body: SETTINGS_REVIEW } });
    const items = [
      makeListItem({ run_id: "f1", outcome: "failed", status: "FAILED", failure_reason: "login_wall", actions: ["retry", "delete"] }),
      makeListItem({
        run_id: "u1",
        outcome: "submit_uncertain",
        status: "SUBMIT_UNCERTAIN",
        failure_reason: "possibly_submitted",
        actions: ["confirm_submitted", "confirm_not_submitted"],
      }),
    ];
    renderWithClient(<ApplicationList items={items} sort="created_at_desc" onSort={() => {}} />);

    const [, failed, uncertain] = screen.getAllByRole("row");
    expect(within(failed).getByText("Login required")).toBeTruthy();
    expect(within(uncertain).getByText("May be submitted")).toBeTruthy();
    expect(within(uncertain).getByRole("link", { name: "Resolve" })).toBeTruthy();
  });

  it("lets only deletable runs be selected for bulk delete", async () => {
    mockApi({ "GET /settings": { body: SETTINGS_REVIEW } });
    const onToggleAll = vi.fn();
    const items = [
      makeListItem({ run_id: "d1", outcome: "failed", actions: ["retry", "delete"] }),
      makeListItem({ run_id: "u1", outcome: "submit_uncertain", actions: ["confirm_submitted", "confirm_not_submitted"] }),
    ];
    renderWithClient(
      <ApplicationList
        items={items}
        sort="created_at_desc"
        onSort={() => {}}
        selectedIds={new Set()}
        onToggleSelect={() => {}}
        onToggleAll={onToggleAll}
      />
    );

    expect(screen.getAllByRole("checkbox", { name: /for deletion/ })).toHaveLength(1);
    await userEvent.click(screen.getByRole("checkbox", { name: /Select all deletable/ }));
    expect(onToggleAll).toHaveBeenCalledWith(["d1"], true);
  });
});

describe("AttentionStrip", () => {
  it("makes uncertain runs and the review queue impossible to miss", async () => {
    const counts = Object.fromEntries(
      ["queued", "in_progress", "awaiting_review", "test_only", "email_verified", "submitted", "submit_uncertain",
        "failed", "cancelled", "expired", "duplicate"].map((o) => [o, 0])
    );
    mockApi({
      "GET /applications/stats": { body: { counts: { ...counts, submit_uncertain: 2, awaiting_review: 3 }, total: 5 } },
      "GET /applications": { body: { items: [makeListItem()], total: 1, page: 1, per_page: 100 } },
    });
    renderWithClient(<AttentionStrip />);

    const uncertain = await screen.findByRole("link", { name: /2 applications may have been submitted/ });
    expect(uncertain.getAttribute("href")).toBe("/applications?filter=submit_uncertain");
    expect(screen.getByRole("link", { name: /3 waiting for your review/ }).getAttribute("href")).toBe("/review");
  });
});
