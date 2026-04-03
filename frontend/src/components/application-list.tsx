"use client";

import Link from "next/link";
import { ChevronRight } from "lucide-react";
import { CompanyLogo } from "@/components/company-logo";
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

export function ApplicationList({ items }: { items: ApplicationListItem[] }) {
  if (items.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src="/YahBah_Logo.png"
          alt="YahBah"
          width={220}
          height={220}
          className="animate-float mb-4 drop-shadow-lg"
        />
        <p className="text-base font-semibold text-foreground">No applications yet</p>
        <p className="text-sm mt-1">Paste a job URL above to get started</p>
      </div>
    );
  }

  return (
    <div className="rounded-xl border bg-card shadow-sm overflow-hidden">
      <Table>
        <TableHeader>
          <TableRow className="bg-muted/50">
            <TableHead className="font-semibold">Company</TableHead>
            <TableHead className="font-semibold">Role</TableHead>
            <TableHead className="font-semibold">Status</TableHead>
            <TableHead className="font-semibold">Current Step</TableHead>
            <TableHead className="font-semibold">Created</TableHead>
            <TableHead className="font-semibold">Updated</TableHead>
            <TableHead className="w-8" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((item) => (
            <TableRow
              key={item.run_id}
              className="group cursor-pointer hover:bg-accent/50 transition-colors border-l-2 border-l-transparent hover:border-l-primary"
            >
              <TableCell>
                <Link
                  href={`/applications/${item.run_id}`}
                  className="flex items-center gap-2.5 font-semibold text-foreground group-hover:text-primary transition-colors"
                >
                  <CompanyLogo
                    companyWebsite={item.company_website}
                    companyName={item.company}
                    size={28}
                  />
                  {item.company ?? "Unknown"}
                </Link>
              </TableCell>
              <TableCell>
                <Link
                  href={`/applications/${item.run_id}`}
                  className="block text-sm text-muted-foreground group-hover:text-foreground transition-colors"
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
              <TableCell>
                <ChevronRight className="h-4 w-4 text-muted-foreground/40 group-hover:text-primary transition-colors" />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
