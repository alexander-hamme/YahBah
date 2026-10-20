// Mirrors the API response schemas (docs/wiki/contracts/run-lifecycle.md §8).
// The run-lifecycle unions below must match src/yahbah/run_state.py; a pytest
// (test/yahbah/api/test_frontend_contract.py) fails when they drift.

/** What a run means to the user, derived on the server (derive_outcome). Render this, never `status`. */
export type Outcome =
  | "queued"
  | "in_progress"
  | "awaiting_review"
  | "test_only"
  | "email_verified"
  | "submitted"
  | "submit_uncertain"
  | "failed"
  | "cancelled"
  | "expired"
  | "duplicate";

export const OUTCOMES: readonly Outcome[] = [
  "queued",
  "in_progress",
  "awaiting_review",
  "test_only",
  "email_verified",
  "submitted",
  "submit_uncertain",
  "failed",
  "cancelled",
  "expired",
  "duplicate",
];

/** What the user may do with a run, from allowed_actions(). The UI shows exactly these. */
export type RunAction =
  | "cancel"
  | "retry"
  | "approve"
  | "reject"
  | "confirm_submitted"
  | "confirm_not_submitted"
  | "delete";

export type FailureReason =
  | "form_not_found"
  | "form_unmapped"
  | "form_changed"
  | "job_closed"
  | "login_wall"
  | "captcha"
  | "blocked"
  | "needs_answer"
  | "fill_failed"
  | "possibly_submitted"
  | "submit_button_not_found"
  | "claim_denied"
  | "email_code_entry"
  | "email_code_timeout"
  | "submit_unverified"
  | "browser_error"
  | "llm_error"
  | "timeout"
  | "superseded"
  | "internal_error"
  | "cancelled"
  | "review_rejected"
  | "review_expired"
  | "duplicate";

/** Batch 2 adds "auto_reviewed" (the reviewer-model pass). */
export type SubmissionMode = "testing" | "review";

export type Origin = "manual" | "discovered";

/** Run-lifecycle fields carried by both list rows and run detail. */
export interface RunLifecycle {
  status: string; // raw, for dev-mode display only
  outcome: Outcome;
  actions: RunAction[];
  failure_reason: FailureReason | null;
  submission_mode: SubmissionMode | null; // null: a run from before the contract
  origin: Origin | null;
  submit_attempted_at: string | null;
  review_deadline: string | null;
  retry_of_run_id: string | null;
}

export interface ApplicationListItem extends RunLifecycle {
  run_id: string;
  job_url: string;
  company: string | null;
  title: string | null;
  location: string | null;
  company_website: string | null;
  ats_type: string;
  current_state: string | null;
  review_round: number | null;
  match_score: number | null;
  error_message: string | null;
  email_confirmed_at: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}

export interface ApplicationListResponse {
  items: ApplicationListItem[];
  total: number;
  page: number;
  per_page: number;
}

export interface ApplicationStats {
  counts: Record<Outcome, number>;
  total: number;
}

export interface JobPostingInfo {
  title: string | null;
  company: string | null;
  location: string | null;
  salary_min: number | null;
  salary_max: number | null;
  company_website: string | null;
  industry: string | null;
  company_description: string | null;
  work_model: string | null;
  seniority_level: string | null;
  experience_required: string | null;
  posted_date: string | null;
  technologies: string[] | null;
  specialties: string[] | null;
  ats_type: string;
}

export interface RunDetail extends RunLifecycle {
  id: string;
  job_url: string;
  current_state: string | null;
  temporal_workflow_id: string | null;
  account_email: string | null;
  error_message: string | null;
  match_score: number | null;
  match_rationale: string | null;
  tracking_url: string | null;
  confirmation_html: string | null;
  email_confirmed_at: string | null;
  review_round?: number | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  job_posting: JobPostingInfo | null;
}

/** One row of the `submitted_values` fill evidence (§8): what was intended, typed, and read back. */
export interface FillEvidenceRow {
  key: string;
  label: string;
  type: string;
  required: boolean;
  options: string[];
  answer: string | null;
  /** known-answer key | profile key | resume | cover_letter | custom | skip; null: not mapped */
  source: string | null;
  /** Stored keys / profile fields backing the answer. */
  basis: string[];
  /** Model-written prose (open-ended questions), not a stored fact. */
  generated: boolean;
  written: string | null;
  page_value: string | null;
  ok: boolean;
}

export interface ReviewPayload {
  run_id: string;
  status: string;
  submission_mode: SubmissionMode | null;
  review_round: number;
  review_deadline: string | null;
  job: { title: string | null; company: string | null; url: string };
  answers: FillEvidenceRow[];
  new_fields: FillEvidenceRow[];
  screenshot_artifact_id: string | null;
  cover_letter: { text: string | null; artifact_id: string | null } | null;
}

/** Effective runtime settings: the server fills in fail-closed defaults. */
export interface RuntimeSettings {
  submission_mode: SubmissionMode;
  review_window_hours: number;
}

/** The §8 error body's `detail`. */
export interface ApiErrorDetail {
  code: string;
  message: string;
  run_id?: string | null;
}

export interface ActionResponse {
  success: boolean;
  message: string;
  run_id?: string | null;
}

export interface Step {
  id: string;
  step_name: string;
  status: string;
  logs: string | null;
  started_at: string | null;
  completed_at: string | null;
}

export interface Artifact {
  id: string;
  artifact_type: string;
  path: string;
  metadata: Record<string, unknown> | null;
  created_at: string;
}

export interface StatusUpdate {
  id: string;
  status_type: string;
  subject: string | null;
  sender: string | null;
  summary: string | null;
  email_date: string;
  confidence: number;
  created_at: string;
}

export interface EnqueueJobResponse {
  run_id: string;
  status: string;
  job_url: string;
}

export interface ScheduledJobItem {
  id: string;
  job_url: string;
  source: string;
  title: string | null;
  company: string | null;
  location: string | null;
  snippet: string | null;
  match_score: number | null;
  match_rationale: string | null;
  status: string;
  hold: boolean;
  promote_after: string;
  created_at: string;
  updated_at: string;
  promoted_run_id: string | null;
}

export interface ScheduledJobListResponse {
  items: ScheduledJobItem[];
  total: number;
  page: number;
  per_page: number;
}

export interface ScheduledStats {
  scheduled: number;
  approved: number;
  skipped: number;
  promoted: number;
  total: number;
}
