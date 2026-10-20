import {
  Ban,
  CheckCircle2,
  CircleHelp,
  Clock,
  Copy,
  Eye,
  FlaskConical,
  Hourglass,
  Loader2,
  MailCheck,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import { OUTCOME_INFO, OUTCOME_TONE_CLASS } from "@/lib/outcomes";
import type { Outcome } from "@/lib/types";
import { cn } from "@/lib/utils";

const OUTCOME_ICON: Record<Outcome, LucideIcon> = {
  queued: Clock,
  in_progress: Loader2,
  awaiting_review: Eye,
  test_only: FlaskConical,
  email_verified: MailCheck,
  submitted: CheckCircle2,
  submit_uncertain: CircleHelp,
  failed: XCircle,
  cancelled: Ban,
  expired: Hourglass,
  duplicate: Copy,
};

/** The run's server-derived outcome. The label carries the meaning; colour only reinforces it. */
export function OutcomeBadge({ outcome, className }: { outcome: Outcome; className?: string }) {
  const info = OUTCOME_INFO[outcome];
  const Icon = OUTCOME_ICON[outcome];
  return (
    <span
      title={info.description}
      className={cn(
        "inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 py-0.5 text-xs font-semibold",
        OUTCOME_TONE_CLASS[info.tone],
        className
      )}
    >
      <Icon
        className={cn("h-3 w-3", outcome === "in_progress" && "animate-spin motion-reduce:animate-none")}
        aria-hidden
      />
      {info.label}
    </span>
  );
}
