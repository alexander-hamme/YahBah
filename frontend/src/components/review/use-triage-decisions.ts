"use client";

import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { CONFLICT_CODES, type Decision } from "@/components/review/decision-bar";
import { reviewQueryKey } from "@/components/review/review-panel";
import { ApiError, approveRun, rejectRun } from "@/lib/api";
import { REVIEW_QUEUE_KEY } from "@/lib/queries";
import { asApiError, toast } from "@/lib/toast";
import type { ApplicationListResponse, ReviewPayload } from "@/lib/types";

// Quick decisions from the review queue (contract §4, "Triage"). A decision waits
// UNDO_MS before its request is sent; the wait replaces the full review's tick box.
// Nothing is sent if the user undoes, leaves the page, the review payload is
// refetched with another round, or the run leaves the queue first. Every drop is
// reported visibly; an outcome the client can't know is reported as unknown.

export const UNDO_MS = 5_000;

/** How long a preview must be on screen, at its current round, before it takes a decision. */
export const DWELL_MS = 800;

export interface PendingDecision {
  runId: string;
  decision: Decision;
  /** The round of the review payload the preview rendered when the user decided. */
  round: number;
  reason: string | null;
  company: string | null;
  sendsAt: number;
  /** waiting: inside the undo window. sending: the request is out; it can't be undone. */
  phase: "waiting" | "sending";
}

/** Why a pending decision was dropped without being sent. */
export type DropReason = "round_changed" | "left_queue" | "stale" | "left_page";

export type TriageProblem =
  | { kind: "dropped"; why: DropReason; decision: Decision }
  | { kind: "conflict"; code: string; round: number }
  /** A 4xx: the server refused it, so nothing was recorded. */
  | { kind: "error"; error: ApiError }
  /** No response, or a 5xx: the decision may or may not have been recorded. */
  | { kind: "unknown"; decision: Decision; error: ApiError };

/** A visible report of a decision that didn't go through, kept after its row is gone. */
export interface TriageNote {
  id: number;
  runId: string;
  title: string;
  body: string;
}

/**
 * The one guard, read from the query cache: null while `p` may still be sent.
 * Checked whenever the cache changes and again immediately before sending.
 */
export function pendingDropReason(queryClient: QueryClient, p: PendingDecision): DropReason | null {
  const review = queryClient.getQueryData<ReviewPayload>(reviewQueryKey(p.runId));
  if (!review) return "stale";
  if (review.review_round !== p.round) return "round_changed";
  const row = queryClient
    .getQueryData<ApplicationListResponse>(REVIEW_QUEUE_KEY)
    ?.items.find((i) => i.run_id === p.runId);
  const action = p.decision === "approved" ? "approve" : "reject";
  if (review.status !== "AWAITING_REVIEW" || !row || !row.actions.includes(action)) return "left_queue";
  return null;
}

function who(p: PendingDecision): string {
  return p.company ?? "this application";
}

const DROP_TEXT: Record<DropReason, string> = {
  round_changed: "it changed while the undo window was open",
  left_queue: "it isn't waiting for review any more",
  stale: "its review needs reloading",
  left_page: "you left the page before the undo window closed",
};

type Entry = { p: PendingDecision; timer: ReturnType<typeof setTimeout> | null };

/**
 * Requests in flight, kept with the QueryClient rather than the component: a remounted
 * queue (navigate away and back) still sees a send it didn't start, and offers no
 * second decision for that run until it settles. One store per client (so per test).
 */
interface InFlightStore {
  get: () => readonly PendingDecision[];
  subscribe: (listener: () => void) => () => void;
  has: (runId: string) => boolean;
  add: (p: PendingDecision) => void;
  remove: (runId: string) => void;
}

const inFlightStores = new WeakMap<QueryClient, InFlightStore>();

function inFlightStore(queryClient: QueryClient): InFlightStore {
  let store = inFlightStores.get(queryClient);
  if (!store) {
    let sends: readonly PendingDecision[] = [];
    const listeners = new Set<() => void>();
    const set = (next: readonly PendingDecision[]) => {
      sends = next;
      for (const l of listeners) l();
    };
    store = {
      get: () => sends,
      subscribe: (listener) => {
        listeners.add(listener);
        return () => listeners.delete(listener);
      },
      has: (runId) => sends.some((p) => p.runId === runId),
      add: (p) => set([...sends.filter((s) => s.runId !== p.runId), p]),
      remove: (runId) => set(sends.filter((s) => s.runId !== runId)),
    };
    inFlightStores.set(queryClient, store);
  }
  return store;
}

const NO_SENDS: readonly PendingDecision[] = [];

const waitingIn = (entries: ReadonlyMap<string, Entry>) => [...entries.values()].filter((e) => e.p.phase === "waiting");

export function useTriageDecisions({ onSent }: { onSent: (runId: string, decision: Decision) => void }) {
  const queryClient = useQueryClient();
  // Source of truth for timers and in-flight requests; `pending` mirrors it for rendering.
  const live = useRef(new Map<string, Entry>());
  const [own, setOwn] = useState<readonly PendingDecision[]>([]);
  const store = inFlightStore(queryClient);
  const inFlight = useSyncExternalStore(store.subscribe, store.get, () => NO_SENDS);
  // This instance's decisions, plus sends another (unmounted) instance started.
  const pending = useMemo(
    () => [...own, ...inFlight.filter((s) => !own.some((p) => p.runId === s.runId))],
    [own, inFlight]
  );
  const [problems, setProblems] = useState<ReadonlyMap<string, TriageProblem>>(new Map());
  const [notes, setNotes] = useState<readonly TriageNote[]>([]);
  const [announcement, setAnnouncement] = useState("");
  const nextNoteId = useRef(1);
  const onSentRef = useRef(onSent);
  useEffect(() => {
    onSentRef.current = onSent;
  });
  // A request can outlive the page: its outcome then goes to the app-wide toaster.
  const mounted = useRef(false);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const sync = useCallback(() => setOwn([...live.current.values()].map((e) => e.p)), []);

  const setProblem = useCallback((runId: string, problem: TriageProblem | null) => {
    setProblems((prev) => {
      const next = new Map(prev);
      if (problem) next.set(runId, problem);
      else next.delete(runId);
      return next;
    });
  }, []);

  /** Report visibly (a note that outlives the row) and to screen readers. */
  const report = useCallback((runId: string, title: string, body: string) => {
    const id = nextNoteId.current++;
    setNotes((prev) => [...prev.filter((n) => n.runId !== runId), { id, runId, title, body }]);
    setAnnouncement(`${title}. ${body}`);
  }, []);

  /** Clear the timer and forget the decision: after this, nothing for it can be sent. */
  const drop = useCallback(
    (runId: string, why: DropReason | "undone") => {
      const entry = live.current.get(runId);
      if (!entry || entry.p.phase !== "waiting") return;
      if (entry.timer) clearTimeout(entry.timer);
      live.current.delete(runId);
      sync();
      const verb = entry.p.decision === "approved" ? "approved" : "rejected";
      if (why === "undone") {
        setAnnouncement(`Undone: ${who(entry.p)} was not ${verb}. Nothing was sent.`);
      } else {
        setProblem(runId, { kind: "dropped", why, decision: entry.p.decision });
        report(runId, `Not sent: ${who(entry.p)}`, `It was not ${verb} because ${DROP_TEXT[why]}.`);
      }
    },
    [report, setProblem, sync]
  );

  const send = useCallback(
    async (p: PendingDecision) => {
      if (live.current.get(p.runId)?.p !== p) return; // dropped or replaced meanwhile
      const why = pendingDropReason(queryClient, p);
      if (why) {
        drop(p.runId, why);
        return;
      }
      // In flight: still blocks a second decision, but can't be undone any more.
      const sending: PendingDecision = { ...p, phase: "sending" };
      live.current.set(p.runId, { p: sending, timer: null });
      store.add(sending);
      sync();
      try {
        if (p.decision === "approved") await approveRun(p.runId, p.round);
        else await rejectRun(p.runId, p.round, p.reason);
        if (!mounted.current) {
          toast.success(p.decision === "approved" ? `Approved: ${who(p)}` : `Rejected: ${who(p)}`);
          return;
        }
        setAnnouncement(
          p.decision === "approved"
            ? `Approved: YahBah re-fills the answers and submits to ${p.company ?? "the employer"}.`
            : `Rejected ${who(p)}. Nothing was submitted.`
        );
        onSentRef.current(p.runId, p.decision);
      } catch (err) {
        const error = asApiError(err);
        const verb = p.decision === "approved" ? "approval" : "rejection";
        const refused = CONFLICT_CODES.has(error.code) || (error.status >= 400 && error.status < 500);
        if (!mounted.current) {
          if (refused) toast.error(error, `Not recorded: ${who(p)}`);
          else
            toast.error(
              new ApiError(error.status, {
                code: error.code,
                message: `${error.message} Your ${verb} may or may not have been recorded: open the run to see whether it was.`,
                run_id: p.runId,
              }),
              `Couldn't confirm: ${who(p)}`
            );
          return;
        }
        if (CONFLICT_CODES.has(error.code)) {
          setProblem(p.runId, { kind: "conflict", code: error.code, round: p.round });
          report(p.runId, `Not sent: ${who(p)}`, "It changed or left review before your decision. Check it again.");
        } else if (error.status >= 400 && error.status < 500) {
          setProblem(p.runId, { kind: "error", error });
          report(p.runId, `Not sent: ${who(p)}`, error.message);
        } else {
          setProblem(p.runId, { kind: "unknown", decision: p.decision, error });
          report(
            p.runId,
            `Couldn't confirm: ${who(p)}`,
            `Your ${verb} may or may not have been recorded. Open the run to see whether it was.`
          );
        }
      } finally {
        live.current.delete(p.runId);
        store.remove(p.runId);
        if (mounted.current) sync();
        // The client outlives the page: other views see the outcome either way.
        queryClient.invalidateQueries({ queryKey: reviewQueryKey(p.runId) });
        queryClient.invalidateQueries({ queryKey: ["run", p.runId] });
        queryClient.invalidateQueries({ queryKey: ["applications"] });
        queryClient.invalidateQueries({ queryKey: ["application-stats"] });
      }
    },
    [drop, queryClient, report, setProblem, store, sync]
  );

  const decide = useCallback(
    (d: Omit<PendingDecision, "sendsAt" | "phase">) => {
      // one decision per run at a time, waiting or in flight (from any instance)
      if (live.current.has(d.runId) || store.has(d.runId)) return;
      const p: PendingDecision = { ...d, sendsAt: Date.now() + UNDO_MS, phase: "waiting" };
      const timer = setTimeout(() => void send(p), UNDO_MS);
      live.current.set(p.runId, { p, timer });
      sync();
      setProblem(p.runId, null);
      setNotes((prev) => prev.filter((n) => n.runId !== p.runId));
      setAnnouncement(
        `${p.decision === "approved" ? "Approving" : "Rejecting"} ${who(p)}: sends in ${UNDO_MS / 1000} seconds. Press U to undo.`
      );
    },
    [send, setProblem, store, sync]
  );

  /** Undo one waiting decision, or the most recent one. Returns the run it undid. */
  const undo = useCallback(
    (runId?: string): string | null => {
      const target = runId ?? waitingIn(live.current).pop()?.p.runId;
      if (!target || live.current.get(target)?.p.phase !== "waiting") return null;
      drop(target, "undone");
      return target;
    },
    [drop]
  );

  // A refetched payload with another round, or a queue without the run, drops it now.
  useEffect(
    () =>
      queryClient.getQueryCache().subscribe((event) => {
        if (event.type !== "updated" && event.type !== "removed") return;
        for (const { p } of waitingIn(live.current)) {
          const why = pendingDropReason(queryClient, p);
          if (why) drop(p.runId, why);
        }
      }),
    [drop, queryClient]
  );

  // Leaving the page (or hiding it) drops everything still waiting.
  useEffect(() => {
    const leave = () => {
      for (const { p } of waitingIn(live.current)) drop(p.runId, "left_page");
    };
    const onVisibility = () => {
      if (document.visibilityState === "hidden") leave();
    };
    window.addEventListener("pagehide", leave);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.removeEventListener("pagehide", leave);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [drop]);

  // So does unmounting (client-side navigation away from the queue). The page is
  // gone, so the app-wide toaster says what wasn't sent.
  useEffect(() => {
    const entries = live.current;
    return () => {
      const unsent = waitingIn(entries).map((e) => e.p);
      for (const { timer } of entries.values()) if (timer) clearTimeout(timer);
      entries.clear();
      if (unsent.length > 0) {
        toast.error(
          new ApiError(0, {
            code: "triage_dropped",
            message: "You left the review queue before the undo window closed, so nothing was sent.",
          }),
          `Not sent: ${unsent.map(who).join(", ")}`
        );
      }
    };
  }, []);

  return {
    pending,
    problems,
    notes,
    announcement,
    decide,
    undo,
    dismissNote: (id: number) => setNotes((prev) => prev.filter((n) => n.id !== id)),
  };
}

/** True when a key press belongs to whatever has focus, not to the page's shortcuts. */
export function isShortcutBlocked(event: KeyboardEvent): boolean {
  if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) return true;
  const target = event.target;
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  if (target.closest("input, textarea, select, [contenteditable=''], [contenteditable='true']")) return true;
  // A dialog (the screenshot lightbox) owns its own keys, Escape included.
  return !!target.closest("[role='dialog'], [aria-modal='true']");
}
