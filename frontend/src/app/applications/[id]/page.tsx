"use client";

import { use } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Bug } from "lucide-react";
import { RunActions } from "@/components/run-actions";
import { getRunDetail, getRunSteps, getRunArtifacts, getRunStatusUpdates } from "@/lib/api";
import { asApiError } from "@/lib/toast";
import { Button } from "@/components/ui/button";
import { ApiErrorMessage } from "@/components/api-error-message";
import { CompanyLogo } from "@/components/company-logo";
import { useDevMode } from "@/components/dev-mode-context";
import { JobMetadataCard } from "@/components/job-metadata-card";
import { OutcomeBadge } from "@/components/outcome-badge";
import { ReviewPanel } from "@/components/review/review-panel";
import { RunInfoCard } from "@/components/run-info-card";
import { StepTimeline } from "@/components/step-timeline";
import { ArtifactViewer } from "@/components/artifact-viewer";
import { FieldMappingsTable } from "@/components/field-mappings-table";
import { UncertainResolution } from "@/components/uncertain-resolution";

function DetailSkeleton() {
  return (
    <div className="space-y-6">
      <div className="h-8 bg-white/5 rounded-lg w-64 animate-pulse" />
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="h-64 bg-white/5 rounded-xl animate-pulse" />
        <div className="h-64 bg-white/5 rounded-xl animate-pulse" />
      </div>
      <div className="h-48 bg-white/5 rounded-xl animate-pulse" />
    </div>
  );
}

export default function ApplicationDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const { devMode, toggleDevMode } = useDevMode();

  const { data: run, error: runError } = useQuery({
    queryKey: ["run", id],
    queryFn: () => getRunDetail(id),
    refetchInterval: 10_000,
  });

  const { data: steps } = useQuery({
    queryKey: ["run-steps", id],
    queryFn: () => getRunSteps(id),
    refetchInterval: 10_000,
  });

  const { data: artifacts } = useQuery({
    queryKey: ["run-artifacts", id],
    queryFn: () => getRunArtifacts(id),
    refetchInterval: 10_000,
  });

  const { data: statusUpdates } = useQuery({
    queryKey: ["run-status-updates", id],
    queryFn: () => getRunStatusUpdates(id),
    refetchInterval: 30_000,
  });

  if (!run) {
    return (
      <div className="max-w-5xl mx-auto px-4 sm:px-6 py-8 space-y-4">
        {runError ? (
          <>
            <ApiErrorMessage error={asApiError(runError)} title="Couldn't load this application" />
            <Link href="/applications" className="text-sm font-medium text-cyan-300 hover:underline">
              Back to all applications
            </Link>
          </>
        ) : (
          <DetailSkeleton />
        )}
      </div>
    );
  }

  const reviewable = run.actions.includes("approve") || run.actions.includes("reject");

  return (
    <div className="max-w-6xl mx-auto px-4 sm:px-6 py-8 space-y-6 animate-fade-in">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <Link href="/applications" aria-label="Back to all applications">
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8 text-muted-foreground hover:text-cyan-400 hover:bg-white/5"
            >
              <ArrowLeft className="h-4 w-4" />
            </Button>
          </Link>
          <CompanyLogo
            companyWebsite={run.job_posting?.company_website ?? null}
            companyName={run.job_posting?.company ?? null}
            size={36}
          />
          <h1 className="min-w-0 text-xl font-bold tracking-tight">
            {run.job_posting?.company ?? "Unknown"} &mdash;{" "}
            {run.job_posting?.title ?? "Untitled"}
          </h1>
          <OutcomeBadge outcome={run.outcome} />
        </div>
        <div className="flex items-center gap-2">
          <RunActions runId={id} actions={run.actions} />
          <Button
            variant={devMode ? "default" : "ghost"}
            size="sm"
            onClick={toggleDevMode}
            aria-pressed={devMode}
            className={`gap-1.5 text-xs ${
              devMode
                ? "bg-cyan-500/20 text-cyan-400 border border-cyan-500/30 hover:bg-cyan-500/30"
                : "text-muted-foreground hover:text-foreground hover:bg-white/5"
            }`}
          >
            <Bug className="h-3.5 w-3.5" />
            Dev
          </Button>
        </div>
      </div>

      {runError && (
        <ApiErrorMessage error={asApiError(runError)} title="Couldn't refresh; showing the last state" />
      )}

      {reviewable && (
        <section aria-label="Review" className="glass-panel rounded-xl p-4 sm:p-5">
          <ReviewPanel runId={id} actions={run.actions} />
        </section>
      )}

      <UncertainResolution run={run} />

      <RunInfoCard run={run} statusUpdates={statusUpdates ?? []} />

      {run.job_posting && (
        <JobMetadataCard jobPosting={run.job_posting} jobUrl={run.job_url} matchScore={run.match_score} matchRationale={run.match_rationale} />
      )}

      {steps && steps.length > 0 && <StepTimeline steps={steps} />}

      {artifacts && artifacts.length > 0 && (
        <>
          {!reviewable && <FieldMappingsTable artifacts={artifacts} runId={id} outcome={run.outcome} />}
          <ArtifactViewer artifacts={artifacts} runId={id} outcome={run.outcome} />
        </>
      )}
    </div>
  );
}
