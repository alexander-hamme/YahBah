"use client";

import Link from "next/link";
import { ChevronRight, ArrowUp, ArrowDown } from "lucide-react";
import { CompanyLogo } from "@/components/company-logo";
import { DeadlineCountdown } from "@/components/deadline-countdown";
import { OutcomeBadge } from "@/components/outcome-badge";
import { RunActions } from "@/components/run-actions";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { failureReasonInfo } from "@/lib/failure-reasons";
import { timeAgo } from "@/lib/time";
import type { ApplicationListItem } from "@/lib/types";

export function formatStep(step: string | null): string {
  if (!step) return "-";
  return step.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

/** The one-line "what's going on" for a row, chosen by its outcome. */
function OutcomeDetail({ item }: { item: ApplicationListItem }) {
  switch (item.outcome) {
    case "queued":
    case "in_progress":
      return <>{formatStep(item.current_state)}</>;
    case "awaiting_review":
      return item.review_deadline ? <DeadlineCountdown deadline={item.review_deadline} /> : <>Waiting for you</>;
    case "test_only":
      return <>Filled, not submitted</>;
    case "submitted":
    case "email_verified":
      return <span title={item.completed_at ? new Date(item.completed_at).toLocaleString() : undefined}>{timeAgo(item.completed_at)}</span>;
    default: {
      const reason = failureReasonInfo(item.failure_reason);
      return (
        <span className="line-clamp-2" title={item.error_message ?? reason?.detail}>
          {reason?.title ?? item.error_message ?? "-"}
        </span>
      );
    }
  }
}

interface SortableColumnProps {
  label: string;
  sortKey: string;
  currentSort: string;
  onSort: (sort: string) => void;
}

function SortableColumn({ label, sortKey, currentSort, onSort }: SortableColumnProps) {
  const isAsc = currentSort === `${sortKey}_asc`;
  const isDesc = currentSort === `${sortKey}_desc`;
  const isActive = isAsc || isDesc;

  function handleClick() {
    if (isDesc) {
      onSort(`${sortKey}_asc`);
    } else {
      onSort(`${sortKey}_desc`);
    }
  }

  return (
    <TableHead
      className="font-semibold cursor-pointer select-none text-muted-foreground hover:text-foreground transition-colors"
      onClick={handleClick}
    >
      <span className="inline-flex items-center gap-1">
        {label}
        {isActive ? (
          isAsc ? (
            <ArrowUp className="h-3 w-3 text-cyan-400" />
          ) : (
            <ArrowDown className="h-3 w-3 text-cyan-400" />
          )
        ) : null}
      </span>
    </TableHead>
  );
}

export function ApplicationList({
  items,
  sort,
  onSort,
  filtered = false,
  selectedIds,
  onToggleSelect,
  onToggleAll,
}: {
  items: ApplicationListItem[];
  sort: string;
  onSort: (sort: string) => void;
  filtered?: boolean;
  selectedIds?: ReadonlySet<string>;
  onToggleSelect?: (id: string) => void;
  onToggleAll?: (ids: string[], selectAll: boolean) => void;
}) {
  if (items.length === 0 && filtered) {
    return (
      <div className="glass-panel rounded-xl py-12 text-center text-sm text-muted-foreground">
        No applications match this filter.
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-16">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src="/YahBah_Logo.png"
          alt="YahBah"
          width={220}
          height={220}
          className="animate-float mb-4 drop-shadow-lg"
        />
        <p className="text-base font-semibold text-foreground">No applications yet</p>
        <p className="text-sm text-muted-foreground mt-1">Paste a job URL above to get started</p>
      </div>
    );
  }

  // Only runs the server lets the user delete can be selected for bulk delete.
  const deletable = items.filter((i) => i.actions.includes("delete")).map((i) => i.run_id);
  const selectable = !!selectedIds && !!onToggleSelect && !!onToggleAll && deletable.length > 0;
  const allSelected = selectable && deletable.every((id) => selectedIds!.has(id));
  const someSelected = selectable && deletable.some((id) => selectedIds!.has(id));

  return (
    <div className="glass-panel rounded-xl overflow-hidden">
      <Table>
        <TableHeader>
          <TableRow className="border-b border-white/5 bg-white/[0.02]">
            {selectable && (
              <TableHead className="w-10 pl-3">
                <input
                  type="checkbox"
                  aria-label="Select all deletable runs on this page"
                  checked={allSelected}
                  ref={el => { if (el) el.indeterminate = someSelected && !allSelected; }}
                  onChange={() => onToggleAll!(deletable, !allSelected)}
                  className="h-4 w-4 rounded border-white/20 bg-white/5 accent-red-500 cursor-pointer"
                />
              </TableHead>
            )}
            <SortableColumn label="Company" sortKey="company" currentSort={sort} onSort={onSort} />
            <SortableColumn label="Role" sortKey="title" currentSort={sort} onSort={onSort} />
            <TableHead className="font-semibold text-muted-foreground">Outcome</TableHead>
            <TableHead className="font-semibold text-muted-foreground">Detail</TableHead>
            <SortableColumn label="Match" sortKey="match_score" currentSort={sort} onSort={onSort} />
            <SortableColumn label="Created" sortKey="created_at" currentSort={sort} onSort={onSort} />
            <SortableColumn label="Updated" sortKey="updated_at" currentSort={sort} onSort={onSort} />
            <TableHead className="w-24 sticky right-0 bg-[#0c1018]" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((item) => {
            const isSelected = selectable && selectedIds!.has(item.run_id);
            return (
            <TableRow
              key={item.run_id}
              className={`group cursor-pointer border-b border-white/[0.03] transition-all border-l-2 ${
                isSelected
                  ? "bg-red-500/[0.06] border-l-red-500/50"
                  : "hover:bg-white/[0.04] border-l-transparent hover:border-l-cyan-400 hover:shadow-[inset_0_0_20px_rgba(56,189,248,0.03)]"
              }`}
            >
              {selectable && (
                <TableCell className="pl-3 w-10">
                  {item.actions.includes("delete") && (
                    <input
                      type="checkbox"
                      aria-label={`Select ${item.company ?? "run"} for deletion`}
                      checked={isSelected}
                      onChange={() => onToggleSelect!(item.run_id)}
                      className="h-4 w-4 rounded border-white/20 bg-white/5 accent-red-500 cursor-pointer"
                    />
                  )}
                </TableCell>
              )}
              <TableCell>
                <Link
                  href={`/applications/${item.run_id}`}
                  className="flex items-center gap-2.5 font-semibold text-foreground group-hover:text-cyan-400 transition-colors"
                >
                  <CompanyLogo
                    companyWebsite={item.company_website}
                    companyName={item.company}
                    size={28}
                  />
                  {item.company ?? "Unknown"}
                </Link>
              </TableCell>
              <TableCell className="max-w-[200px]">
                <Link
                  href={`/applications/${item.run_id}`}
                  className="block text-sm text-muted-foreground group-hover:text-foreground transition-colors truncate"
                  title={item.title ?? "Untitled"}
                >
                  {item.title ?? "Untitled"}
                </Link>
              </TableCell>
              <TableCell>
                <Link href={`/applications/${item.run_id}`} className="inline-flex items-center gap-1.5">
                  <OutcomeBadge outcome={item.outcome} />
                </Link>
              </TableCell>
              <TableCell className="max-w-[220px] whitespace-normal text-sm text-muted-foreground">
                <OutcomeDetail item={item} />
              </TableCell>
              <TableCell className="text-sm">
                {item.match_score != null ? (
                  <span
                    className={`font-bold tabular-nums ${
                      item.match_score >= 85
                        ? "text-emerald-400"
                        : item.match_score >= 75
                        ? "text-teal-400"
                        : item.match_score >= 50
                        ? "text-cyan-400"
                        : item.match_score >= 35
                        ? "text-amber-400"
                        : "text-red-400"
                    }`}
                  >
                    {item.match_score}%
                  </span>
                ) : (
                  <span className="text-muted-foreground">-</span>
                )}
              </TableCell>
              <TableCell
                className="text-sm text-muted-foreground"
                title={new Date(item.created_at).toLocaleString()}
              >
                {timeAgo(item.created_at)}
              </TableCell>
              <TableCell
                className="text-sm text-muted-foreground"
                title={new Date(item.updated_at).toLocaleString()}
              >
                {timeAgo(item.updated_at)}
              </TableCell>
              <TableCell className="sticky right-0 bg-[#0a0e1a] group-hover:bg-[#0f1420]">
                <div className="flex items-center gap-1">
                  <RunActions runId={item.run_id} actions={item.actions} compact />
                  <ChevronRight className="h-4 w-4 text-white/10 group-hover:text-cyan-400 transition-colors" />
                </div>
              </TableCell>
            </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}
