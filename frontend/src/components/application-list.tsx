"use client";

import Link from "next/link";
import { ChevronRight, ArrowUp, ArrowDown } from "lucide-react";
import { CompanyLogo } from "@/components/company-logo";
import { RunActions } from "@/components/run-actions";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { StatusBadge } from "@/components/status-badge";
import type { ApplicationListItem } from "@/lib/types";

function timeAgo(iso: string): string {
  const seconds = Math.floor(
    (Date.now() - new Date(iso).getTime()) / 1000
  );
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  return new Date(iso).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
  });
}

function formatStep(step: string | null): string {
  if (!step) return "-";
  return step.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
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
}: {
  items: ApplicationListItem[];
  sort: string;
  onSort: (sort: string) => void;
}) {
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

  return (
    <div className="glass-panel rounded-xl overflow-hidden">
      <Table>
        <TableHeader>
          <TableRow className="border-b border-white/5 bg-white/[0.02]">
            <SortableColumn label="Company" sortKey="company" currentSort={sort} onSort={onSort} />
            <SortableColumn label="Role" sortKey="title" currentSort={sort} onSort={onSort} />
            <SortableColumn label="Status" sortKey="status" currentSort={sort} onSort={onSort} />
            <SortableColumn label="Current Step" sortKey="current_state" currentSort={sort} onSort={onSort} />
            <SortableColumn label="Created" sortKey="created_at" currentSort={sort} onSort={onSort} />
            <SortableColumn label="Updated" sortKey="updated_at" currentSort={sort} onSort={onSort} />
            <TableHead className="w-24 sticky right-0 bg-[#0c1018]" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((item) => (
            <TableRow
              key={item.run_id}
              className="group cursor-pointer border-b border-white/[0.03] hover:bg-white/[0.04] transition-all border-l-2 border-l-transparent hover:border-l-cyan-400 hover:shadow-[inset_0_0_20px_rgba(56,189,248,0.03)]"
            >
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
                <Link href={`/applications/${item.run_id}`}>
                  <StatusBadge status={item.status} />
                </Link>
              </TableCell>
              <TableCell className="text-sm text-muted-foreground">
                {formatStep(item.current_state)}
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
                  <RunActions runId={item.run_id} status={item.status} compact />
                  <ChevronRight className="h-4 w-4 text-white/10 group-hover:text-cyan-400 transition-colors" />
                </div>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
