import type {
  ActionResponse,
  ApiErrorDetail,
  ApplicationListResponse,
  ApplicationStats,
  Artifact,
  EnqueueJobResponse,
  Outcome,
  ReviewPayload,
  RunDetail,
  RuntimeSettings,
  ScheduledJobItem,
  ScheduledJobListResponse,
  ScheduledStats,
  StatusUpdate,
  Step,
  SubmissionMode,
} from "./types";

const BASE = "/api";

/**
 * A failed API call. `code`, `message` and `runId` come from the §8 error body
 * (`{"detail": {"code", "message", "run_id"?}}`); other bodies (FastAPI's own
 * validation errors, a proxy error page, no response at all) get a generic code.
 */
export class ApiError extends Error {
  readonly status: number; // 0: no response
  readonly code: string;
  readonly runId: string | null;

  constructor(status: number, detail: ApiErrorDetail) {
    super(detail.message);
    this.name = "ApiError";
    this.status = status;
    this.code = detail.code;
    this.runId = detail.run_id ?? null;
  }
}

function isErrorDetail(value: unknown): value is ApiErrorDetail {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as ApiErrorDetail).code === "string" &&
    typeof (value as ApiErrorDetail).message === "string"
  );
}

function genericMessage(status: number): string {
  if (status === 404) return "Not found.";
  if (status >= 500) return `The YahBah API failed (HTTP ${status}).`;
  return `The request was rejected (HTTP ${status}).`;
}

export async function errorFromResponse(res: Response): Promise<ApiError> {
  const body: unknown = await res.json().catch(() => null);
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (isErrorDetail(detail)) return new ApiError(res.status, detail);
  const message = typeof detail === "string" && detail ? detail : genericMessage(res.status);
  return new ApiError(res.status, { code: `http_${res.status}`, message });
}

async function fetchJSON<T>(url: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, init);
  } catch {
    throw new ApiError(0, {
      code: "network_error",
      message: "Can't reach the YahBah API. Is it running?",
    });
  }
  if (!res.ok) throw await errorFromResponse(res);
  return res.json();
}

function postJSON<T>(url: string, body?: unknown): Promise<T> {
  return fetchJSON(url, {
    method: "POST",
    ...(body === undefined
      ? {}
      : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  });
}

// ── Applications ─────────────────────────────────────────────────────────

export interface ApplicationsParams {
  page?: number;
  per_page?: number;
  /** Any of these outcomes; empty or omitted means all. */
  outcome?: readonly Outcome[];
  search?: string;
  sort?: string;
}

export function applicationsQuery(params: ApplicationsParams): string {
  const qs = new URLSearchParams();
  if (params.page) qs.set("page", String(params.page));
  if (params.per_page) qs.set("per_page", String(params.per_page));
  for (const o of params.outcome ?? []) qs.append("outcome", o);
  if (params.search) qs.set("search", params.search);
  if (params.sort) qs.set("sort", params.sort);
  return qs.toString();
}

export function getApplications(
  params: ApplicationsParams = {}
): Promise<ApplicationListResponse> {
  return fetchJSON(`${BASE}/applications?${applicationsQuery(params)}`);
}

export function getApplicationStats(): Promise<ApplicationStats> {
  return fetchJSON(`${BASE}/applications/stats`);
}

// ── Runs ─────────────────────────────────────────────────────────────────

export interface BulkDeleteParams {
  run_ids?: string[];
  status?: string;
}

/** Deletes only runs whose actions include `delete`; the rest are counted in `skipped`. */
export function bulkDeleteRuns(
  params: BulkDeleteParams
): Promise<{ deleted: number; skipped: number; success: boolean }> {
  return postJSON(`${BASE}/runs/bulk-delete`, params);
}

export function getRunDetail(id: string): Promise<RunDetail> {
  return fetchJSON(`${BASE}/runs/${id}`);
}

export function getRunSteps(id: string): Promise<Step[]> {
  return fetchJSON(`${BASE}/runs/${id}/steps`);
}

export function getRunArtifacts(id: string): Promise<Artifact[]> {
  return fetchJSON(`${BASE}/runs/${id}/artifacts`);
}

export function getRunStatusUpdates(id: string): Promise<StatusUpdate[]> {
  return fetchJSON(`${BASE}/runs/${id}/status-updates`);
}

export function getRunReview(id: string): Promise<ReviewPayload> {
  return fetchJSON(`${BASE}/runs/${id}/review`);
}

export function submitJob(jobUrl: string): Promise<EnqueueJobResponse> {
  return postJSON(`${BASE}/jobs`, { job_url: jobUrl });
}

export function deleteRun(id: string): Promise<ActionResponse> {
  return fetchJSON(`${BASE}/runs/${id}`, { method: "DELETE" });
}

export function cancelRun(id: string): Promise<ActionResponse> {
  return postJSON(`${BASE}/runs/${id}/cancel`);
}

/** Retry creates a new run; the response's `run_id` is the new run. */
export function retryRun(id: string): Promise<ActionResponse & { run_id: string }> {
  return postJSON(`${BASE}/runs/${id}/retry`);
}

/** Approves exactly the review round the user saw; a newer round answers 409 stale_review_round. */
export function approveRun(id: string, reviewRound: number): Promise<ActionResponse> {
  return postJSON(`${BASE}/runs/${id}/approve`, { review_round: reviewRound });
}

export function rejectRun(
  id: string,
  reviewRound: number,
  reason: string | null
): Promise<ActionResponse> {
  return postJSON(`${BASE}/runs/${id}/reject`, { review_round: reviewRound, reason });
}

export function confirmSubmitted(id: string): Promise<ActionResponse> {
  return postJSON(`${BASE}/runs/${id}/confirm-submitted`);
}

/** Marks the run FAILED and deletes its submission claim: applying to the job is possible again. */
export function confirmNotSubmitted(id: string): Promise<ActionResponse> {
  return postJSON(`${BASE}/runs/${id}/confirm-not-submitted`);
}

export function artifactDownloadUrl(runId: string, artifactId: string): string {
  return `${BASE}/runs/${runId}/artifacts/${artifactId}/download`;
}

// ── Settings ─────────────────────────────────────────────────────────────

export function getRuntimeSettings(): Promise<RuntimeSettings> {
  return fetchJSON(`${BASE}/settings`);
}

export function setSubmissionMode(mode: SubmissionMode): Promise<RuntimeSettings> {
  return fetchJSON(`${BASE}/settings/submission_mode`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ value: mode }),
  });
}

// ── Scheduled queue ──────────────────────────────────────────────────────

export interface ScheduledParams {
  page?: number;
  per_page?: number;
  status?: string;
  search?: string;
  sort?: string;
}

export function getScheduledJobs(
  params: ScheduledParams = {}
): Promise<ScheduledJobListResponse> {
  const qs = new URLSearchParams();
  if (params.page) qs.set("page", String(params.page));
  if (params.per_page) qs.set("per_page", String(params.per_page));
  if (params.status) qs.set("status", params.status);
  if (params.search) qs.set("search", params.search);
  if (params.sort) qs.set("sort", params.sort);
  return fetchJSON(`${BASE}/scheduled?${qs}`);
}

export function getScheduledStats(): Promise<ScheduledStats> {
  return fetchJSON(`${BASE}/scheduled/stats`);
}

export function addScheduledJob(jobUrl: string): Promise<ScheduledJobItem> {
  return postJSON(`${BASE}/scheduled`, { job_url: jobUrl });
}

export function approveScheduledJob(id: string): Promise<ActionResponse> {
  return postJSON(`${BASE}/scheduled/${id}/approve`);
}

export function rejectScheduledJob(id: string): Promise<ActionResponse> {
  return postJSON(`${BASE}/scheduled/${id}/reject`);
}

export function toggleHoldScheduledJob(id: string): Promise<ActionResponse> {
  return postJSON(`${BASE}/scheduled/${id}/hold`);
}

export function promoteScheduledJob(id: string): Promise<ActionResponse> {
  return postJSON(`${BASE}/scheduled/${id}/promote`);
}

export function deleteScheduledJob(id: string): Promise<ActionResponse> {
  return fetchJSON(`${BASE}/scheduled/${id}`, { method: "DELETE" });
}

export function triggerAlertBackfill(
  since: string
): Promise<{ success: boolean; processed: number; skipped: number }> {
  return postJSON(`${BASE}/gmail/alert-backfill`, { since });
}
