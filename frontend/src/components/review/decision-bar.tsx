"use client";

import { useId, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { CheckCircle2, Loader2, Send, ThumbsDown } from "lucide-react";
import { ApiErrorMessage } from "@/components/api-error-message";
import { Button } from "@/components/ui/button";
import { approveRun, rejectRun } from "@/lib/api";
import { asApiError } from "@/lib/toast";

export type Decision = "approved" | "rejected";

/** 409 codes that mean "what you're looking at is out of date": the panel refreshes and explains. */
export const CONFLICT_CODES = new Set(["stale_review_round", "not_awaiting_review", "action_not_allowed"]);

/**
 * Approve or reject exactly this review round. Approving needs the box ticked
 * first ("check the box to submit"): the tick says what the user is attesting,
 * and keeps a stray click from submitting an application.
 */
export function DecisionBar({
  runId,
  reviewRound,
  company,
  canApprove,
  canReject,
  onDecided,
  onConflict,
}: {
  runId: string;
  reviewRound: number;
  company: string | null;
  canApprove: boolean;
  canReject: boolean;
  onDecided: (decision: Decision) => void;
  onConflict: (code: string) => void;
}) {
  const id = useId();
  const [checked, setChecked] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [decided, setDecided] = useState<Decision | null>(null);

  const mutation = useMutation({
    mutationFn: (decision: Decision) =>
      decision === "approved"
        ? approveRun(runId, reviewRound)
        : rejectRun(runId, reviewRound, reason.trim() || null),
    meta: { inlineErrors: true },
    onSuccess: (_data, decision) => {
      setDecided(decision);
      onDecided(decision);
    },
    onError: (error) => {
      const code = asApiError(error).code;
      if (CONFLICT_CODES.has(code)) onConflict(code);
    },
  });

  const error = mutation.error ? asApiError(mutation.error) : null;
  const target = company ?? "the employer";

  if (!decided && !canApprove && !canReject) {
    return (
      <p role="status" className="rounded-xl border border-white/10 bg-white/[0.03] px-4 py-3 text-sm text-muted-foreground">
        Read only: this application isn&apos;t waiting for a decision.
      </p>
    );
  }

  if (decided) {
    return (
      <div role="status" className="flex items-center gap-2 rounded-xl border border-emerald-500/25 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-200">
        <CheckCircle2 className="h-4 w-4" aria-hidden />
        {decided === "approved"
          ? `Approved. YahBah re-opens the form, re-fills these answers and submits to ${target}.`
          : "Rejected. Nothing was submitted; you can retry it later."}
      </div>
    );
  }

  return (
    <div className="space-y-3 rounded-xl border border-white/10 bg-[#0d121c]/95 p-4 shadow-[0_-12px_32px_-16px_rgba(0,0,0,0.8)] backdrop-blur-md">
      {error && !CONFLICT_CODES.has(error.code) && (
        <ApiErrorMessage error={error} title="The decision wasn't recorded" />
      )}

      {rejecting ? (
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
            placeholder="e.g. wrong salary expectation, not interested any more"
            className="w-full rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm placeholder:text-muted-foreground focus:border-cyan-500/40 focus:outline-none"
          />
          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="ghost" size="sm" onClick={() => setRejecting(false)} disabled={mutation.isPending}>
              Back
            </Button>
            <Button
              size="sm"
              variant="destructive"
              onClick={() => mutation.mutate("rejected")}
              disabled={mutation.isPending}
            >
              {mutation.isPending ? <Loader2 className="animate-spin" aria-hidden /> : <ThumbsDown aria-hidden />}
              Reject application
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          {canApprove ? (
            <label htmlFor={`${id}-ok`} className="flex cursor-pointer items-start gap-2.5 text-sm">
              <input
                id={`${id}-ok`}
                type="checkbox"
                checked={checked}
                onChange={(e) => setChecked(e.target.checked)}
                className="mt-0.5 h-4 w-4 shrink-0 cursor-pointer accent-emerald-500"
              />
              <span>
                These answers are right: submit this application to <strong>{target}</strong>
              </span>
            </label>
          ) : (
            <span />
          )}
          <div className="flex shrink-0 gap-2 self-end sm:self-auto">
            {canReject && (
              <Button variant="ghost" size="sm" onClick={() => setRejecting(true)} disabled={mutation.isPending}>
                <ThumbsDown aria-hidden />
                Reject…
              </Button>
            )}
            {canApprove && (
              <Button
                size="sm"
                onClick={() => mutation.mutate("approved")}
                disabled={!checked || mutation.isPending}
                className="bg-emerald-500 text-emerald-950 hover:bg-emerald-400"
              >
                {mutation.isPending ? <Loader2 className="animate-spin" aria-hidden /> : <Send aria-hidden />}
                Approve and submit
              </Button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
