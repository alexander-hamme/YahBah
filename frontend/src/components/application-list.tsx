"use client";

import Link from "next/link";
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

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function formatStep(step: string | null): string {
  if (!step) return "-";
  return step.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function ApplicationList({ items }: { items: ApplicationListItem[] }) {
  if (items.length === 0) {
    return (
      <div className="text-center py-12 text-gray-500">
        No applications found.
      </div>
    );
  }

  return (
    <div className="rounded-md border bg-white dark:bg-gray-900">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Company</TableHead>
            <TableHead>Role</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Current Step</TableHead>
            <TableHead>Created</TableHead>
            <TableHead>Updated</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((item) => (
            <TableRow key={item.run_id} className="cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800">
              <TableCell>
                <Link
                  href={`/applications/${item.run_id}`}
                  className="block font-medium"
                >
                  {item.company ?? "Unknown"}
                </Link>
              </TableCell>
              <TableCell>
                <Link href={`/applications/${item.run_id}`} className="block">
                  {item.title ?? "Untitled"}
                </Link>
              </TableCell>
              <TableCell>
                <Link href={`/applications/${item.run_id}`}>
                  <StatusBadge status={item.status} />
                </Link>
              </TableCell>
              <TableCell className="text-sm text-gray-500">
                {formatStep(item.current_state)}
              </TableCell>
              <TableCell className="text-sm text-gray-500">
                {formatDate(item.created_at)}
              </TableCell>
              <TableCell className="text-sm text-gray-500">
                {formatDate(item.updated_at)}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
