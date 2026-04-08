export interface ApplicationListItem {
  run_id: string;
  job_url: string;
  company: string | null;
  title: string | null;
  location: string | null;
  company_website: string | null;
  ats_type: string;
  status: string;
  current_state: string | null;
  match_score: number | null;
  is_test_run: boolean;
  error_message: string | null;
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
  pending: number;
  running: number;
  completed: number;
  failed: number;
  duplicate: number;
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

export interface RunDetail {
  id: string;
  job_url: string;
  status: string;
  current_state: string | null;
  temporal_workflow_id: string | null;
  account_email: string | null;
  error_message: string | null;
  match_score: number | null;
  match_rationale: string | null;
  is_test_run: boolean;
  tracking_url: string | null;
  confirmation_html: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  job_posting: JobPostingInfo | null;
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

export interface EnqueueJobResponse {
  run_id: string;
  status: string;
  job_url: string;
}

export type ApplicationStatus =
  | "PENDING"
  | "RUNNING"
  | "COMPLETED"
  | "FAILED"
  | "DUPLICATE";

export interface FieldMapping {
  form_label: string;
  mapped_to: string;
  value: string;
  confidence: number;
}
