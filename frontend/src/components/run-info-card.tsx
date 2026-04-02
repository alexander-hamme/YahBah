"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { StatusBadge } from "@/components/status-badge";
import type { RunDetail } from "@/lib/types";

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

export function RunInfoCard({ run }: { run: RunDetail }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-lg">Run Info</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-2 gap-x-8 gap-y-2 text-sm">
          <div>
            <span className="text-gray-500">Status</span>
            <div className="mt-0.5">
              <StatusBadge status={run.status} />
            </div>
          </div>
          <div>
            <span className="text-gray-500">Current Step</span>
            <p className="font-medium">{formatStep(run.current_state)}</p>
          </div>
          <div>
            <span className="text-gray-500">Created</span>
            <p className="font-medium">{formatDateTime(run.created_at)}</p>
          </div>
          <div>
            <span className="text-gray-500">Updated</span>
            <p className="font-medium">{formatDateTime(run.updated_at)}</p>
          </div>
          <div>
            <span className="text-gray-500">Completed</span>
            <p className="font-medium">{formatDateTime(run.completed_at)}</p>
          </div>
          <div>
            <span className="text-gray-500">Workflow ID</span>
            <p className="font-mono text-xs break-all">
              {run.temporal_workflow_id ?? "-"}
            </p>
          </div>
        </div>

        {run.error_message && (
          <div className="mt-3 p-3 rounded-md bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-800">
            <span className="text-sm font-medium text-red-800 dark:text-red-200">
              Error
            </span>
            <p className="text-sm text-red-700 dark:text-red-300 mt-1 whitespace-pre-wrap">
              {run.error_message}
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
