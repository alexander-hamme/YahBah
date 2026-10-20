import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RunInfoCard } from "@/components/run-info-card";
import { UntrustedHtmlFrame } from "@/components/untrusted-html-frame";
import { untrustedHtmlSrcDoc } from "@/lib/untrusted-html";
import type { RunDetail } from "@/lib/types";

const PAYLOAD =
  '<p>Application received</p><img src=x onerror="window.__x=1"><script>window.__x=2</script>';

function expectSandboxedFrame(frame: HTMLIFrameElement, html: string) {
  // Present but empty: every restriction on, no allow-* tokens.
  expect(frame.hasAttribute("sandbox")).toBe(true);
  expect(frame.getAttribute("sandbox")).toBe("");
  expect(frame.getAttribute("srcdoc")).toBe(untrustedHtmlSrcDoc(html));
  expect(frame.getAttribute("referrerpolicy")).toBe("no-referrer");
}

function expectPayloadNotInMainDocument() {
  expect(document.querySelectorAll("img, script")).toHaveLength(0);
  expect(document.body.textContent).not.toContain("Application received");
  expect((window as { __x?: unknown }).__x).toBeUndefined();
}

describe("UntrustedHtmlFrame", () => {
  it("renders the HTML only inside an empty-sandbox iframe", () => {
    render(<UntrustedHtmlFrame html={PAYLOAD} title="Captured page" />);
    expectSandboxedFrame(screen.getByTitle("Captured page") as HTMLIFrameElement, PAYLOAD);
    expectPayloadNotInMainDocument();
  });
});

describe("RunInfoCard confirmation", () => {
  const run: RunDetail = {
    id: "00000000-0000-0000-0000-000000000001",
    job_url: "https://boards.example/jobs/1",
    status: "COMPLETED",
    current_state: null,
    temporal_workflow_id: null,
    account_email: null,
    error_message: null,
    match_score: null,
    match_rationale: null,
    is_test_run: false,
    tracking_url: null,
    confirmation_html: PAYLOAD,
    submit_attempted_at: null,
    email_confirmed_at: null,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
    completed_at: "2026-10-01T00:00:00Z",
    job_posting: null,
  };

  it("never injects confirmation_html into the dashboard DOM", () => {
    render(<RunInfoCard run={run} statusUpdates={[]} />);
    expectPayloadNotInMainDocument();
    expectSandboxedFrame(
      screen.getByTitle("Confirmation page captured from the job site") as HTMLIFrameElement,
      PAYLOAD
    );
  });
});
