"use client";

import { useState } from "react";
import {
  Activity,
  Calendar,
  Clock,
  AlertTriangle,
  Workflow,
  CheckCheck,
  Clipboard,
  Check,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { StatusBadge } from "@/components/status-badge";
import type { RunDetail } from "@/lib/types";

function timeAgo(iso: string | null): string {
  if (!iso) return "-";
  const seconds = Math.floor(
    (Date.now() - new Date(iso).getTime()) / 1000
  );
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
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

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      onClick={() => {
        navigator.clipboard.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      }}
      className="text-muted-foreground hover:text-foreground transition-colors"
      title="Copy to clipboard"
    >
      {copied ? (
        <Check className="h-3 w-3 text-emerald-500" />
      ) : (
        <Clipboard className="h-3 w-3" />
      )}
    </button>
  );
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

export function RunInfoCard({ run }: { run: RunDetail }) {
  return (
    <Card className="shadow-sm">
      <CardHeader className="pb-3">
        <CardTitle className="text-base font-semibold">Run Info</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-2 gap-x-6 gap-y-3">
          <Field icon={<Activity className="h-3 w-3" />} label="Status">
            <StatusBadge status={run.status} />
          </Field>
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
                <code className="font-mono text-xs text-muted-foreground truncate max-w-[140px]">
                  {run.temporal_workflow_id}
                </code>
                <CopyButton text={run.temporal_workflow_id} />
              </span>
            ) : (
              "-"
            )}
          </Field>
        </div>

        {run.error_message && (
          <div className="flex gap-3 p-3 rounded-lg bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-800">
            <AlertTriangle className="h-4 w-4 text-red-600 dark:text-red-400 shrink-0 mt-0.5" />
            <div>
              <p className="text-sm font-medium text-red-800 dark:text-red-200">
                Error
              </p>
              <p className="text-sm text-red-700 dark:text-red-300 mt-0.5 whitespace-pre-wrap">
                {run.error_message}
              </p>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
