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
import type { Step } from "@/lib/types";

function StepIcon({ status }: { status: string }) {
  switch (status) {
    case "COMPLETED":
      return (
        <div className="relative">
          <CheckCircle2 className="h-5 w-5 text-emerald-400" />
          <div className="absolute inset-0 rounded-full bg-emerald-400/20 blur-sm" />
        </div>
      );
    case "RUNNING":
      return (
        <div className="relative">
          <Loader2 className="h-5 w-5 text-cyan-400 animate-spin" />
          <div className="absolute inset-0 rounded-full bg-cyan-400/20 blur-sm animate-pulse" />
        </div>
      );
    case "FAILED":
      return (
        <div className="relative">
          <XCircle className="h-5 w-5 text-red-400" />
          <div className="absolute inset-0 rounded-full bg-red-400/20 blur-sm" />
        </div>
      );
    case "SKIPPED":
      return <MinusCircle className="h-5 w-5 text-slate-500" />;
    default:
      return <Circle className="h-5 w-5 text-white/15" />;
  }
}

function lineColor(status: string): string {
  switch (status) {
    case "COMPLETED":
      return "bg-emerald-500/40";
    case "RUNNING":
      return "bg-cyan-500/40";
    case "FAILED":
      return "bg-red-500/40";
    default:
      return "bg-white/5";
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
              className="text-muted-foreground hover:text-cyan-400 transition-colors"
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
          <pre className="mt-2 text-xs text-muted-foreground bg-white/[0.02] border border-white/5 rounded-md p-3 overflow-x-auto whitespace-pre-wrap font-mono">
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
    <div className="glass-panel rounded-xl overflow-hidden">
      <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-white/10 to-transparent" />
      <div className="px-5 pt-4 pb-2">
        <h3 className="text-sm font-semibold text-foreground" style={{ fontFamily: "var(--font-heading)" }}>
          Step Timeline
        </h3>
      </div>
      <div className="px-5 pb-5">
        {steps.map((step, i) => (
          <StepItem
            key={step.id}
            step={step}
            isLast={i === steps.length - 1}
          />
        ))}
      </div>
    </div>
  );
}
