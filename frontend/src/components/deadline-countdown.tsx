"use client";

import { Timer } from "lucide-react";
import { countdown, formatDateTime, useNow, type Urgency } from "@/lib/time";
import { cn } from "@/lib/utils";

const URGENCY_CLASS: Record<Urgency, string> = {
  normal: "text-muted-foreground",
  soon: "text-amber-300",
  urgent: "text-red-300 font-semibold",
  overdue: "text-red-300 font-semibold",
};

/** "31h 12m left" until a review deadline; the absolute time is in the tooltip. */
export function DeadlineCountdown({
  deadline,
  className,
  showIcon = true,
}: {
  deadline: string;
  className?: string;
  showIcon?: boolean;
}) {
  const now = useNow();
  const { text, urgency } = countdown(deadline, now);
  const absolute = formatDateTime(deadline);
  return (
    <time
      dateTime={deadline}
      title={`Review deadline: ${absolute}. Unapproved applications expire without being sent.`}
      className={cn("inline-flex items-center gap-1 whitespace-nowrap tabular-nums text-xs", URGENCY_CLASS[urgency], className)}
    >
      {showIcon && <Timer className="h-3 w-3" aria-hidden />}
      {text}
      <span className="sr-only">, deadline {absolute}</span>
    </time>
  );
}
