"use client";

import { use } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { getRunDetail, getRunSteps, getRunArtifacts } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { useDevMode } from "@/components/dev-mode-context";
import { JobMetadataCard } from "@/components/job-metadata-card";
import { RunInfoCard } from "@/components/run-info-card";
import { StepTimeline } from "@/components/step-timeline";
import { ArtifactViewer } from "@/components/artifact-viewer";
import { FieldMappingsTable } from "@/components/field-mappings-table";

export default function ApplicationDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const { devMode, toggleDevMode } = useDevMode();

  const { data: run, isLoading: runLoading } = useQuery({
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

  if (runLoading || !run) {
    return (
      <div className="max-w-5xl mx-auto px-6 py-8">
        <p className="text-gray-500">Loading...</p>
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto px-6 py-8 space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Link
            href="/applications"
            className="text-sm text-gray-500 hover:text-gray-900 dark:hover:text-gray-100"
          >
            &larr; Back
          </Link>
          <h1 className="text-2xl font-bold">
            {run.job_posting?.company ?? "Unknown"} &mdash;{" "}
            {run.job_posting?.title ?? "Untitled"}
          </h1>
        </div>
        <Button
          variant={devMode ? "default" : "outline"}
          size="sm"
          onClick={toggleDevMode}
          className="text-xs"
        >
          Dev
        </Button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {run.job_posting && (
          <JobMetadataCard jobPosting={run.job_posting} jobUrl={run.job_url} />
        )}
        <RunInfoCard run={run} />
      </div>

      {steps && steps.length > 0 && <StepTimeline steps={steps} />}

      {artifacts && artifacts.length > 0 && (
        <>
          <FieldMappingsTable artifacts={artifacts} runId={id} />
          <ArtifactViewer artifacts={artifacts} runId={id} />
        </>
      )}
    </div>
  );
}
