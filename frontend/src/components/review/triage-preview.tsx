"use client";

import { useEffect, useId, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { Check, CheckCircle2, ExternalLink, HelpCircle, Info, Loader2, Maximize2, RefreshCw, ThumbsDown, Undo2 } from "lucide-react";
import { ApiErrorMessage } from "@/components/api-error-message";
import { DeadlineCountdown } from "@/components/deadline-countdown";
import {
  AnswerValue,
  FlagChips,
  evidenceFlags,
  needsLook,
  normalizeEvidenceRow,
} from "@/components/review/answer-evidence";
import type { Decision } from "@/components/review/decision-bar";
import { CoverLetter, httpUrl, noticeText, reviewQueryKey } from "@/components/review/review-panel";
import { ReviewScreenshot } from "@/components/review/review-screenshot";
import {
  DWELL_MS,
  isShortcutBlocked,
  type PendingDecision,
  type TriageProblem,
} from "@/components/review/use-triage-decisions";
import { Button } from "@/components/ui/button";
import { getRunReview } from "@/lib/api";
import { asApiError } from "@/lib/toast";
import type { FillEvidenceRow, ReviewPayload, RunAction } from "@/lib/types";
import { cn } from "@/lib/utils";

// Yup / Nope fade in over the dwell instead of popping: visibly "arming", not lagging.
const ARMING = "transition-opacity duration-500 motion-reduce:transition-none";

export function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <kbd
      aria-hidden
      className="ml-1 rounded border border-current/25 px-1 font-mono text-[10px] leading-4 opacity-70"
    >
      {children}
    </kbd>
  );
}

/** Whole seconds until `at`, refreshed while it counts down. */
export function SecondsLeft({ at }: { at: number }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(id);
  }, []);
  return <span className="tabular-nums">{Math.max(0, Math.ceil((at - now) / 1000))} s</span>;
}

/** Counts the times the page became visible; null while it is hidden. */
function useShownEpoch(): number | null {
  const [state, setState] = useState(() => ({
    epoch: 0,
    hidden: typeof document !== "undefined" && document.visibilityState === "hidden",
  }));
  useEffect(() => {
    const onVisibility = () =>
      setState((prev) => ({ epoch: prev.epoch + 1, hidden: document.visibilityState === "hidden" }));
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, []);
  return state.hidden ? null : state.epoch;
}

function dropText(problem: Extract<TriageProblem, { kind: "dropped" }>, review: ReviewPayload): string {
  const what = problem.decision === "approved" ? "approval" : "rejection";
  switch (problem.why) {
    case "round_changed":
      return `Not sent: this application changed to round ${review.review_round} during the undo window. Check what's new, then decide again.`;
    case "left_queue":
      return `Not sent: it stopped waiting for review before your ${what} went out.`;
    case "left_page":
      return `Not sent: you left the page before the undo window closed. Decide again when you're ready.`;
    case "stale":
      return "Not sent: the review needed reloading. Check it again before deciding.";
  }
}

function ProblemNotice({ runId, problem, review }: { runId: string; problem: TriageProblem; review: ReviewPayload }) {
  if (problem.kind === "error") {
    return <ApiErrorMessage error={problem.error} title="The decision wasn't recorded" />;
  }
  if (problem.kind === "unknown") {
    return (
      <div role="alert" className="flex gap-2 rounded-lg border border-amber-500/35 bg-amber-500/10 px-3 py-2 text-sm text-amber-100">
        <HelpCircle className="mt-0.5 h-4 w-4 shrink-0 text-amber-300" aria-hidden />
        <p>
          Couldn&apos;t confirm your {problem.decision === "approved" ? "approval" : "rejection"} ({problem.error.message}). It
          may have been recorded.{" "}
          <Link href={`/applications/${runId}`} className="font-semibold text-cyan-300 hover:underline">
            Open the run
          </Link>{" "}
          to see whether it was, before deciding again.
        </p>
      </div>
    );
  }
  const text =
    problem.kind === "conflict"
      ? noticeText({ code: problem.code, round: problem.round }, review)
      : dropText(problem, review);
  return (
    <div role="alert" className="flex gap-2 rounded-lg border border-amber-500/35 bg-amber-500/10 px-3 py-2 text-sm text-amber-100">
      <Info className="mt-0.5 h-4 w-4 shrink-0 text-amber-300" aria-hidden />
      {text}
    </div>
  );
}

function FlaggedRow({ row, isNew }: { row: FillEvidenceRow; isNew: boolean }) {
  const flags = evidenceFlags(row, isNew);
  return (
    <li
      data-flagged="true"
      className={cn(
        "space-y-1 border-b border-white/[0.05] px-3 py-2.5 text-sm last:border-b-0",
        flags.mismatch || flags.blankRequired ? "bg-amber-500/[0.07]" : flags.isNew ? "bg-cyan-500/[0.06]" : undefined
      )}
    >
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="break-words font-medium">
          {row.label}
          {row.required && (
            <span className="ml-0.5 text-red-300" title="Required">
              *<span className="sr-only"> (required)</span>
            </span>
          )}
        </span>
        <FlagChips flags={flags} />
      </div>
      <div className="text-foreground/90">
        <span className="sr-only">Answer: </span>
        <AnswerValue row={row} full />
      </div>
      {flags.mismatch && (
        <p className="break-words text-xs text-amber-200">
          <span className="font-semibold">Page shows: </span>
          {row.page_value ? <span className="whitespace-pre-wrap">{row.page_value}</span> : <span className="italic">nothing</span>}
        </p>
      )}
    </li>
  );
}

function FineRows({ rows }: { rows: readonly FillEvidenceRow[] }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  if (rows.length === 0) return null;
  return (
    <div className="text-sm">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        aria-controls={id}
        className="inline-flex items-center gap-1.5 text-muted-foreground hover:text-foreground"
      >
        <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400" aria-hidden />
        {rows.length} more {rows.length === 1 ? "answer comes" : "answers come"} from stored facts and read back OK
        <span className="text-xs font-medium text-cyan-300">{open ? "Hide" : "Show"}</span>
      </button>
      {open && (
        <dl id={id} className="mt-1.5 grid grid-cols-[minmax(0,12rem)_minmax(0,1fr)] gap-x-4 gap-y-1 pl-5 text-xs">
          {rows.map((r) => (
            <div key={r.key} className="contents">
              <dt className="truncate text-muted-foreground" title={r.label}>
                {r.label}
              </dt>
              <dd className="truncate" title={r.answer ?? undefined}>
                {r.answer ?? "—"}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

/**
 * Enough of one run's review to say yup or nope: every flagged answer (generated
 * text in full), a screenshot thumbnail and the start of the cover letter. A
 * decision uses the round of the payload rendered here, and only once it has.
 */
export function TriagePreview({
  runId,
  actions,
  pending,
  decided,
  problem,
  autoFocus,
  onDecide,
  onUndo,
  onOpenFull,
}: {
  runId: string;
  actions: readonly RunAction[];
  pending: PendingDecision | undefined;
  decided: Decision | undefined;
  problem: TriageProblem | undefined;
  autoFocus: boolean;
  onDecide: (decision: Decision, round: number, reason: string | null, company: string | null) => void;
  onUndo: () => void;
  onOpenFull: () => void;
}) {
  const id = useId();
  const rootRef = useRef<HTMLElement>(null);
  const [withReason, setWithReason] = useState(false);
  const [reason, setReason] = useState("");
  const { data, error, isPending, refetch, isFetching, errorUpdateCount } = useQuery({
    queryKey: reviewQueryKey(runId),
    queryFn: () => getRunReview(runId),
    staleTime: 0,
  });
  // What is on screen: a refetch error keeps `data`, but the body then shows only the error.
  const review = data && !error ? data : undefined;

  useEffect(() => {
    if (autoFocus) rootRef.current?.focus({ preventScroll: true });
  }, [autoFocus]);

  // The dwell: decisions arm DWELL_MS after this payload (this round, after any error) is on
  // screen, so a held or doubled key or click can't carry over to a preview just shown.
  // Only visible time counts: hiding the tab disarms, and the dwell restarts on return.
  const shownEpoch = useShownEpoch();
  const [armedFor, setArmedFor] = useState<{ review: ReviewPayload; errors: number; epoch: number } | null>(null);
  useEffect(() => {
    if (!review || shownEpoch === null) return;
    const timer = setTimeout(() => setArmedFor({ review, errors: errorUpdateCount, epoch: shownEpoch }), DWELL_MS);
    return () => clearTimeout(timer);
  }, [review, errorUpdateCount, shownEpoch]);
  const armed =
    !!review &&
    armedFor?.review === review &&
    armedFor.errors === errorUpdateCount &&
    armedFor.epoch === shownEpoch;

  // The first round this preview showed: a later one arrived while the user was looking.
  const [firstRound, setFirstRound] = useState<number | null>(null);
  if (review && firstRound === null) setFirstRound(review.review_round);
  const roundChanged = !!review && firstRound !== null && review.review_round !== firstRound;

  const open = !!review && !pending && !decided;
  const canApprove = open && actions.includes("approve");
  const canReject = open && actions.includes("reject");

  // y / n / o / Enter act on the rendered payload only: the listener exists once it has rendered.
  useEffect(() => {
    if (!review) return;
    const { review_round: round, job } = review;
    function onKey(event: KeyboardEvent) {
      if (isShortcutBlocked(event)) return;
      const key = event.key.toLowerCase();
      if ((key === "y" || key === "n") && event.repeat) return; // a held key never decides
      if (key === "y" && canApprove && armed) onDecide("approved", round, null, job.company);
      else if (key === "n" && canReject && armed) onDecide("rejected", round, null, job.company);
      else if (key === "o") onOpenFull();
      else if (key === "enter") {
        // Enter on a focused control keeps its own meaning.
        const t = event.target;
        if (t instanceof HTMLElement && t.closest("button, a, summary")) return;
        onOpenFull();
      } else return;
      event.preventDefault();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [review, canApprove, canReject, armed, onDecide, onOpenFull]);

  let body: React.ReactNode;
  if (isPending) {
    body = (
      <div className="space-y-3" aria-busy="true" aria-label="Loading the preview">
        <div className="h-7 w-2/3 animate-pulse rounded bg-white/5" />
        <div className="h-40 animate-pulse rounded-lg bg-white/5" />
        <p className="text-xs text-muted-foreground">You can decide once the preview has loaded.</p>
      </div>
    );
  } else if (error) {
    body = (
      <div className="space-y-2">
        <ApiErrorMessage error={asApiError(error)} title="Couldn't load the preview" />
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
  } else if (review) {
    const newFields = review.new_fields.map((r, i) => normalizeEvidenceRow({ ...r }, i));
    const newKeys = new Set(newFields.map((r) => r.key));
    const answers = review.answers.map((r, i) => normalizeEvidenceRow({ ...r }, i)).filter((r) => !newKeys.has(r.key));
    const flagged = [...newFields.map((r) => ({ r, isNew: true })), ...answers.map((r) => ({ r, isNew: false }))].filter(
      ({ r, isNew }) => needsLook(evidenceFlags(r, isNew))
    );
    const fine = answers.filter((r) => !needsLook(evidenceFlags(r)));
    const jobLink = httpUrl(review.job.url);
    const company = review.job.company;
    const target = company ?? "the employer";

    body = (
      <>
        <header className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
          <div className="min-w-0">
            <h2 id={`${id}-title`} className="text-lg font-semibold leading-tight">
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
                Round {review.review_round} · new fields
              </span>
            )}
            {review.review_deadline && <DeadlineCountdown deadline={review.review_deadline} className="text-sm" />}
          </div>
        </header>

        {problem ? (
          <ProblemNotice runId={runId} problem={problem} review={review} />
        ) : (
          roundChanged && (
            <div role="alert" className="flex gap-2 rounded-lg border border-cyan-400/40 bg-cyan-500/10 px-3 py-2 text-sm text-cyan-100">
              <Info className="mt-0.5 h-4 w-4 shrink-0 text-cyan-300" aria-hidden />
              This application moved to round {review.review_round} while you were looking. The new fields are marked New
              below; check them before deciding.
            </div>
          )
        )}

        <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_14rem]">
          <div className="min-w-0 space-y-3">
            {flagged.length > 0 ? (
              <section aria-labelledby={`${id}-flagged`} className="space-y-1.5">
                <h3 id={`${id}-flagged`} className="text-sm font-semibold">
                  {flagged.length} to check
                </h3>
                <ol aria-label="Answers to check" className="overflow-hidden rounded-lg border border-white/[0.07]">
                  {flagged.map(({ r, isNew }) => (
                    <FlaggedRow key={`${isNew ? "new" : "a"}:${r.key}`} row={r} isNew={isNew} />
                  ))}
                </ol>
              </section>
            ) : (
              <p className="flex items-center gap-1.5 text-sm text-emerald-200">
                <CheckCircle2 className="h-4 w-4 text-emerald-400" aria-hidden />
                Nothing flagged: every answer comes from a stored fact and read back as filled.
              </p>
            )}
            <FineRows rows={fine} />
            {review.cover_letter && <CoverLetter runId={runId} letter={review.cover_letter} />}
          </div>

          <aside className="min-w-0" aria-label="Screenshot">
            <ReviewScreenshot
              runId={runId}
              artifactId={review.screenshot_artifact_id}
              reviewRound={review.review_round}
              compact
            />
          </aside>
        </div>

        <div className="sticky bottom-3 z-10 rounded-xl border border-white/10 bg-[#0d121c]/95 p-3 shadow-[0_-12px_32px_-16px_rgba(0,0,0,0.8)] backdrop-blur-md">
          {pending?.phase === "sending" ? (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              {pending.decision === "approved" ? `Sending your approval to YahBah…` : "Sending your rejection…"}
            </p>
          ) : pending ? (
            <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
              <span>
                {pending.decision === "approved" ? `Submitting to ${target} in ` : "Rejecting in "}
                <SecondsLeft at={pending.sendsAt} />…
              </span>
              <Button size="sm" variant="ghost" onClick={onUndo}>
                <Undo2 aria-hidden />
                Undo
                <Kbd>U</Kbd>
              </Button>
            </div>
          ) : decided ? (
            <p className="flex items-center gap-2 text-sm text-emerald-200">
              <CheckCircle2 className="h-4 w-4" aria-hidden />
              {decided === "approved"
                ? `Approved. YahBah re-opens the form, re-fills these answers and submits to ${target}.`
                : "Rejected. Nothing was submitted."}
            </p>
          ) : !canApprove && !canReject ? (
            <p className="text-sm text-muted-foreground">Read only: this application isn&apos;t waiting for a decision.</p>
          ) : withReason ? (
            <div className="space-y-2">
              <label htmlFor={`${id}-reason`} className="block text-sm font-medium">
                Why reject it? <span className="font-normal text-muted-foreground">(optional)</span>
              </label>
              <textarea
                id={`${id}-reason`}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                rows={2}
                maxLength={500}
                autoFocus
                className="w-full rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm placeholder:text-muted-foreground focus:border-cyan-500/40 focus:outline-none"
              />
              <div className="flex justify-end gap-2">
                <Button size="sm" variant="ghost" onClick={() => setWithReason(false)}>
                  Back
                </Button>
                <Button
                  size="sm"
                  variant="destructive"
                  disabled={!armed}
                  onClick={() => onDecide("rejected", review.review_round, reason.trim() || null, company)}
                >
                  <ThumbsDown aria-hidden />
                  Nope
                </Button>
              </div>
            </div>
          ) : (
            <div className="flex flex-wrap items-center gap-2">
              {canApprove && (
                <Button
                  size="sm"
                  disabled={!armed}
                  onClick={() => onDecide("approved", review.review_round, null, company)}
                  className={cn("bg-emerald-500 text-emerald-950 hover:bg-emerald-400", ARMING)}
                  aria-keyshortcuts="Y"
                >
                  <Check aria-hidden />
                  Yup, submit
                  <Kbd>Y</Kbd>
                </Button>
              )}
              {canReject && (
                <>
                  <Button
                    size="sm"
                    variant="destructive"
                    disabled={!armed}
                    className={ARMING}
                    onClick={() => onDecide("rejected", review.review_round, null, company)}
                    aria-keyshortcuts="N"
                  >
                    <ThumbsDown aria-hidden />
                    Nope
                    <Kbd>N</Kbd>
                  </Button>
                  <button
                    type="button"
                    onClick={() => setWithReason(true)}
                    className="text-xs font-medium text-muted-foreground hover:text-foreground"
                  >
                    with a reason…
                  </button>
                </>
              )}
              <Button size="sm" variant="ghost" onClick={onOpenFull} className="ml-auto" aria-keyshortcuts="O">
                <Maximize2 aria-hidden />
                Open full review
                <Kbd>O</Kbd>
              </Button>
            </div>
          )}
        </div>
      </>
    );
  }

  return (
    <section
      ref={rootRef}
      tabIndex={-1}
      aria-labelledby={review ? `${id}-title` : undefined}
      aria-label={review ? undefined : "Preview"}
      className="space-y-4 outline-none"
    >
      {body}
    </section>
  );
}
