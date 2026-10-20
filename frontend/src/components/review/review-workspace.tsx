"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { CheckCircle2, Inbox, Mail, ThumbsDown } from "lucide-react";
import { ApiErrorMessage } from "@/components/api-error-message";
import { CompanyLogo } from "@/components/company-logo";
import { DeadlineCountdown } from "@/components/deadline-countdown";
import type { Decision } from "@/components/review/decision-bar";
import { ReviewPanel } from "@/components/review/review-panel";
import { useReviewQueue, useRuntimeSettings } from "@/lib/queries";
import { asApiError } from "@/lib/toast";
import type { ApplicationListItem } from "@/lib/types";
import { cn } from "@/lib/utils";

function QueueItem({
  item,
  selected,
  decision,
  onSelect,
}: {
  item: ApplicationListItem;
  selected: boolean;
  decision: Decision | undefined;
  onSelect: () => void;
}) {
  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        aria-current={selected ? "true" : undefined}
        className={cn(
          "flex w-full items-start gap-3 rounded-lg border px-3 py-2.5 text-left transition-colors",
          selected
            ? "border-cyan-400/40 bg-cyan-500/10"
            : "border-transparent hover:border-white/10 hover:bg-white/[0.04]",
          decision && "opacity-55"
        )}
      >
        <CompanyLogo companyWebsite={item.company_website} companyName={item.company} size={28} />
        <span className="min-w-0 flex-1">
          <span className="flex items-baseline justify-between gap-2">
            <span className="truncate text-sm font-semibold">{item.company ?? "Unknown"}</span>
            {decision ? (
              <span className="inline-flex shrink-0 items-center gap-1 text-xs text-muted-foreground">
                {decision === "approved" ? (
                  <CheckCircle2 className="h-3 w-3 text-emerald-400" aria-hidden />
                ) : (
                  <ThumbsDown className="h-3 w-3" aria-hidden />
                )}
                {decision === "approved" ? "Approved" : "Rejected"}
              </span>
            ) : (
              item.review_deadline && <DeadlineCountdown deadline={item.review_deadline} showIcon={false} />
            )}
          </span>
          <span className="block truncate text-xs text-muted-foreground">{item.title ?? "Untitled role"}</span>
          {item.review_round != null && item.review_round > 1 && (
            <span className="mt-0.5 inline-flex rounded border border-cyan-400/40 bg-cyan-500/15 px-1 text-[11px] font-semibold text-cyan-200">
              Round {item.review_round} · new fields
            </span>
          )}
          {item.origin === "discovered" && (
            <span className="mt-0.5 inline-flex items-center gap-1 text-[11px] text-white/35">
              <Mail className="h-3 w-3" aria-hidden />
              From a job alert
            </span>
          )}
        </span>
      </button>
    </li>
  );
}

/** The review queue: awaiting_review runs, soonest deadline first, beside the selected run's review. */
export function ReviewWorkspace() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [decided, setDecided] = useState<ReadonlyMap<string, Decision>>(new Map());
  const { data: settings } = useRuntimeSettings();

  const queue = useReviewQueue();
  const items = queue.data?.items ?? [];
  const waiting = items.filter((i) => !decided.has(i.run_id));

  const requested = searchParams.get("run");
  const selected =
    items.find((i) => i.run_id === requested) ?? (requested ? undefined : waiting[0]);

  function select(runId: string | null) {
    router.replace(runId ? `${pathname}?run=${runId}` : pathname, { scroll: false });
  }

  function handleDecided(runId: string, decision: Decision) {
    const next = new Map(decided).set(runId, decision);
    setDecided(next);
    const index = items.findIndex((i) => i.run_id === runId);
    const after = [...items.slice(index + 1), ...items.slice(0, index)].find((i) => !next.has(i.run_id));
    select(after?.run_id ?? null);
  }

  const windowHours = settings?.review_window_hours;

  return (
    <div className="mx-auto max-w-7xl space-y-5 px-4 py-6 sm:px-6 animate-fade-in">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Review queue</h1>
          <p className="mt-0.5 text-sm text-muted-foreground">
            Approve to submit.{" "}
            {windowHours != null
              ? `Anything not approved within ${windowHours} h of filling expires without being sent.`
              : "Anything not approved before its deadline expires without being sent."}
          </p>
        </div>
        {queue.data && (
          <p className="text-sm text-muted-foreground" aria-live="polite">
            <span className="font-semibold text-foreground">{waiting.length}</span> waiting
          </p>
        )}
      </div>

      {queue.error && (
        <ApiErrorMessage
          error={asApiError(queue.error)}
          title={queue.data ? "Couldn't refresh the queue; showing the last list" : "Couldn't load the review queue"}
        />
      )}

      {queue.isPending ? (
        <div className="grid gap-5 lg:grid-cols-[17rem_minmax(0,1fr)]" aria-busy="true">
          <div className="h-64 animate-pulse rounded-xl bg-white/5" />
          <div className="h-96 animate-pulse rounded-xl bg-white/5" />
        </div>
      ) : items.length === 0 && !queue.error ? (
        <div className="glass-panel flex flex-col items-center gap-2 rounded-xl px-6 py-16 text-center">
          <Inbox className="h-8 w-8 text-muted-foreground" aria-hidden />
          <p className="font-semibold">Nothing to review</p>
          <p className="max-w-md text-sm text-muted-foreground">
            {settings?.submission_mode === "testing"
              ? "New runs are in Testing mode: they're filled, never submitted, and don't wait for review."
              : "Filled applications wait here for your approval before anything is submitted."}
          </p>
        </div>
      ) : (
        <div className="grid gap-5 lg:grid-cols-[17rem_minmax(0,1fr)]">
          <nav aria-label="Applications awaiting review" className="lg:sticky lg:top-20 lg:self-start">
            <ol className="max-h-[40vh] space-y-1 overflow-y-auto lg:max-h-[calc(100vh-8rem)]">
              {items.map((item) => (
                <QueueItem
                  key={item.run_id}
                  item={item}
                  selected={item.run_id === selected?.run_id}
                  decision={decided.get(item.run_id)}
                  onSelect={() => select(item.run_id)}
                />
              ))}
            </ol>
          </nav>

          <div className="glass-panel min-w-0 rounded-xl p-4 sm:p-5">
            {selected ? (
              <ReviewPanel
                key={selected.run_id}
                runId={selected.run_id}
                actions={decided.has(selected.run_id) ? [] : selected.actions}
                onDecided={handleDecided}
              />
            ) : requested ? (
              <div className="py-12 text-center text-sm text-muted-foreground">
                That application isn&apos;t waiting for review any more.{" "}
                <Link href={`/applications/${requested}`} className="font-medium text-cyan-300 hover:underline">
                  Open it
                </Link>
              </div>
            ) : (
              <div className="py-12 text-center text-sm text-muted-foreground">
                All done. Every application in the queue has a decision.
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
