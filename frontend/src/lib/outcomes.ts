import { OUTCOMES, type Outcome } from "./types";

// Presentation of the server's `outcome` (contract §8). Wording only: which
// outcome a run has, and what it may do, always comes from the API.

export type OutcomeTone =
  | "active"
  | "attention"
  | "uncertain"
  | "success"
  | "test"
  | "failure"
  | "neutral";

export interface OutcomeInfo {
  label: string;
  /** One sentence on what the outcome means for this application. */
  description: string;
  tone: OutcomeTone;
}

export const OUTCOME_INFO: Record<Outcome, OutcomeInfo> = {
  queued: {
    label: "Queued",
    description: "Waiting for a free run slot.",
    tone: "neutral",
  },
  in_progress: {
    label: "In progress",
    description: "YahBah is working on the application.",
    tone: "active",
  },
  awaiting_review: {
    label: "Awaiting review",
    description: "Filled and waiting for your approval. Nothing has been submitted.",
    tone: "attention",
  },
  test_only: {
    label: "Test only",
    description: "Filled in testing mode. Never submitted.",
    tone: "test",
  },
  email_verified: {
    label: "Email verified",
    description: "Submitted, and the employer's confirmation email arrived.",
    tone: "success",
  },
  submitted: {
    label: "Submitted",
    description: "Submitted and verified on the page.",
    tone: "success",
  },
  submit_uncertain: {
    label: "May be submitted",
    description:
      "Submit was attempted but not verified. It may have been sent; tell YahBah what happened.",
    tone: "uncertain",
  },
  failed: {
    label: "Failed",
    description: "Stopped before anything was submitted.",
    tone: "failure",
  },
  cancelled: {
    label: "Cancelled",
    description: "Stopped before anything was submitted.",
    tone: "neutral",
  },
  expired: {
    label: "Expired",
    description: "Nobody approved it within the review window. Nothing was submitted.",
    tone: "neutral",
  },
  duplicate: {
    label: "Duplicate",
    description: "Not started: YahBah has already applied, or may have applied, to this job.",
    tone: "neutral",
  },
};

export const OUTCOME_TONE_CLASS: Record<OutcomeTone, string> = {
  active: "bg-cyan-500/15 text-cyan-300 border-cyan-500/30",
  attention: "bg-amber-500/15 text-amber-300 border-amber-500/35",
  uncertain: "bg-orange-500/20 text-orange-200 border-orange-400/50",
  success: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  test: "bg-violet-500/15 text-violet-300 border-violet-500/30",
  failure: "bg-red-500/15 text-red-300 border-red-500/30",
  neutral: "bg-slate-500/15 text-slate-300 border-slate-500/30",
};

/** The list filter: one choice in the UI, possibly several outcomes on the wire. */
export interface OutcomeFilter {
  value: string;
  label: string;
  outcomes: readonly Outcome[]; // empty: every outcome
}

export const DEFAULT_FILTER = "hide_failed";

export const OUTCOME_FILTERS: readonly OutcomeFilter[] = [
  { value: "hide_failed", label: "All but failed", outcomes: OUTCOMES.filter((o) => o !== "failed") },
  { value: "all", label: "All outcomes", outcomes: [] },
  { value: "needs_you", label: "Needs you", outcomes: ["awaiting_review", "submit_uncertain"] },
  { value: "sent", label: "Submitted (any)", outcomes: ["submitted", "email_verified"] },
  ...OUTCOMES.map((o) => ({ value: o, label: OUTCOME_INFO[o].label, outcomes: [o] })),
];

export function filterByValue(value: string | null): OutcomeFilter {
  return (
    OUTCOME_FILTERS.find((f) => f.value === value) ??
    OUTCOME_FILTERS.find((f) => f.value === DEFAULT_FILTER)!
  );
}
