import { useEffect, useState } from "react";

export function timeAgo(iso: string | null, now: number = Date.now()): string {
  if (!iso) return "-";
  const seconds = Math.floor((now - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

export function formatDateTime(iso: string | null): string {
  if (!iso) return "-";
  return new Date(iso).toLocaleString("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export type Urgency = "normal" | "soon" | "urgent" | "overdue";

export interface Countdown {
  text: string;
  urgency: Urgency;
}

const HOUR = 3_600_000;

/** Time left until a deadline, compact ("31h 12m left"), with how urgent it is. */
export function countdown(deadlineIso: string, now: number): Countdown {
  const ms = new Date(deadlineIso).getTime() - now;
  if (ms <= 0) return { text: "Deadline passed", urgency: "overdue" };
  const totalMinutes = Math.floor(ms / 60_000);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  let text: string;
  if (hours === 0) text = `${Math.max(minutes, 1)}m left`;
  else if (hours < 48) text = `${hours}h ${minutes}m left`;
  else text = `${Math.floor(hours / 24)}d ${hours % 24}h left`;
  const urgency: Urgency = ms < HOUR ? "urgent" : ms < 6 * HOUR ? "soon" : "normal";
  return { text, urgency };
}

/** The current time, refreshed every `intervalMs` (for countdowns and "x ago"). */
export function useNow(intervalMs = 30_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}
