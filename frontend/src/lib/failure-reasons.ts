import type { FailureReason } from "./types";

// failure_reason in human terms (contract §2). The text says what happened,
// not whether anything was sent: the same reason can end a run FAILED (nothing
// sent) or SUBMIT_UNCERTAIN (maybe sent), and the outcome says which.

export interface FailureReasonInfo {
  title: string;
  detail: string;
}

export const FAILURE_REASON_INFO: Record<FailureReason, FailureReasonInfo> = {
  form_not_found: {
    title: "No application form found",
    detail: "YahBah couldn't find an application form on the job page.",
  },
  form_unmapped: {
    title: "Couldn't read the form",
    detail: "The form's fields couldn't be mapped to questions YahBah understands.",
  },
  form_changed: {
    title: "The form changed",
    detail: "The form was different when YahBah came back to submit the approved answers.",
  },
  job_closed: {
    title: "Job closed",
    detail: "The job board says this posting is gone or no longer accepts applications. Nothing to do: move on.",
  },
  login_wall: {
    title: "Login required",
    detail: "The site asks you to sign in or create an account first.",
  },
  captcha: {
    title: "CAPTCHA",
    detail: "The site asked for human verification.",
  },
  blocked: {
    title: "Blocked by the site's bot protection",
    detail:
      "The site's security check refused automated access; this says nothing about the job, which may still be open. Open it yourself, use the company's direct application link (for example its Greenhouse or Lever page), or retry later.",
  },
  needs_answer: {
    title: "Needs an answer from you",
    detail:
      "Some required questions couldn't be answered from your stored facts. The list below says what's missing: add those facts to config/personal.yaml, then retry.",
  },
  fill_failed: {
    title: "Couldn't fill the form",
    detail: "Some fields couldn't be filled, or didn't keep their values.",
  },
  possibly_submitted: {
    title: "Page changed during filling",
    detail: "The page navigated or the form disappeared while an agent was filling it.",
  },
  submit_button_not_found: {
    title: "No submit button",
    detail: "The form was filled, but YahBah couldn't find its submit button.",
  },
  claim_denied: {
    title: "Submission blocked",
    detail: "Another run had already claimed this job, so YahBah didn't click submit.",
  },
  email_code_entry: {
    title: "Verification code failed",
    detail: "The site asked for an emailed code, and entering it didn't work.",
  },
  email_code_timeout: {
    title: "No verification code",
    detail: "The site asked for an emailed code that never arrived.",
  },
  submit_unverified: {
    title: "Submit not verified",
    detail: "Submit was clicked, but YahBah couldn't confirm the site accepted it.",
  },
  browser_error: {
    title: "Browser error",
    detail: "The browser crashed, or the site didn't load (server or network error).",
  },
  llm_error: {
    title: "Model error",
    detail: "A call to a local model failed.",
  },
  timeout: {
    title: "Timed out",
    detail: "A stage took longer than its time limit.",
  },
  superseded: {
    title: "Superseded",
    detail: "A newer attempt took over this run.",
  },
  internal_error: {
    title: "Internal error",
    detail: "Something unexpected went wrong inside YahBah.",
  },
  cancelled: {
    title: "Cancelled",
    detail: "You cancelled this run.",
  },
  review_rejected: {
    title: "Rejected in review",
    detail: "You rejected this application in review.",
  },
  review_expired: {
    title: "Review window ran out",
    detail: "Nobody approved it before the review deadline.",
  },
  duplicate: {
    title: "Already applied",
    detail: "Another run has applied, or may have applied, to this job.",
  },
};

function humanize(code: string): string {
  const words = code.replace(/_/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** Known reasons get their copy; an unknown one (a newer server) still reads as words. */
export function failureReasonInfo(reason: string | null | undefined): FailureReasonInfo | null {
  if (!reason) return null;
  return (
    FAILURE_REASON_INFO[reason as FailureReason] ?? { title: humanize(reason), detail: "" }
  );
}
