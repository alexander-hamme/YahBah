"use client";

import Link from "next/link";
import { ArrowRight, CircleHelp, Eye } from "lucide-react";
import { DeadlineCountdown } from "@/components/deadline-countdown";
import { useApplicationStats, useReviewQueue } from "@/lib/queries";

/** What needs the user now: uncertain submissions to resolve, applications to review. */
export function AttentionStrip() {
  const { data: stats } = useApplicationStats();
  const uncertain = stats?.counts.submit_uncertain ?? 0;
  const awaiting = stats?.counts.awaiting_review ?? 0;

  const queue = useReviewQueue({ enabled: awaiting > 0 });
  const nextDeadline = queue.data?.items.find((i) => i.review_deadline)?.review_deadline ?? null;

  if (uncertain === 0 && awaiting === 0) return null;

  return (
    <section aria-label="Needs your attention" className="grid gap-3 md:grid-cols-2">
      {uncertain > 0 && (
        <Link
          href="/applications?filter=submit_uncertain"
          className="group flex items-center gap-3 rounded-xl border border-orange-400/45 bg-orange-500/10 px-4 py-3 transition-colors hover:bg-orange-500/15"
        >
          <CircleHelp className="h-5 w-5 shrink-0 text-orange-300" aria-hidden />
          <span className="min-w-0 flex-1">
            <span className="block text-sm font-semibold text-orange-100">
              {uncertain} application{uncertain === 1 ? "" : "s"} may have been submitted
            </span>
            <span className="block text-xs text-orange-100/70">
              Tell YahBah what happened. Until then it won&apos;t apply to {uncertain === 1 ? "that job" : "those jobs"} again.
            </span>
          </span>
          <span className="inline-flex items-center gap-1 text-xs font-semibold text-orange-200">
            Resolve
            <ArrowRight className="h-3.5 w-3.5 transition-transform group-hover:translate-x-0.5" aria-hidden />
          </span>
        </Link>
      )}
      {awaiting > 0 && (
        <Link
          href="/review"
          className="group flex items-center gap-3 rounded-xl border border-amber-500/35 bg-amber-500/[0.08] px-4 py-3 transition-colors hover:bg-amber-500/15"
        >
          <Eye className="h-5 w-5 shrink-0 text-amber-300" aria-hidden />
          <span className="min-w-0 flex-1">
            <span className="block text-sm font-semibold text-amber-100">
              {awaiting} waiting for your review
            </span>
            <span className="flex items-center gap-1 text-xs text-amber-100/70">
              {nextDeadline ? (
                <>
                  Next deadline: <DeadlineCountdown deadline={nextDeadline} showIcon={false} />
                </>
              ) : (
                "Nothing is submitted until you approve it."
              )}
            </span>
          </span>
          <span className="inline-flex items-center gap-1 text-xs font-semibold text-amber-200">
            Review
            <ArrowRight className="h-3.5 w-3.5 transition-transform group-hover:translate-x-0.5" aria-hidden />
          </span>
        </Link>
      )}
    </section>
  );
}
