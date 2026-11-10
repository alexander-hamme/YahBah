import type {
  ApplicationListItem,
  FillEvidenceRow,
  ReviewPayload,
  RunDetail,
} from "@/lib/types";

// Contract §8 shapes with neutral defaults; tests override what they assert on.

const T0 = "2026-10-19T12:00:00Z";

export function makeRun(overrides: Partial<RunDetail> = {}): RunDetail {
  return {
    id: "00000000-0000-0000-0000-000000000001",
    job_url: "https://boards.example/jobs/1",
    status: "COMPLETED",
    outcome: "submitted",
    actions: ["delete"],
    failure_reason: null,
    submission_mode: "review",
    origin: "manual",
    submit_attempted_at: null,
    review_deadline: null,
    retry_of_run_id: null,
    current_state: null,
    temporal_workflow_id: null,
    account_email: null,
    error_message: null,
    match_score: null,
    match_rationale: null,
    tracking_url: null,
    confirmation_html: null,
    email_confirmed_at: null,
    created_at: T0,
    updated_at: T0,
    completed_at: T0,
    job_posting: null,
    ...overrides,
  };
}

export function makeListItem(overrides: Partial<ApplicationListItem> = {}): ApplicationListItem {
  return {
    run_id: "00000000-0000-0000-0000-0000000000b1",
    job_url: "https://boards.example/jobs/1",
    company: "Acme",
    title: "Backend Engineer",
    location: null,
    company_website: null,
    ats_type: "greenhouse",
    status: "AWAITING_REVIEW",
    outcome: "awaiting_review",
    actions: ["approve", "reject", "cancel"],
    failure_reason: null,
    submission_mode: "review",
    origin: "manual",
    submit_attempted_at: null,
    review_deadline: "2026-10-21T12:00:00Z",
    retry_of_run_id: null,
    current_state: "REVIEW",
    review_round: 1,
    match_score: 80,
    error_message: null,
    email_confirmed_at: null,
    created_at: T0,
    updated_at: T0,
    completed_at: null,
    ...overrides,
  };
}

export function makeRow(overrides: Partial<FillEvidenceRow> = {}): FillEvidenceRow {
  return {
    key: "email",
    label: "Email",
    type: "text",
    required: true,
    options: [],
    answer: "applicant@example.com",
    source: "autofill",
    basis: ["profile.email"],
    generated: false,
    written: "applicant@example.com",
    page_value: "applicant@example.com",
    ok: true,
    ...overrides,
  };
}

export function makeReview(overrides: Partial<ReviewPayload> = {}): ReviewPayload {
  return {
    run_id: "00000000-0000-0000-0000-0000000000b1",
    status: "AWAITING_REVIEW",
    submission_mode: "review",
    review_round: 1,
    review_deadline: "2026-10-21T12:00:00Z",
    job: { title: "Backend Engineer", company: "Acme", url: "https://boards.example/jobs/1" },
    answers: [makeRow()],
    new_fields: [],
    screenshot_artifact_id: "00000000-0000-0000-0000-0000000000c1",
    cover_letter: null,
    ...overrides,
  };
}
