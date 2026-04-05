import type {
  ApplicationListResponse,
  ApplicationStats,
  Artifact,
  EnqueueJobResponse,
  RunDetail,
  Step,
} from "./types";

const BASE = "/api";

async function fetchJSON<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`${res.status}: ${body}`);
  }
  return res.json();
}

export interface ApplicationsParams {
  page?: number;
  per_page?: number;
  status?: string;
  search?: string;
  sort?: string;
}

export function getApplications(
  params: ApplicationsParams = {}
): Promise<ApplicationListResponse> {
  const qs = new URLSearchParams();
  if (params.page) qs.set("page", String(params.page));
  if (params.per_page) qs.set("per_page", String(params.per_page));
  if (params.status) qs.set("status", params.status);
  if (params.search) qs.set("search", params.search);
  if (params.sort) qs.set("sort", params.sort);
  return fetchJSON(`${BASE}/applications?${qs}`);
}

export function getApplicationStats(): Promise<ApplicationStats> {
  return fetchJSON(`${BASE}/applications/stats`);
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

export function submitJob(jobUrl: string): Promise<EnqueueJobResponse> {
  return fetchJSON(`${BASE}/jobs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ job_url: jobUrl }),
  });
}

export function deleteRun(id: string): Promise<{ success: boolean; message: string }> {
  return fetchJSON(`${BASE}/runs/${id}`, { method: "DELETE" });
}

export function cancelRun(id: string): Promise<{ success: boolean; message: string }> {
  return fetchJSON(`${BASE}/runs/${id}/cancel`, { method: "POST" });
}

export function retryRun(
  id: string
): Promise<{ success: boolean; message: string; run_id: string }> {
  return fetchJSON(`${BASE}/runs/${id}/retry`, { method: "POST" });
}

export function getRuntimeSettings(): Promise<{ testing_mode: boolean }> {
  return fetchJSON(`${BASE}/settings`);
}

export function setTestingMode(enabled: boolean): Promise<{ testing_mode: boolean }> {
  return fetchJSON(`${BASE}/settings/testing_mode?value=${enabled}`, {
    method: "PUT",
  });
}

export function artifactDownloadUrl(
  runId: string,
  artifactId: string
): string {
  return `${BASE}/runs/${runId}/artifacts/${artifactId}/download`;
}
