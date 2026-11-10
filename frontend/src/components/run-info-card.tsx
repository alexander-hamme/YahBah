"use client";

import Link from "next/link";
import {
  Activity,
  Calendar,
  Clock,
  AlertTriangle,
  Workflow,
  CheckCheck,
  ExternalLink,
  MessageSquareCheck,
  Settings2,
} from "lucide-react";
import { CopyButton } from "@/components/copy-button";
import { OutcomeBadge } from "@/components/outcome-badge";
import { ApplicationStatusPipeline } from "@/components/application-status-pipeline";
import { DeadlineCountdown } from "@/components/deadline-countdown";
import { useDevMode } from "@/components/dev-mode-context";
import { modeLabel } from "@/components/submission-mode-control";
import { UntrustedHtmlFrame } from "@/components/untrusted-html-frame";
import { failureReasonInfo } from "@/lib/failure-reasons";
import { OUTCOME_INFO } from "@/lib/outcomes";
import { timeAgo } from "@/lib/time";
import type { Outcome, RunDetail, StatusUpdate } from "@/lib/types";
import { cn } from "@/lib/utils";

const IN_FLIGHT_OUTCOMES: ReadonlySet<Outcome> = new Set(["queued", "in_progress", "awaiting_review"]);
const SENT_OUTCOMES: ReadonlySet<Outcome> = new Set(["submitted", "email_verified"]);

function ReasonBox({ run }: { run: RunDetail }) {
  const reason = failureReasonInfo(run.failure_reason);
  if (!reason && !run.error_message) return null;
  const failed = run.outcome === "failed";
  return (
    <div
      className={cn(
        "flex gap-3 rounded-lg border p-3",
        failed ? "border-red-500/20 bg-red-500/10" : "border-white/10 bg-white/[0.03]"
      )}
    >
      <AlertTriangle className={cn("mt-0.5 h-4 w-4 shrink-0", failed ? "text-red-400" : "text-muted-foreground")} aria-hidden />
      <div className="min-w-0">
        <p className={cn("text-sm font-medium", failed ? "text-red-300" : "text-foreground")}>
          {reason?.title ?? "Error"}
        </p>
        {reason?.detail && <p className="mt-0.5 text-sm text-foreground/75">{reason.detail}</p>}
        <p className="mt-0.5 text-xs text-muted-foreground">{OUTCOME_INFO[run.outcome].description}</p>
        {run.error_message && (
          <p className={cn("mt-1.5 whitespace-pre-wrap text-xs", failed ? "text-red-300/80" : "text-muted-foreground")}>
            {run.error_message}
          </p>
        )}
      </div>
    </div>
  );
}

function formatDateTime(iso: string | null): string {
  if (!iso) return "-";
  return new Date(iso).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
  });
}

function formatStep(step: string | null): string {
  if (!step) return "-";
  return step.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function Field({
  icon,
  label,
  children,
}: {
  icon: React.ReactNode;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-0.5">
      <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground uppercase tracking-wider">
        {icon}
        {label}
      </div>
      <div className="text-sm font-medium">{children}</div>
    </div>
  );
}

export function RunInfoCard({
  run,
  statusUpdates,
}: {
  run: RunDetail;
  statusUpdates: StatusUpdate[];
}) {
  const { devMode } = useDevMode();
  const submitted = SENT_OUTCOMES.has(run.outcome);
  const showPipeline = submitted || statusUpdates.length > 0;

  const isInProgress = run.outcome === "queued" || run.outcome === "in_progress";
  const isDone = !IN_FLIGHT_OUTCOMES.has(run.outcome);

  return (
    <div className="glass-panel rounded-xl">
      <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-white/10 to-transparent rounded-t-xl" />
      <div className="px-5 pt-4 pb-2">
        <h3
          className="text-sm font-semibold text-foreground"
          style={{ fontFamily: "var(--font-heading)" }}
        >
          Application Info
        </h3>
      </div>
      <div className="px-5 pb-5 space-y-5">
        {/* Single row: status fields + pipeline + track button */}
        <div className="flex items-center gap-8 flex-wrap">
          <Field icon={<Activity className="h-3 w-3" />} label="Outcome">
            <OutcomeBadge outcome={run.outcome} />
          </Field>

          {run.outcome === "awaiting_review" && run.review_deadline && (
            <Field icon={<Clock className="h-3 w-3" />} label="Review deadline">
              <DeadlineCountdown deadline={run.review_deadline} className="text-sm" />
            </Field>
          )}

          <Field icon={<Settings2 className="h-3 w-3" />} label="Mode">
            <span className="text-sm">
              {run.submission_mode ? modeLabel(run.submission_mode) : "Legacy run"}
              {run.origin && (
                <span className="text-muted-foreground"> · {run.origin === "discovered" ? "from a job alert" : "added by you"}</span>
              )}
            </span>
          </Field>

          {isInProgress && !devMode && (
            <Field icon={<CheckCheck className="h-3 w-3" />} label="Current Step">
              {formatStep(run.current_state)}
            </Field>
          )}

          {isDone && !devMode && (
            <Field icon={<Clock className="h-3 w-3" />} label={submitted ? "Submitted" : "Ended"}>
              <span title={formatDateTime(run.completed_at)}>
                {run.completed_at ? timeAgo(run.completed_at) : "-"}
              </span>
            </Field>
          )}

          {/* Pipeline stages */}
          {showPipeline && (
            <div className="flex-1 min-w-0">
              <ApplicationStatusPipeline
                updates={statusUpdates}
                submitted={submitted}
                completedAt={run.completed_at}
                emailConfirmedAt={run.email_confirmed_at}
              />
            </div>
          )}

          {/* Tracking URL */}
          {run.tracking_url && (
            <a
              href={run.tracking_url}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-cyan-400 bg-cyan-500/10 border border-cyan-500/20 hover:bg-cyan-500/20 transition-all shrink-0"
            >
              <ExternalLink className="h-3.5 w-3.5" />
              Track
            </a>
          )}
        </div>

        {/* Dev mode details */}
        {devMode && (
          <div className="flex items-start gap-8 flex-wrap">
            <Field icon={<CheckCheck className="h-3 w-3" />} label="Current Step">
              {formatStep(run.current_state)}
            </Field>
            <Field icon={<Calendar className="h-3 w-3" />} label="Created">
              <span title={formatDateTime(run.created_at)}>
                {timeAgo(run.created_at)}
              </span>
            </Field>
            <Field icon={<Clock className="h-3 w-3" />} label="Updated">
              <span title={formatDateTime(run.updated_at)}>
                {timeAgo(run.updated_at)}
              </span>
            </Field>
            <Field icon={<Clock className="h-3 w-3" />} label="Completed">
              <span title={formatDateTime(run.completed_at)}>
                {run.completed_at ? timeAgo(run.completed_at) : "-"}
              </span>
            </Field>
            <Field icon={<Workflow className="h-3 w-3" />} label="Workflow ID">
              {run.temporal_workflow_id ? (
                <span className="inline-flex items-center gap-1.5">
                  <code className="font-mono text-xs text-muted-foreground truncate max-w-[200px]">
                    {run.temporal_workflow_id}
                  </code>
                  <CopyButton text={run.temporal_workflow_id} />
                </span>
              ) : (
                "-"
              )}
            </Field>
          </div>
        )}

        {run.retry_of_run_id && (
          <p className="text-xs text-muted-foreground">
            Retry of{" "}
            <Link href={`/applications/${run.retry_of_run_id}`} className="font-medium text-cyan-300 hover:underline">
              an earlier run
            </Link>
            . That run and its evidence are unchanged.
          </p>
        )}

        {/* Why it ended. Uncertain runs explain themselves in the resolution panel. */}
        {isDone && run.outcome !== "submit_uncertain" && <ReasonBox run={run} />}

        {/* Confirmation page excerpt: third-party HTML, sandboxed */}
        {run.confirmation_html && (
          <div>
            <div className="flex items-baseline justify-between gap-3 mb-2">
              <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground uppercase tracking-wider">
                <MessageSquareCheck className="h-3 w-3" />
                Confirmation
              </div>
              <span className="text-xs text-muted-foreground">
                Captured from the job site · scripts and remote content blocked
              </span>
            </div>
            <UntrustedHtmlFrame
              html={run.confirmation_html}
              title="Confirmation page captured from the job site"
              className="block w-full h-48 rounded-lg border border-white/10"
            />
          </div>
        )}
      </div>
    </div>
  );
}
