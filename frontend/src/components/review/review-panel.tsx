"use client";

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, ExternalLink, Info, RefreshCw } from "lucide-react";
import { ApiErrorMessage } from "@/components/api-error-message";
import { DeadlineCountdown } from "@/components/deadline-countdown";
import {
  AnswerEvidenceList,
  GeneratedChip,
  evidenceFlags,
  needsLook,
  normalizeEvidenceRow,
} from "@/components/review/answer-evidence";
import { DecisionBar, type Decision } from "@/components/review/decision-bar";
import { ReviewScreenshot } from "@/components/review/review-screenshot";
import { artifactDownloadUrl, getRunReview } from "@/lib/api";
import { asApiError, toast } from "@/lib/toast";
import type { FillEvidenceRow, ReviewPayload, RunAction } from "@/lib/types";
import { cn } from "@/lib/utils";

export function reviewQueryKey(runId: string) {
  return ["run-review", runId] as const;
}

/** Only http(s) URLs become links: a job URL is data from outside. */
export function httpUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.href : null;
  } catch {
    return null;
  }
}

export interface Notice {
  code: string;
  round: number;
}

/** What a 409 on a decision means for the user, given the review as refreshed. */
export function noticeText(notice: Notice, review: ReviewPayload | undefined): string {
  if (notice.code === "stale_review_round") {
    return review && review.review_round > notice.round
      ? `This application changed while you were reviewing it: it's now round ${review.review_round}. Check the highlighted new fields, then decide again.`
      : "This review is out of date. It has been refreshed; check it again before deciding.";
  }
  return "This application is no longer waiting for review, so the decision wasn't recorded.";
}

function Summary({
  answers,
  newFields,
  onlyFlagged,
  onToggle,
}: {
  answers: readonly FillEvidenceRow[];
  newFields: readonly FillEvidenceRow[];
  onlyFlagged: boolean;
  onToggle: () => void;
}) {
  const all = [
    ...newFields.map((r) => evidenceFlags(r, true)),
    ...answers.map((r) => evidenceFlags(r)),
  ];
  const count = (pick: (f: ReturnType<typeof evidenceFlags>) => boolean) => all.filter(pick).length;
  const parts = [
    [count((f) => f.isNew), "new"],
    [count((f) => f.mismatch), "differ on the page"],
    [count((f) => f.blankRequired), "required but blank"],
    [count((f) => f.unbacked), "without a stored basis"],
    [count((f) => f.generated), "generated"],
  ] as const;
  const flagged = count(needsLook);
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
      <span className="font-semibold">{all.length} answers</span>
      {parts
        .filter(([n]) => n > 0)
        .map(([n, label]) => (
          <span key={label} className="text-muted-foreground">
            · <span className="font-semibold text-foreground">{n}</span> {label}
          </span>
        ))}
      {flagged > 0 && flagged < all.length && (
        <button
          type="button"
          onClick={onToggle}
          aria-pressed={onlyFlagged}
          className="ml-auto rounded-md border border-white/10 px-2 py-0.5 text-xs font-medium text-cyan-300 hover:bg-white/5"
        >
          {onlyFlagged ? `Show all ${all.length}` : `Only the ${flagged} to check`}
        </button>
      )}
    </div>
  );
}

/** The generated cover letter, clamped with a control to read it whole. */
export function CoverLetter({ runId, letter }: { runId: string; letter: NonNullable<ReviewPayload["cover_letter"]> }) {
  const [open, setOpen] = useState(false);
  return (
    <section aria-labelledby={`cover-${runId}`} className="space-y-2">
      <div className="flex items-center gap-2">
        <h3 id={`cover-${runId}`} className="text-sm font-semibold">
          Cover letter
        </h3>
        <GeneratedChip />
        {letter.artifact_id && (
          <a
            href={artifactDownloadUrl(runId, letter.artifact_id)}
            target="_blank"
            rel="noopener noreferrer"
            className="ml-auto inline-flex items-center gap-1 text-xs font-medium text-cyan-300 hover:text-cyan-200"
          >
            <Download className="h-3 w-3" aria-hidden />
            Download
          </a>
        )}
      </div>
      {letter.text ? (
        <div className="rounded-lg border border-white/[0.07] bg-white/[0.02] px-4 py-3">
          <p className={cn("whitespace-pre-wrap text-sm leading-relaxed text-foreground/90", !open && "line-clamp-6")}>
            {letter.text}
          </p>
          <button
            type="button"
            onClick={() => setOpen(!open)}
            aria-expanded={open}
            className="mt-1 text-xs font-medium text-cyan-300 hover:text-cyan-200"
          >
            {open ? "Show less" : "Read the whole letter"}
          </button>
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">The letter&apos;s text wasn&apos;t saved; download it to read it.</p>
      )}
    </section>
  );
}

/**
 * Everything needed to approve or reject one filled application: each answer with
 * its basis and what the page read back, new fields, the full-page screenshot and
 * the cover letter, then the decision for exactly this review round.
 */
export function ReviewPanel({
  runId,
  actions,
  onDecided,
}: {
  runId: string;
  /** The run's allowed actions: the decision bar offers approve/reject only when listed. */
  actions: readonly RunAction[];
  onDecided?: (runId: string, decision: Decision) => void;
}) {
  const queryClient = useQueryClient();
  const [notice, setNotice] = useState<Notice | null>(null);
  const [onlyFlagged, setOnlyFlagged] = useState(false);
  const { data: review, error, isPending, refetch, isFetching } = useQuery({
    queryKey: reviewQueryKey(runId),
    queryFn: () => getRunReview(runId),
    staleTime: 0,
  });

  function refreshRunQueries() {
    queryClient.invalidateQueries({ queryKey: ["run", runId] });
    queryClient.invalidateQueries({ queryKey: ["applications"] });
    queryClient.invalidateQueries({ queryKey: ["application-stats"] });
  }

  if (isPending) {
    return (
      <div className="space-y-3" aria-busy="true" aria-label="Loading the review">
        <div className="h-7 w-2/3 animate-pulse rounded bg-white/5" />
        <div className="h-64 animate-pulse rounded-lg bg-white/5" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="space-y-2">
        <ApiErrorMessage error={asApiError(error)} title="Couldn't load the review" />
        <button
          type="button"
          onClick={() => refetch()}
          className="inline-flex items-center gap-1 text-xs font-medium text-cyan-300 hover:text-cyan-200"
        >
          <RefreshCw className={cn("h-3 w-3", isFetching && "animate-spin")} aria-hidden />
          Try again
        </button>
      </div>
    );
  }

  // Rows come from the fill evidence artifact as written; fill in anything an older writer left out.
  const newFields = review.new_fields.map((r, i) => normalizeEvidenceRow({ ...r }, i));
  const newKeys = new Set(newFields.map((r) => r.key));
  const answers = review.answers.map((r, i) => normalizeEvidenceRow({ ...r }, i)).filter((r) => !newKeys.has(r.key));
  const jobLink = httpUrl(review.job.url);
  const company = review.job.company;

  return (
    <article aria-labelledby={`review-title-${runId}`} className="space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <div className="min-w-0">
          <h2 id={`review-title-${runId}`} className="text-lg font-semibold leading-tight">
            {company ?? "Unknown company"}
            <span className="font-normal text-muted-foreground"> · {review.job.title ?? "Untitled role"}</span>
          </h2>
          {jobLink && (
            <a
              href={jobLink}
              target="_blank"
              rel="noopener noreferrer"
              className="mt-0.5 inline-flex max-w-full items-center gap-1 truncate text-xs text-muted-foreground hover:text-cyan-300"
            >
              <ExternalLink className="h-3 w-3 shrink-0" aria-hidden />
              <span className="truncate">{jobLink}</span>
            </a>
          )}
        </div>
        <div className="flex items-center gap-3">
          {review.review_round > 1 && (
            <span className="rounded-md border border-cyan-400/40 bg-cyan-500/15 px-2 py-0.5 text-xs font-semibold text-cyan-200">
              Round {review.review_round}
            </span>
          )}
          {review.review_deadline && <DeadlineCountdown deadline={review.review_deadline} className="text-sm" />}
        </div>
      </header>

      {notice && (
        <div role="alert" className="flex gap-2 rounded-lg border border-amber-500/35 bg-amber-500/10 px-3 py-2 text-sm text-amber-100">
          <Info className="mt-0.5 h-4 w-4 shrink-0 text-amber-300" aria-hidden />
          {noticeText(notice, review)}
        </div>
      )}

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <div className="min-w-0 space-y-4">
          <Summary
            answers={answers}
            newFields={newFields}
            onlyFlagged={onlyFlagged}
            onToggle={() => setOnlyFlagged(!onlyFlagged)}
          />

          {newFields.length > 0 && (
            <section aria-labelledby={`new-${runId}`} className="space-y-2">
              <h3 id={`new-${runId}`} className="text-sm font-semibold text-cyan-200">
                New in round {review.review_round}: the form asked for these after your last approval
              </h3>
              <AnswerEvidenceList rows={newFields} newKeys={newKeys} label="New fields" />
            </section>
          )}

          <AnswerEvidenceList
            rows={answers}
            onlyFlagged={onlyFlagged}
            label={newFields.length > 0 ? "Previously approved answers" : "Answers"}
          />

          {review.cover_letter && <CoverLetter runId={runId} letter={review.cover_letter} />}
        </div>

        <aside className="min-w-0 lg:sticky lg:top-20 lg:self-start" aria-label="Screenshot">
          <ReviewScreenshot
            runId={runId}
            artifactId={review.screenshot_artifact_id}
            reviewRound={review.review_round}
          />
        </aside>
      </div>

      <div className="sticky bottom-3 z-10">
        <DecisionBar
          key={`${runId}:${review.review_round}`}
          runId={runId}
          reviewRound={review.review_round}
          company={company}
          canApprove={actions.includes("approve")}
          canReject={actions.includes("reject")}
          onDecided={(decision) => {
            setNotice(null);
            toast.success(
              decision === "approved" ? `Approved: ${company ?? "application"}` : "Rejected",
              decision === "approved"
                ? "YahBah re-fills the approved answers and submits."
                : "Nothing was submitted."
            );
            refreshRunQueries();
            onDecided?.(runId, decision);
          }}
          onConflict={(code) => {
            setNotice({ code, round: review.review_round });
            refetch();
            refreshRunQueries();
          }}
        />
      </div>
    </article>
  );
}
