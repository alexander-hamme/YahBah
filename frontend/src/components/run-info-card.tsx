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
  ExternalLink,
  MessageSquareCheck,
  Target,
} from "lucide-react";
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
      className="text-muted-foreground hover:text-cyan-400 transition-colors"
      title="Copy to clipboard"
    >
      {copied ? (
        <Check className="h-3 w-3 text-emerald-400" />
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
    <div className="glass-panel rounded-xl overflow-hidden">
      <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-white/10 to-transparent" />
      <div className="px-5 pt-4 pb-2">
        <h3 className="text-sm font-semibold text-foreground" style={{ fontFamily: "var(--font-heading)" }}>
          Run Info
        </h3>
      </div>
      <div className="px-5 pb-5 space-y-4">
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

        {run.match_score != null && (
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground uppercase tracking-wider">
                <Target className="h-3 w-3" />
                Match Score
              </div>
              <span
                className={`text-2xl font-bold tabular-nums ${
                  run.match_score >= 85
                    ? "text-emerald-400"
                    : run.match_score >= 75
                    ? "text-teal-400"
                    : run.match_score >= 50
                    ? "text-cyan-400"
                    : run.match_score >= 35
                    ? "text-amber-400"
                    : "text-red-400"
                }`}
                style={{ fontFamily: "var(--font-heading)" }}
              >
                {run.match_score}%
              </span>
            </div>
            <div className="h-2 rounded-full overflow-hidden bg-white/5">
              <div
                className={`h-full rounded-full transition-all duration-500 ${
                  run.match_score >= 85
                    ? "bg-emerald-500 shadow-[0_0_8px_rgba(52,211,153,0.4)]"
                    : run.match_score >= 75
                    ? "bg-teal-500 shadow-[0_0_8px_rgba(20,184,166,0.4)]"
                    : run.match_score >= 50
                    ? "bg-cyan-500 shadow-[0_0_8px_rgba(56,189,248,0.4)]"
                    : run.match_score >= 35
                    ? "bg-amber-500 shadow-[0_0_8px_rgba(251,191,36,0.4)]"
                    : "bg-red-500 shadow-[0_0_8px_rgba(239,68,68,0.4)]"
                }`}
                style={{ width: `${run.match_score}%` }}
              />
            </div>
            {run.match_rationale && (
              <p className="text-xs text-muted-foreground">
                {run.match_rationale}
              </p>
            )}
          </div>
        )}

        {run.error_message && (
          <div className="flex gap-3 p-3 rounded-lg bg-red-500/10 border border-red-500/20 shadow-[0_0_12px_rgba(239,68,68,0.08)]">
            <AlertTriangle className="h-4 w-4 text-red-400 shrink-0 mt-0.5" />
            <div>
              <p className="text-sm font-medium text-red-400">Error</p>
              <p className="text-sm text-red-300/80 mt-0.5 whitespace-pre-wrap">
                {run.error_message}
              </p>
            </div>
          </div>
        )}

        {run.tracking_url && (
          <a
            href={run.tracking_url}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl text-sm font-semibold text-cyan-400 bg-cyan-500/10 border border-cyan-500/20 hover:bg-cyan-500/20 hover:shadow-[0_0_16px_rgba(56,189,248,0.2)] transition-all"
          >
            <ExternalLink className="h-4 w-4" />
            Track Application Status
          </a>
        )}

        {run.confirmation_html && (
          <div>
            <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground uppercase tracking-wider mb-2">
              <MessageSquareCheck className="h-3 w-3" />
              Confirmation
            </div>
            <div
              className="text-sm text-foreground/80 bg-white/[0.02] border border-white/5 rounded-lg p-3 max-h-48 overflow-y-auto [&_a]:text-cyan-400 [&_a]:underline [&_h1]:text-lg [&_h1]:font-bold [&_h2]:text-base [&_h2]:font-semibold [&_p]:my-1 [&_ul]:list-disc [&_ul]:pl-4"
              dangerouslySetInnerHTML={{ __html: run.confirmation_html }}
            />
          </div>
        )}
      </div>
    </div>
  );
}
