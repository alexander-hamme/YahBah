"use client";

import Link from "next/link";
import { XCircle } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { getApplicationStats } from "@/lib/api";

export function FailedBadge() {
  const { data } = useQuery({
    queryKey: ["application-stats"],
    queryFn: getApplicationStats,
    refetchInterval: 30_000,
    staleTime: 30_000,
  });

  const failedCount = data?.failed ?? 0;
  if (failedCount === 0) return null;

  return (
    <Link
      href="/applications?status=FAILED"
      className="flex items-center gap-1 px-1.5 py-0.5 rounded-full
                 bg-red-500/15 border border-red-500/25 text-red-400
                 text-xs font-semibold tabular-nums
                 hover:bg-red-500/25 transition-colors"
      title={`${failedCount} failed application${failedCount !== 1 ? "s" : ""}`}
    >
      <XCircle className="h-3 w-3" />
      {failedCount}
    </Link>
  );
}
