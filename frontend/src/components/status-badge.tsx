"use client";

import {
  Clock,
  Loader2,
  CheckCircle2,
  XCircle,
  Copy,
} from "lucide-react";

const statusConfig: Record<
  string,
  { label: string; icon: React.ReactNode; className: string }
> = {
  PENDING: {
    label: "Pending",
    icon: <Clock className="h-3 w-3" />,
    className:
      "bg-amber-500/15 text-amber-400 border-amber-500/30 shadow-[0_0_8px_rgba(251,191,36,0.15)]",
  },
  RUNNING: {
    label: "Running",
    icon: <Loader2 className="h-3 w-3 animate-spin" />,
    className:
      "bg-cyan-500/15 text-cyan-400 border-cyan-500/30 shadow-[0_0_12px_rgba(56,189,248,0.2)] animate-pulse-ring",
  },
  COMPLETED: {
    label: "Completed",
    icon: <CheckCircle2 className="h-3 w-3" />,
    className:
      "bg-emerald-500/15 text-emerald-400 border-emerald-500/30 shadow-[0_0_8px_rgba(52,211,153,0.15)]",
  },
  FAILED: {
    label: "Failed",
    icon: <XCircle className="h-3 w-3" />,
    className:
      "bg-red-500/15 text-red-400 border-red-500/30 shadow-[0_0_8px_rgba(239,68,68,0.15)]",
  },
  DUPLICATE: {
    label: "Duplicate",
    icon: <Copy className="h-3 w-3" />,
    className:
      "bg-slate-500/15 text-slate-400 border-slate-500/30",
  },
};

export function StatusBadge({ status }: { status: string }) {
  const config = statusConfig[status] ?? {
    label: status,
    icon: null,
    className: "bg-white/5 text-slate-400 border-white/10",
  };
  return (
    <span
      className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold border transition-all ${config.className}`}
    >
      {config.icon}
      {config.label}
    </span>
  );
}
