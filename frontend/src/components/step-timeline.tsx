"use client";

import { useState } from "react";
import {
  CheckCircle2,
  Loader2,
  XCircle,
  MinusCircle,
  Circle,
  Clock,
  ChevronDown,
  ChevronRight,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { Step } from "@/lib/types";

function StepIcon({ status }: { status: string }) {
  switch (status) {
    case "COMPLETED":
      return <CheckCircle2 className="h-5 w-5 text-emerald-500" />;
    case "RUNNING":
      return <Loader2 className="h-5 w-5 text-blue-500 animate-spin" />;
    case "FAILED":
      return <XCircle className="h-5 w-5 text-red-500" />;
    case "SKIPPED":
      return <MinusCircle className="h-5 w-5 text-slate-400" />;
    default:
      return <Circle className="h-5 w-5 text-slate-300" />;
  }
}

function lineColor(status: string): string {
  switch (status) {
    case "COMPLETED":
      return "bg-emerald-300 dark:bg-emerald-700";
    case "RUNNING":
      return "bg-blue-300 dark:bg-blue-700";
    case "FAILED":
      return "bg-red-300 dark:bg-red-700";
    default:
      return "bg-border";
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

function StepItem({
  step,
  isLast,
}: {
  step: Step;
  isLast: boolean;
}) {
  const [expanded, setExpanded] = useState(step.status === "FAILED");
  const duration = formatDuration(step.started_at, step.completed_at);
  const hasLogs = !!step.logs;

  return (
    <div className="flex gap-3">
      <div className="flex flex-col items-center">
        <StepIcon status={step.status} />
        {!isLast && (
          <div
            className={`w-0.5 flex-1 mt-1 min-h-[16px] ${lineColor(step.status)}`}
          />
        )}
      </div>
      <div className="flex-1 pb-4 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-foreground">
            {formatStepName(step.step_name)}
          </span>
          {duration && (
            <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
              <Clock className="h-3 w-3" />
              {duration}
            </span>
          )}
          {hasLogs && (
            <button
              onClick={() => setExpanded(!expanded)}
              className="text-muted-foreground hover:text-foreground transition-colors"
            >
              {expanded ? (
                <ChevronDown className="h-3.5 w-3.5" />
              ) : (
                <ChevronRight className="h-3.5 w-3.5" />
              )}
            </button>
          )}
        </div>
        {expanded && step.logs && (
          <pre className="mt-2 text-xs text-muted-foreground bg-muted rounded-md p-3 overflow-x-auto whitespace-pre-wrap font-mono">
            {step.logs}
          </pre>
        )}
      </div>
    </div>
  );
}

export function StepTimeline({ steps }: { steps: Step[] }) {
  if (steps.length === 0) return null;

  return (
    <Card className="shadow-sm">
      <CardHeader className="pb-3">
        <CardTitle className="text-base font-semibold">Step Timeline</CardTitle>
      </CardHeader>
      <CardContent>
        {steps.map((step, i) => (
          <StepItem
            key={step.id}
            step={step}
            isLast={i === steps.length - 1}
          />
        ))}
      </CardContent>
    </Card>
  );
}
