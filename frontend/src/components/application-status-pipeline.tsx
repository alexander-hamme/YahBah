"use client";

import { useState, useRef, useEffect } from "react";
import {
  XCircle,
  FileText,
  MessageSquare,
  Trophy,
  Brain,
  CalendarCheck,
  LogOut,
  HelpCircle,
  Send,
} from "lucide-react";
import type { StatusUpdate } from "@/lib/types";

// Progressive stage ordering — later stages imply RECEIVED.
// Only stages with explicit emails are shown (except RECEIVED which is
// implied by any later stage).
const STAGE_ORDER = [
  "UNDER_REVIEW",
  "ONLINE_ASSESSMENT",
  "INTERVIEW_REQUEST",
  "INTERVIEW_SCHEDULED",
  "OFFER",
] as const;

const TERMINAL_STATUSES = new Set(["REJECTED", "WITHDRAWN", "OTHER"]);

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
  SUBMITTED: {
    label: "Submitted",
    icon: <Send className={iconSize} />,
    activeColor: "text-cyan-400",
    activeBg: "bg-cyan-500/15",
    activeBorder: "border-cyan-500/40",
    glow: "shadow-[0_0_12px_rgba(56,189,248,0.25)]",
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
    label: "Update",
    icon: <HelpCircle className={iconSize} />,
    activeColor: "text-slate-400",
    activeBg: "bg-slate-500/15",
    activeBorder: "border-slate-500/40",
    glow: "",
  },
};

function formatShortDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

function formatFullDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

/**
 * Compute visible pipeline stages. Only show stages we have evidence for:
 * - SUBMITTED: always, if run completed
 * - RECEIVED: if explicit email OR implied by any later stage
 * - Everything else: only if we have an explicit email for it
 */
function computeVisibleStages(
  explicitTypes: Set<string>,
  submitted: boolean
): string[] {
  const stages: string[] = [];

  if (submitted) stages.push("SUBMITTED");

  // Any non-terminal stage implies UNDER_REVIEW
  const hasAnyProgress = STAGE_ORDER.some((s) => explicitTypes.has(s));
  if (hasAnyProgress) {
    stages.push("UNDER_REVIEW");
  }

  // Add only later stages with explicit emails (skip UNDER_REVIEW, already handled)
  for (let i = 1; i < STAGE_ORDER.length; i++) {
    if (explicitTypes.has(STAGE_ORDER[i])) {
      stages.push(STAGE_ORDER[i]);
    }
  }

  return stages;
}

function StageNode({
  stage,
  date,
  updates,
  isLatest,
}: {
  stage: string;
  date: string | null;
  updates: StatusUpdate[];
  isLatest: boolean;
}) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const config = stageConfig[stage] ?? stageConfig.OTHER;
  const hasUpdates = updates.length > 0;

  useEffect(() => {
    if (!open) return;
    function handleClick(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open]);

  return (
    <div ref={containerRef} className="relative flex flex-col items-center gap-1.5 min-w-0">
      <button
        onClick={() => hasUpdates && setOpen(!open)}
        disabled={!hasUpdates}
        className={`
          relative flex items-center justify-center w-10 h-10 rounded-full border-2 transition-all
          ${config.activeBg} ${config.activeBorder} ${config.activeColor} ${config.glow}
          ${isLatest ? "animate-pulse-ring" : ""}
          ${hasUpdates ? "cursor-pointer hover:scale-110" : "cursor-default"}
        `}
      >
        {config.icon}
      </button>

      <span
        className={`text-[11px] font-medium tracking-wide text-center leading-tight ${config.activeColor}`}
      >
        {config.label}
      </span>

      {date && (
        <span className="text-[10px] text-white/30">
          {formatShortDate(date)}
        </span>
      )}

      {hasUpdates && open && (
        <div
          className={`
            absolute top-full mt-2 z-50 w-64 max-h-48 overflow-y-auto
            rounded-lg border p-3 text-xs space-y-2
            bg-black/90 backdrop-blur-md shadow-lg
            ${config.activeBorder}
          `}
        >
          {updates.map((u) => (
            <div key={u.id} className="space-y-0.5 select-text">
              {u.subject && (
                <p className="text-white/80 font-medium">{u.subject}</p>
              )}
              {u.summary && (
                <p className="text-white/50 leading-relaxed">{u.summary}</p>
              )}
              <p className="text-white/30">{formatFullDate(u.email_date)}</p>
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
  completedAt,
}: {
  updates: StatusUpdate[];
  runStatus: string;
  completedAt: string | null;
}) {
  // Group updates by status_type
  const byType: Record<string, StatusUpdate[]> = {};
  for (const u of updates) {
    (byType[u.status_type] ??= []).push(u);
  }

  const explicitTypes = new Set(Object.keys(byType));
  const submitted = runStatus === "COMPLETED";

  const visibleStages = computeVisibleStages(explicitTypes, submitted);

  // Terminal statuses
  const terminalUpdates: { type: string; updates: StatusUpdate[] }[] = [];
  for (const [type, typeUpdates] of Object.entries(byType)) {
    if (TERMINAL_STATUSES.has(type)) {
      terminalUpdates.push({ type, updates: typeUpdates });
    }
  }

  const latestTerminal =
    terminalUpdates.length > 0 ? terminalUpdates[0].type : null;
  const latestStage =
    visibleStages.length > 0 ? visibleStages[visibleStages.length - 1] : null;
  const overallLatest = latestTerminal ?? latestStage;

  const hasAnyUpdates = updates.length > 0;

  // Get the date for each stage
  function stageDate(stage: string): string | null {
    if (stage === "SUBMITTED") return completedAt;
    const stageUpdates = byType[stage];
    if (stageUpdates && stageUpdates.length > 0) {
      return stageUpdates[0].email_date;
    }
    // UNDER_REVIEW implied by a later stage — use the earliest email date
    if (stage === "UNDER_REVIEW" && !byType["UNDER_REVIEW"] && updates.length > 0) {
      return updates[0].email_date;
    }
    return null;
  }

  return (
    <div className="space-y-4 py-1">
      <div className="flex items-start gap-0 pb-1">
        {visibleStages.map((stage, i) => (
          <div key={stage} className="flex items-start shrink-0">
            <StageNode
              stage={stage}
              date={stageDate(stage)}
              updates={byType[stage] ?? []}
              isLatest={overallLatest === stage}
            />
            {i < visibleStages.length - 1 && (
              <div className="flex items-center pt-4 px-1">
                <div className="w-8 h-0.5 bg-white/20" />
              </div>
            )}
          </div>
        ))}
      </div>

      {terminalUpdates.length > 0 && (
        <div className="flex flex-wrap gap-2">
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
                <span className="text-white/30">
                  {formatShortDate(latest.email_date)}
                </span>
                {latest.summary && (
                  <span className="text-white/40 max-w-xs truncate">
                    {latest.summary}
                  </span>
                )}
              </div>
            );
          })}
        </div>
      )}

      {!hasAnyUpdates && (
        <p className="text-[11px] text-white/20">
          Status updates from email will appear here as they arrive
        </p>
      )}
    </div>
  );
}
