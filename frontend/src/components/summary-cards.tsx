"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Loader2,
  CheckCircle2,
  XCircle,
  Clock,
  Copy,
} from "lucide-react";
import { getApplicationStats } from "@/lib/api";

interface StatusDef {
  label: string;
  key: "running" | "completed" | "failed" | "pending" | "duplicate";
  icon: React.ReactNode;
  color: string;
  bg: string;
  barColor: string;
}

const pipeline: StatusDef[] = [
  {
    label: "Pending",
    key: "pending",
    icon: <Clock className="h-3.5 w-3.5" />,
    color: "text-amber-600 dark:text-amber-400",
    bg: "bg-amber-500/10 hover:bg-amber-500/20",
    barColor: "bg-amber-400",
  },
  {
    label: "Running",
    key: "running",
    icon: <Loader2 className="h-3.5 w-3.5 animate-spin" />,
    color: "text-blue-600 dark:text-blue-400",
    bg: "bg-blue-500/10 hover:bg-blue-500/20",
    barColor: "bg-blue-500",
  },
  {
    label: "Completed",
    key: "completed",
    icon: <CheckCircle2 className="h-3.5 w-3.5" />,
    color: "text-emerald-600 dark:text-emerald-400",
    bg: "bg-emerald-500/10 hover:bg-emerald-500/20",
    barColor: "bg-emerald-500",
  },
];

const outcomes: StatusDef[] = [
  {
    label: "Failed",
    key: "failed",
    icon: <XCircle className="h-3.5 w-3.5" />,
    color: "text-red-600 dark:text-red-400",
    bg: "bg-red-500/10 hover:bg-red-500/20",
    barColor: "bg-red-500",
  },
  {
    label: "Duplicate",
    key: "duplicate",
    icon: <Copy className="h-3.5 w-3.5" />,
    color: "text-slate-500 dark:text-slate-400",
    bg: "bg-slate-500/10 hover:bg-slate-500/20",
    barColor: "bg-slate-400",
  },
];

function StatusPill({
  def,
  count,
  onClick,
}: {
  def: StatusDef;
  count: number;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`group/pill flex items-center gap-2 px-3.5 py-2 rounded-xl ${def.bg} transition-all duration-200 hover:-translate-y-0.5 hover:shadow-md cursor-pointer`}
    >
      <span className={def.color}>{def.icon}</span>
      <span
        className={`text-lg font-bold tabular-nums ${def.color}`}
        style={{ fontFamily: "var(--font-heading)" }}
      >
        {count}
      </span>
      <span className="text-xs font-medium text-muted-foreground group-hover/pill:text-foreground transition-colors">
        {def.label}
      </span>
    </button>
  );
}

export function SummaryCards({
  onStatusClick,
}: {
  onStatusClick?: (status: string) => void;
}) {
  const { data: stats } = useQuery({
    queryKey: ["application-stats"],
    queryFn: getApplicationStats,
    refetchInterval: 10_000,
  });

  const total = stats?.total ?? 0;
  const pipelineTotal =
    (stats?.pending ?? 0) + (stats?.running ?? 0) + (stats?.completed ?? 0);

  return (
    <div className="relative">
      {/* Ambient blur blobs */}
      <div className="absolute -top-8 -left-8 w-40 h-40 bg-primary/8 rounded-full blur-3xl pointer-events-none" />
      <div className="absolute -bottom-6 -right-6 w-32 h-32 bg-emerald-500/8 rounded-full blur-3xl pointer-events-none" />
      <div className="absolute top-4 right-1/3 w-24 h-24 bg-amber-500/6 rounded-full blur-2xl pointer-events-none" />

      {/* Glassmorphism container */}
      <div className="relative rounded-2xl border border-white/20 dark:border-white/10 bg-white/60 dark:bg-white/5 backdrop-blur-xl shadow-[0_8px_32px_-8px_rgba(0,0,0,0.08),inset_0_1px_0_rgba(255,255,255,0.4)] dark:shadow-[0_8px_32px_-8px_rgba(0,0,0,0.3),inset_0_1px_0_rgba(255,255,255,0.06)] overflow-hidden">
        {/* Inner glow top edge */}
        <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-white/60 dark:via-white/10 to-transparent" />

        <div className="px-6 py-5 flex flex-col sm:flex-row items-center gap-6">
          {/* Hero total */}
          <button
            onClick={() => onStatusClick?.("all")}
            className="flex items-baseline gap-3 group cursor-pointer shrink-0"
          >
            <span
              className="text-6xl font-extrabold tracking-tighter text-foreground tabular-nums transition-transform group-hover:scale-105"
              style={{ fontFamily: "var(--font-heading)" }}
            >
              {total}
            </span>
            <span className="text-sm font-medium text-muted-foreground uppercase tracking-widest">
              Applications
            </span>
          </button>

          {/* Divider */}
          <div className="hidden sm:block w-px h-14 bg-gradient-to-b from-transparent via-border to-transparent" />

          {/* Pipeline progression + outcomes */}
          <div className="flex flex-col gap-3 flex-1 min-w-0">
            {/* Pipeline: Pending → Running → Completed */}
            <div className="flex items-center gap-0">
              {pipeline.map((s, i) => (
                <div key={s.key} className="flex items-center">
                  {i > 0 && (
                    <div className="flex items-center px-1.5 text-muted-foreground/30">
                      <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                        <path d="M6 3L11 8L6 13" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                      </svg>
                    </div>
                  )}
                  <StatusPill
                    def={s}
                    count={stats?.[s.key] ?? 0}
                    onClick={() => onStatusClick?.(s.key.toUpperCase())}
                  />
                </div>
              ))}
            </div>

            {/* Pipeline progress bar */}
            {pipelineTotal > 0 && (
              <div className="flex h-1 rounded-full overflow-hidden bg-muted/50">
                {pipeline.map((s) => {
                  const count = stats?.[s.key] ?? 0;
                  if (count === 0) return null;
                  return (
                    <div
                      key={s.key}
                      className={`${s.barColor} transition-all duration-500`}
                      style={{ width: `${(count / pipelineTotal) * 100}%` }}
                      title={`${s.label}: ${count}`}
                    />
                  );
                })}
              </div>
            )}
          </div>

          {/* Divider */}
          <div className="hidden sm:block w-px h-14 bg-gradient-to-b from-transparent via-border to-transparent" />

          {/* Outcomes: Failed & Duplicate */}
          <div className="flex items-center gap-2 shrink-0">
            {outcomes.map((s) => (
              <StatusPill
                key={s.key}
                def={s}
                count={stats?.[s.key] ?? 0}
                onClick={() => onStatusClick?.(s.key.toUpperCase())}
              />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
