"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowLeft, CheckCircle2, Clock, HelpCircle, Inbox, Loader2, Mail, ThumbsDown, Undo2, X } from "lucide-react";
import { ApiErrorMessage } from "@/components/api-error-message";
import { CompanyLogo } from "@/components/company-logo";
import { DeadlineCountdown } from "@/components/deadline-countdown";
import type { Decision } from "@/components/review/decision-bar";
import { ReviewPanel, reviewQueryKey } from "@/components/review/review-panel";
import { Kbd, SecondsLeft, TriagePreview } from "@/components/review/triage-preview";
import {
  isShortcutBlocked,
  useTriageDecisions,
  type PendingDecision,
  type TriageNote,
  type TriageProblem,
} from "@/components/review/use-triage-decisions";
import { Button } from "@/components/ui/button";
import { getRunReview } from "@/lib/api";
import { useReviewQueue, useRuntimeSettings } from "@/lib/queries";
import { asApiError } from "@/lib/toast";
import type { ApplicationListItem } from "@/lib/types";
import { cn } from "@/lib/utils";

function QueueItem({
  item,
  selected,
  decision,
  pending,
  problem,
  onSelect,
}: {
  item: ApplicationListItem;
  selected: boolean;
  decision: Decision | undefined;
  pending: PendingDecision | undefined;
  problem: TriageProblem | undefined;
  onSelect: () => void;
}) {
  return (
    <li>
      <button
        type="button"
        data-run={item.run_id}
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
            {pending?.phase === "sending" ? (
              <span className="inline-flex shrink-0 items-center gap-1 text-xs text-muted-foreground">
                <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
                Sending
              </span>
            ) : pending ? (
              <span className="inline-flex shrink-0 items-center gap-1 text-xs text-amber-200">
                <Clock className="h-3 w-3" aria-hidden />
                {pending.decision === "approved" ? "Approving" : "Rejecting"} · <SecondsLeft at={pending.sendsAt} />
              </span>
            ) : problem ? (
              <span className="inline-flex shrink-0 items-center gap-1 text-xs text-amber-200">
                {problem.kind === "unknown" ? (
                  <HelpCircle className="h-3 w-3" aria-hidden />
                ) : (
                  <AlertTriangle className="h-3 w-3" aria-hidden />
                )}
                {problem.kind === "unknown" ? "Unconfirmed" : "Not sent"}
              </span>
            ) : decision ? (
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

const NOTES_SHOWN = 3;

/**
 * Decisions inside their undo window (the newest is the one U undoes), requests in
 * flight, and notes on decisions that didn't go through: they outlive the run's row.
 */
function UndoBar({
  pending,
  notes,
  onUndo,
  onShow,
  onDismiss,
}: {
  pending: readonly PendingDecision[];
  notes: readonly TriageNote[];
  onUndo: (runId: string) => void;
  onShow: (runId: string) => void;
  onDismiss: (id: number) => void;
}) {
  const [allNotes, setAllNotes] = useState(false);
  if (pending.length === 0 && notes.length === 0) return null;
  // The newest few by default; every undismissed note stays one click away, never discarded.
  const shownNotes = allNotes ? notes : notes.slice(-NOTES_SHOWN);
  const hiddenNotes = notes.length - shownNotes.length;
  const newest = pending.filter((p) => p.phase === "waiting").at(-1)?.runId;
  const card =
    "pointer-events-auto flex w-full max-w-md items-center justify-between gap-3 rounded-xl border bg-[#0d121c]/95 px-4 py-2 text-sm shadow-lg backdrop-blur-md";
  return (
    <div data-undo-bar className="pointer-events-none fixed inset-x-0 bottom-4 z-30 flex flex-col items-center gap-2 px-4">
      {hiddenNotes > 0 && (
        <button
          type="button"
          onClick={() => setAllNotes(true)}
          className="pointer-events-auto rounded-full border border-amber-500/35 bg-[#0d121c]/95 px-3 py-1 text-xs font-medium text-amber-100 hover:bg-white/10"
        >
          +{hiddenNotes} more not sent
        </button>
      )}
      {allNotes && notes.length > NOTES_SHOWN && (
        <button
          type="button"
          onClick={() => setAllNotes(false)}
          className="pointer-events-auto text-xs font-medium text-cyan-300 hover:text-cyan-200"
        >
          Show only the latest {NOTES_SHOWN}
        </button>
      )}
      <div className={cn("flex w-full flex-col items-center gap-2", allNotes && "pointer-events-auto max-h-[50vh] overflow-y-auto")}>
        {shownNotes.map((n) => (
          <div key={n.id} className={cn(card, "border-amber-500/35")}>
            <div className="min-w-0">
              <p className="font-semibold text-amber-100">{n.title}</p>
              <p className="text-xs text-foreground/75">{n.body}</p>
            </div>
            <div className="flex shrink-0 items-center gap-1">
              <Button size="sm" variant="ghost" onClick={() => onShow(n.runId)}>
                Show
              </Button>
              <button
                type="button"
                onClick={() => onDismiss(n.id)}
                aria-label="Dismiss"
                className="h-6 w-6 rounded-md p-1 text-white/40 hover:bg-white/10 hover:text-white"
              >
                <X className="h-4 w-4" aria-hidden />
              </button>
            </div>
          </div>
        ))}
      </div>
      {pending.map((p) =>
        p.phase === "sending" ? (
          <div key={p.runId} className={cn(card, "border-white/10 text-muted-foreground")}>
            <span className="min-w-0 truncate">
              Sending {p.decision === "approved" ? "the approval" : "the rejection"} for{" "}
              <strong className="text-foreground">{p.company ?? "this application"}</strong>…
            </span>
            <Loader2 className="h-4 w-4 shrink-0 animate-spin" aria-hidden />
          </div>
        ) : (
          <div key={p.runId} className={cn(card, "border-white/10")}>
            <span className="min-w-0 truncate">
              {p.decision === "approved" ? "Submitting to " : "Rejecting "}
              <strong>{p.company ?? "this application"}</strong> in <SecondsLeft at={p.sendsAt} />
            </span>
            <Button size="sm" variant="ghost" onClick={() => onUndo(p.runId)}>
              <Undo2 aria-hidden />
              Undo
              {p.runId === newest && <Kbd>U</Kbd>}
            </Button>
          </div>
        )
      )}
    </div>
  );
}

/**
 * The review queue: awaiting_review runs, soonest deadline first, beside the selected
 * run's quick preview (yup / nope / open the full review).
 */
export function ReviewWorkspace() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const queryClient = useQueryClient();
  const [decided, setDecided] = useState<ReadonlyMap<string, Decision>>(new Map());
  const [fullRunId, setFullRunId] = useState<string | null>(null);
  // The run whose preview takes focus when it mounts (after a keyboard move, a decision or an undo).
  const [focusRunId, setFocusRunId] = useState<string | null>(null);
  const { data: settings } = useRuntimeSettings();

  const markDecided = useCallback(
    (runId: string, decision: Decision) => setDecided((prev) => new Map(prev).set(runId, decision)),
    []
  );
  const triage = useTriageDecisions({ onSent: markDecided });
  const pendingById = new Map(triage.pending.map((p) => [p.runId, p]));

  const queue = useReviewQueue();
  const items = queue.data?.items ?? [];
  const open = (i: ApplicationListItem) => !decided.has(i.run_id) && !pendingById.has(i.run_id);
  const waiting = items.filter(open);

  const requested = searchParams.get("run");
  const selected =
    items.find((i) => i.run_id === requested) ?? (requested ? undefined : waiting[0]);
  const showFull = !!selected && fullRunId === selected.run_id;

  function select(runId: string | null, { focus = false } = {}) {
    setFullRunId(null);
    setFocusRunId(focus ? runId : null);
    router.replace(runId ? `${pathname}?run=${runId}` : pathname, { scroll: false });
  }

  /** The next run without a decision after `runId`, wrapping around. */
  function nextOpen(runId: string, skip: ReadonlySet<string> = new Set()) {
    const index = items.findIndex((i) => i.run_id === runId);
    return [...items.slice(index + 1), ...items.slice(0, index)].find((i) => open(i) && !skip.has(i.run_id));
  }

  function handleDecided(runId: string, decision: Decision) {
    markDecided(runId, decision);
    select(nextOpen(runId, new Set([runId]))?.run_id ?? null);
  }

  function quickDecide(item: ApplicationListItem, decision: Decision, round: number, reason: string | null, company: string | null) {
    triage.decide({ runId: item.run_id, decision, round, reason, company });
    select(nextOpen(item.run_id, new Set([item.run_id]))?.run_id ?? null, { focus: true });
  }

  function undo(runId?: string) {
    const undone = triage.undo(runId);
    if (undone) select(undone, { focus: true });
  }

  /** From the queue list (arrows), focus stays on the list; otherwise it follows to the preview. */
  function move(step: 1 | -1, { inQueue = false } = {}) {
    if (items.length === 0) return;
    const index = selected ? items.findIndex((i) => i.run_id === selected.run_id) : -1;
    const next = items[(index + step + items.length) % items.length];
    select(next.run_id, { focus: !inQueue });
    if (inQueue) document.querySelector<HTMLElement>(`[data-review-queue] [data-run="${next.run_id}"]`)?.focus();
  }

  // Warm the next run's review, so the preview after a decision renders at once.
  const upNext = selected ? nextOpen(selected.run_id)?.run_id : undefined;
  useEffect(() => {
    if (!upNext) return;
    queryClient.prefetchQuery({
      queryKey: reviewQueryKey(upNext),
      queryFn: () => getRunReview(upNext),
      staleTime: 30_000,
    });
  }, [queryClient, upNext]);

  // j / k move through the queue (arrows too, from the queue list); u / Esc undo the newest decision.
  const keysRef = useRef({ move, undo, showFull });
  useEffect(() => {
    keysRef.current = { move, undo, showFull };
  });
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (isShortcutBlocked(event)) return;
      const inQueue = event.target instanceof HTMLElement && !!event.target.closest("[data-review-queue]");
      const key = event.key;
      if (key === "j" || key === "J" || (inQueue && key === "ArrowDown")) keysRef.current.move(1, { inQueue });
      else if (key === "k" || key === "K" || (inQueue && key === "ArrowUp")) keysRef.current.move(-1, { inQueue });
      else if (key === "u" || key === "U" || key === "Escape") {
        // Undo belongs to the triage preview and the undo bar, not to a full review of another run.
        const inUndoBar = event.target instanceof HTMLElement && !!event.target.closest("[data-undo-bar]");
        if (keysRef.current.showFull && !inUndoBar) return;
        keysRef.current.undo();
      }
      else return;
      event.preventDefault();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [keysRef]);

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
          <div className="text-right text-sm text-muted-foreground">
            <p aria-live="polite">
              <span className="font-semibold text-foreground">{waiting.length}</span> waiting
            </p>
            {items.length > 0 && (
              <p className="hidden text-xs text-white/40 sm:block">
                <Kbd>J</Kbd>/<Kbd>K</Kbd> move · <Kbd>Y</Kbd> yup · <Kbd>N</Kbd> nope · <Kbd>O</Kbd> full review ·{" "}
                <Kbd>U</Kbd> undo
              </p>
            )}
          </div>
        )}
      </div>

      {/* Outcomes and undo windows, announced once each (the visible countdowns are not live). */}
      <p role="status" aria-live="polite" className="sr-only">
        {triage.announcement}
      </p>

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
          <nav aria-label="Applications awaiting review" data-review-queue className="lg:sticky lg:top-20 lg:self-start">
            <ol className="max-h-[40vh] space-y-1 overflow-y-auto lg:max-h-[calc(100vh-8rem)]">
              {items.map((item) => (
                <QueueItem
                  key={item.run_id}
                  item={item}
                  selected={item.run_id === selected?.run_id}
                  decision={decided.get(item.run_id)}
                  pending={pendingById.get(item.run_id)}
                  problem={triage.problems.get(item.run_id)}
                  onSelect={() => select(item.run_id)}
                />
              ))}
            </ol>
          </nav>

          <div className="glass-panel min-w-0 rounded-xl p-4 sm:p-5">
            {selected && showFull ? (
              <div className="space-y-3">
                <button
                  type="button"
                  onClick={() => select(selected.run_id, { focus: true })}
                  className="inline-flex items-center gap-1 text-xs font-medium text-cyan-300 hover:text-cyan-200"
                >
                  <ArrowLeft className="h-3 w-3" aria-hidden />
                  Back to the quick preview
                </button>
                <ReviewPanel
                  key={selected.run_id}
                  runId={selected.run_id}
                  actions={decided.has(selected.run_id) || pendingById.has(selected.run_id) ? [] : selected.actions}
                  onDecided={handleDecided}
                />
              </div>
            ) : selected ? (
              <TriagePreview
                key={selected.run_id}
                runId={selected.run_id}
                actions={selected.actions}
                pending={pendingById.get(selected.run_id)}
                decided={decided.get(selected.run_id)}
                problem={triage.problems.get(selected.run_id)}
                autoFocus={focusRunId === selected.run_id}
                onDecide={(decision, round, reason, company) => quickDecide(selected, decision, round, reason, company)}
                onUndo={() => undo(selected.run_id)}
                onOpenFull={() => {
                  // Looking closer means not decided yet: an open undo window for this run closes unsent.
                  triage.undo(selected.run_id);
                  setFullRunId(selected.run_id);
                }}
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
                {triage.pending.length > 0
                  ? "All decided. The last ones send when their undo windows close."
                  : "All done. Every application in the queue has a decision."}
              </div>
            )}
          </div>
        </div>
      )}

      <UndoBar
        pending={triage.pending}
        notes={triage.notes}
        onUndo={undo}
        onShow={(runId) => select(runId, { focus: true })}
        onDismiss={triage.dismissNote}
      />
    </div>
  );
}

