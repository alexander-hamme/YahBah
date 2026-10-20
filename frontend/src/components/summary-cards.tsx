"use client";

import { OUTCOME_INFO, OUTCOME_TONE_CLASS } from "@/lib/outcomes";
import { useApplicationStats } from "@/lib/queries";
import type { Outcome } from "@/lib/types";
import { cn } from "@/lib/utils";

// Counts by server-derived outcome, grouped by where the application stands.
const GROUPS: { label: string; outcomes: Outcome[] }[] = [
  { label: "In flight", outcomes: ["queued", "in_progress", "awaiting_review"] },
  { label: "Submitted", outcomes: ["submitted", "email_verified"] },
  { label: "Maybe", outcomes: ["submit_uncertain"] },
  { label: "Not submitted", outcomes: ["test_only", "failed", "cancelled", "expired", "duplicate"] },
];

function OutcomePill({
  outcome,
  count,
  active,
  onClick,
}: {
  outcome: Outcome;
  count: number;
  active: boolean;
  onClick: () => void;
}) {
  const info = OUTCOME_INFO[outcome];
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      title={info.description}
      className={cn(
        "flex items-baseline gap-1.5 rounded-lg border px-2.5 py-1.5 text-left transition-colors",
        count > 0 ? OUTCOME_TONE_CLASS[info.tone] : "border-white/5 bg-white/[0.02] text-muted-foreground",
        active && "ring-2 ring-cyan-400/50"
      )}
    >
      <span className="text-base font-bold tabular-nums">{count}</span>
      <span className="text-xs font-medium">{info.label}</span>
    </button>
  );
}

export function SummaryCards({
  activeFilter,
  onFilter,
}: {
  activeFilter: string;
  onFilter: (filter: string) => void;
}) {
  const { data: stats } = useApplicationStats();

  return (
    <div className="glass-panel-elevated flex flex-col gap-4 rounded-2xl px-5 py-4 lg:flex-row lg:items-center">
      <button
        type="button"
        onClick={() => onFilter("all")}
        aria-pressed={activeFilter === "all"}
        className="flex shrink-0 items-baseline gap-2.5 text-left"
      >
        <span className="text-5xl font-extrabold tabular-nums tracking-tighter" style={{ fontFamily: "var(--font-heading)" }}>
          {stats?.total ?? 0}
        </span>
        <span className="text-xs font-medium uppercase tracking-widest text-muted-foreground">Applications</span>
      </button>
      <div className="flex flex-1 flex-wrap gap-x-6 gap-y-3">
        {GROUPS.map((group) => (
          <div key={group.label} className="min-w-0 space-y-1.5">
            <p className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">{group.label}</p>
            <div className="flex flex-wrap gap-1.5">
              {group.outcomes.map((outcome) => (
                <OutcomePill
                  key={outcome}
                  outcome={outcome}
                  count={stats?.counts[outcome] ?? 0}
                  active={activeFilter === outcome}
                  onClick={() => onFilter(outcome)}
                />
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
