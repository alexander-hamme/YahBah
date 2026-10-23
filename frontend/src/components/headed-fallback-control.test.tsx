import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { HeadedFallbackControl } from "@/components/headed-fallback-control";
import { mockApi, renderWithClient, SETTINGS_REVIEW } from "@/test/render";

const NAME = "Visible-browser fallback for blocked sites";
const toggle = () => screen.getByRole("switch", { name: NAME });

describe("HeadedFallbackControl", () => {
  it("shows the server's value, explains it, and applies to new runs only", async () => {
    mockApi({ "GET /settings": { body: SETTINGS_REVIEW } });
    renderWithClient(<HeadedFallbackControl />);

    await waitFor(() => expect(toggle().getAttribute("aria-checked")).toBe("true"));
    expect(screen.getByText("on")).toBeTruthy();
    // the help is the switch's description, and visible on request
    expect(toggle().getAttribute("aria-describedby")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "About the visible-browser fallback" }));
    const help = screen.getAllByText(/A Chrome window may open on this Mac/);
    expect(help.some((el) => !el.closest(".sr-only"))).toBe(true);
    expect(help[0].textContent).toMatch(/Applies to new runs only/);
  });

  it("writes the flipped value and confirms it for new runs", async () => {
    const { calls } = mockApi({
      "GET /settings": { body: SETTINGS_REVIEW },
      "PUT /settings/browser_headed_fallback": { body: { ...SETTINGS_REVIEW, browser_headed_fallback: false } },
    });
    renderWithClient(<HeadedFallbackControl />);

    await waitFor(() => expect(toggle().getAttribute("aria-checked")).toBe("true"));
    await userEvent.click(toggle());

    await waitFor(() => expect(toggle().getAttribute("aria-checked")).toBe("false"));
    expect(calls.find((c) => c.method === "PUT")).toEqual({
      method: "PUT",
      path: "/settings/browser_headed_fallback",
      body: { value: false },
    });
    expect(await screen.findByText("Visible-browser fallback off for new runs")).toBeTruthy();
  });

  it("keeps the server's value when the write is refused", async () => {
    mockApi({
      "GET /settings": { body: SETTINGS_REVIEW },
      "PUT /settings/browser_headed_fallback": {
        status: 422,
        body: { detail: { code: "invalid_setting", message: "browser_headed_fallback must be true or false." } },
      },
    });
    renderWithClient(<HeadedFallbackControl />);

    await waitFor(() => expect(toggle().getAttribute("aria-checked")).toBe("true"));
    await userEvent.click(toggle());
    expect(await screen.findByText("browser_headed_fallback must be true or false.")).toBeTruthy();
    expect(toggle().getAttribute("aria-checked")).toBe("true");
  });

  it("says unknown, never on or off, when settings can't load", async () => {
    mockApi({ "GET /settings": { status: 500, body: { detail: "boom" } } });
    renderWithClient(<HeadedFallbackControl />);

    expect(await screen.findByRole("button", { name: /Visible browser unknown/ })).toBeTruthy();
    expect(screen.queryByRole("switch")).toBeNull();
  });

  it("says unknown when the server doesn't report the setting at all", async () => {
    mockApi({ "GET /settings": { body: { submission_mode: "review", review_window_hours: 48 } } });
    renderWithClient(<HeadedFallbackControl />);

    expect(await screen.findByRole("button", { name: /Visible browser unknown/ })).toBeTruthy();
    expect(screen.queryByRole("switch")).toBeNull();
  });

  it("shows no value and offers no change while settings load", () => {
    mockApi({});
    renderWithClient(<HeadedFallbackControl />);
    expect(screen.queryByRole("switch")).toBeNull(); // a switch would have to claim on or off
    expect(screen.getByText("Visible browser")).toBeTruthy();
    expect(screen.queryByText("on")).toBeNull();
    expect(screen.queryByText("off")).toBeNull();
  });
});
