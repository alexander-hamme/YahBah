"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { Step } from "@/lib/types";

function statusIcon(status: string): string {
  switch (status) {
    case "COMPLETED":
      return "\u2713";
    case "RUNNING":
      return "\u25CB";
    case "FAILED":
      return "\u2717";
    case "SKIPPED":
      return "\u2014";
    default:
      return "\u00B7";
  }
}

function statusColor(status: string): string {
  switch (status) {
    case "COMPLETED":
      return "text-green-600 bg-green-100 dark:bg-green-900";
    case "RUNNING":
      return "text-blue-600 bg-blue-100 dark:bg-blue-900 animate-pulse";
    case "FAILED":
      return "text-red-600 bg-red-100 dark:bg-red-900";
    case "SKIPPED":
      return "text-gray-400 bg-gray-100 dark:bg-gray-800";
    default:
      return "text-gray-400 bg-gray-100 dark:bg-gray-800";
  }
}

function formatDuration(start: string | null, end: string | null): string {
  if (!start) return "";
  const s = new Date(start).getTime();
  const e = end ? new Date(end).getTime() : Date.now();
  const seconds = Math.round((e - s) / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${seconds % 60}s`;
}

function formatStepName(name: string): string {
  return name.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function StepTimeline({ steps }: { steps: Step[] }) {
  if (steps.length === 0) {
    return null;
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-lg">Step Timeline</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="space-y-3">
          {steps.map((step, i) => (
            <div key={step.id} className="flex items-start gap-3">
              <div className="flex flex-col items-center">
                <div
                  className={`w-7 h-7 rounded-full flex items-center justify-center text-sm font-bold ${statusColor(step.status)}`}
                >
                  {statusIcon(step.status)}
                </div>
                {i < steps.length - 1 && (
                  <div className="w-px h-6 bg-gray-200 dark:bg-gray-700 mt-1" />
                )}
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium">
                    {formatStepName(step.step_name)}
                  </span>
                  <span className="text-xs text-gray-400">
                    {formatDuration(step.started_at, step.completed_at)}
                  </span>
                </div>
                {step.logs && (
                  <p className="text-xs text-gray-500 mt-0.5 whitespace-pre-wrap truncate">
                    {step.logs}
                  </p>
                )}
              </div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
