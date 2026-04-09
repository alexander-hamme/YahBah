"use client";

import { useState } from "react";
import {
  CheckCircle2,
  Circle,
  Clock,
  XCircle,
  Mail,
  FileText,
  MessageSquare,
  Trophy,
  ChevronDown,
  ChevronUp,
  Brain,
  CalendarCheck,
  LogOut,
  HelpCircle,
} from "lucide-react";
import type { StatusUpdate } from "@/lib/types";

// Ordered pipeline stages — the "happy path" flows left to right.
// REJECTED can branch off at any point.
const PIPELINE_STAGES = [
  "RECEIVED",
  "UNDER_REVIEW",
  "ONLINE_ASSESSMENT",
  "INTERVIEW_REQUEST",
  "INTERVIEW_SCHEDULED",
  "OFFER",
] as const;

const TERMINAL_STATUSES = ["REJECTED", "WITHDRAWN", "OTHER"] as const;

type StageType = (typeof PIPELINE_STAGES)[number];
type TerminalType = (typeof TERMINAL_STATUSES)[number];

interface StageConfig {
  label: string;
  icon: React.ReactNode;
  activeColor: string;
  activeBg: string;
  activeBorder: string;
  glow: string;
}

const iconSize = "h-4 w-4";

const stageConfig: Record<string, StageConfig> = {
  RECEIVED: {
    label: "Received",
    icon: <Mail className={iconSize} />,
    activeColor: "text-emerald-400",
    activeBg: "bg-emerald-500/15",
    activeBorder: "border-emerald-500/40",
    glow: "shadow-[0_0_12px_rgba(52,211,153,0.25)]",
  },
  UNDER_REVIEW: {
    label: "Under Review",
    icon: <FileText className={iconSize} />,
    activeColor: "text-cyan-400",
    activeBg: "bg-cyan-500/15",
    activeBorder: "border-cyan-500/40",
    glow: "shadow-[0_0_12px_rgba(56,189,248,0.25)]",
  },
  ONLINE_ASSESSMENT: {
    label: "Assessment",
    icon: <Brain className={iconSize} />,
    activeColor: "text-amber-400",
    activeBg: "bg-amber-500/15",
    activeBorder: "border-amber-500/40",
    glow: "shadow-[0_0_12px_rgba(251,191,36,0.25)]",
  },
  INTERVIEW_REQUEST: {
    label: "Interview",
    icon: <MessageSquare className={iconSize} />,
    activeColor: "text-violet-400",
    activeBg: "bg-violet-500/15",
    activeBorder: "border-violet-500/40",
    glow: "shadow-[0_0_12px_rgba(167,139,250,0.25)]",
  },
  INTERVIEW_SCHEDULED: {
    label: "Scheduled",
    icon: <CalendarCheck className={iconSize} />,
    activeColor: "text-violet-400",
    activeBg: "bg-violet-500/15",
    activeBorder: "border-violet-500/40",
    glow: "shadow-[0_0_12px_rgba(167,139,250,0.25)]",
  },
  OFFER: {
    label: "Offer",
    icon: <Trophy className={iconSize} />,
    activeColor: "text-emerald-300",
    activeBg: "bg-emerald-500/20",
    activeBorder: "border-emerald-400/50",
    glow: "shadow-[0_0_16px_rgba(52,211,153,0.35)]",
  },
  REJECTED: {
    label: "Rejected",
    icon: <XCircle className={iconSize} />,
    activeColor: "text-red-400",
    activeBg: "bg-red-500/15",
    activeBorder: "border-red-500/40",
    glow: "shadow-[0_0_12px_rgba(239,68,68,0.25)]",
  },
  WITHDRAWN: {
    label: "Withdrawn",
    icon: <LogOut className={iconSize} />,
    activeColor: "text-slate-400",
    activeBg: "bg-slate-500/15",
    activeBorder: "border-slate-500/40",
    glow: "",
  },
  OTHER: {
    label: "Other",
    icon: <HelpCircle className={iconSize} />,
    activeColor: "text-slate-400",
    activeBg: "bg-slate-500/15",
    activeBorder: "border-slate-500/40",
    glow: "",
  },
};

function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function StageNode({
  stage,
  updates,
  isActive,
  isLatest,
}: {
  stage: string;
  updates: StatusUpdate[];
  isActive: boolean;
  isLatest: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const config = stageConfig[stage] ?? stageConfig.OTHER;
  const hasUpdates = updates.length > 0;

  return (
    <div className="flex flex-col items-center gap-1.5 min-w-0">
      {/* Node circle */}
      <button
        onClick={() => hasUpdates && setExpanded(!expanded)}
        disabled={!hasUpdates}
        className={`
          relative flex items-center justify-center w-10 h-10 rounded-full border-2 transition-all
          ${
            isActive
              ? `${config.activeBg} ${config.activeBorder} ${config.activeColor} ${config.glow}`
              : "bg-white/[0.03] border-white/10 text-white/20"
          }
          ${isLatest && isActive ? "animate-pulse-ring" : ""}
          ${hasUpdates ? "cursor-pointer hover:scale-110" : "cursor-default"}
        `}
      >
        {isActive ? config.icon : <Circle className="h-3 w-3" />}
      </button>

      {/* Label */}
      <span
        className={`text-[11px] font-medium tracking-wide text-center leading-tight ${
          isActive ? config.activeColor : "text-white/25"
        }`}
      >
        {config.label}
      </span>

      {/* Expandable detail */}
      {hasUpdates && expanded && (
        <div
          className={`
            mt-1 w-48 rounded-lg border p-2.5 text-xs space-y-1.5
            bg-black/60 backdrop-blur-sm
            ${config.activeBorder}
          `}
        >
          {updates.map((u) => (
            <div key={u.id} className="space-y-0.5">
              {u.subject && (
                <p className="text-white/70 font-medium truncate">{u.subject}</p>
              )}
              {u.summary && (
                <p className="text-white/50 leading-relaxed">{u.summary}</p>
              )}
              <p className="text-white/30">{formatDate(u.email_date)}</p>
              {u.sender && (
                <p className="text-white/20 truncate">{u.sender}</p>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function ApplicationStatusPipeline({
  updates,
  runStatus,
}: {
  updates: StatusUpdate[];
  runStatus: string;
}) {
  // Group updates by status_type
  const byType: Record<string, StatusUpdate[]> = {};
  for (const u of updates) {
    (byType[u.status_type] ??= []).push(u);
  }

  // Determine which stages are reached
  const reachedStages = new Set(updates.map((u) => u.status_type));

  // "Submitted" is considered reached if the run completed (or is a test run)
  const submitted = runStatus === "COMPLETED";

  // Find the latest (rightmost) reached stage in the pipeline
  let latestPipelineIdx = -1;
  for (let i = PIPELINE_STAGES.length - 1; i >= 0; i--) {
    if (reachedStages.has(PIPELINE_STAGES[i])) {
      latestPipelineIdx = i;
      break;
    }
  }

  // Check for terminal statuses
  const terminalUpdates: { type: string; updates: StatusUpdate[] }[] = [];
  for (const t of TERMINAL_STATUSES) {
    if (byType[t]) {
      terminalUpdates.push({ type: t, updates: byType[t] });
    }
  }

  const latestStage =
    latestPipelineIdx >= 0 ? PIPELINE_STAGES[latestPipelineIdx] : null;
  const latestTerminal =
    terminalUpdates.length > 0 ? terminalUpdates[0].type : null;
  // If we have no email updates yet, "Submitted" is the latest
  const overallLatest =
    latestTerminal ?? latestStage ?? (submitted ? "SUBMITTED" : null);

  const hasAnyUpdates = updates.length > 0;

  return (
    <div className="relative bg-white/[0.02] border border-white/5 rounded-xl overflow-hidden">
      {/* Gradient top border */}
      <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-cyan-500/30 to-transparent" />

      <div className="p-5">
        <h3 className="text-sm font-semibold text-white/60 uppercase tracking-wider mb-4">
          Application Updates
        </h3>

        {/* Main pipeline */}
        <div className="flex items-start gap-0 overflow-x-auto pb-2">
          {/* Submitted anchor node */}
          <div className="flex items-start shrink-0">
            <div className="flex flex-col items-center gap-1.5 min-w-0">
              <div
                className={`
                  flex items-center justify-center w-10 h-10 rounded-full border-2 transition-all
                  ${
                    submitted
                      ? "bg-cyan-500/15 border-cyan-500/40 text-cyan-400 shadow-[0_0_12px_rgba(56,189,248,0.25)]"
                      : "bg-white/[0.03] border-white/10 text-white/20"
                  }
                  ${overallLatest === "SUBMITTED" ? "animate-pulse-ring" : ""}
                `}
              >
                {submitted ? (
                  <CheckCircle2 className={iconSize} />
                ) : (
                  <Circle className="h-3 w-3" />
                )}
              </div>
              <span
                className={`text-[11px] font-medium tracking-wide text-center leading-tight ${
                  submitted ? "text-cyan-400" : "text-white/25"
                }`}
              >
                Submitted
              </span>
            </div>
            <div className="flex items-center pt-4 px-1">
              <div
                className={`w-6 h-0.5 transition-colors ${
                  submitted && reachedStages.has(PIPELINE_STAGES[0])
                    ? stageConfig[PIPELINE_STAGES[0]].activeColor.replace("text-", "bg-")
                    : submitted
                    ? "bg-white/15"
                    : "bg-white/5"
                }`}
              />
            </div>
          </div>

          {PIPELINE_STAGES.map((stage, i) => (
            <div key={stage} className="flex items-start shrink-0">
              <StageNode
                stage={stage}
                updates={byType[stage] ?? []}
                isActive={reachedStages.has(stage)}
                isLatest={overallLatest === stage}
              />
              {/* Connector line */}
              {i < PIPELINE_STAGES.length - 1 && (
                <div className="flex items-center pt-4 px-1">
                  <div
                    className={`w-6 h-0.5 transition-colors ${
                      reachedStages.has(stage) &&
                      reachedStages.has(PIPELINE_STAGES[i + 1])
                        ? stageConfig[PIPELINE_STAGES[i + 1]].activeColor.replace(
                            "text-",
                            "bg-"
                          )
                        : reachedStages.has(stage)
                        ? "bg-white/15"
                        : "bg-white/5"
                    }`}
                  />
                </div>
              )}
            </div>
          ))}
        </div>

        {/* Terminal status badges (Rejected, Withdrawn, etc) */}
        {terminalUpdates.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-2">
            {terminalUpdates.map(({ type, updates: tUpdates }) => {
              const config = stageConfig[type] ?? stageConfig.OTHER;
              const latest = tUpdates[tUpdates.length - 1];
              return (
                <div
                  key={type}
                  className={`
                    flex items-center gap-2 px-3 py-1.5 rounded-lg border text-xs
                    ${config.activeBg} ${config.activeBorder} ${config.activeColor} ${config.glow}
                  `}
                >
                  {config.icon}
                  <span className="font-semibold">{config.label}</span>
                  {latest.summary && (
                    <span className="text-white/40 ml-1 max-w-xs truncate">
                      {latest.summary}
                    </span>
                  )}
                  <span className="text-white/25 ml-1">
                    {formatDate(latest.email_date)}
                  </span>
                </div>
              );
            })}
          </div>
        )}

        {/* Empty state hint */}
        {!hasAnyUpdates && (
          <p className="text-[11px] text-white/20 mt-2">
            Status updates from email will appear here as they arrive
          </p>
        )}
      </div>
    </div>
  );
}
