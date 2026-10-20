"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, CircleHelp, Loader2, XCircle } from "lucide-react";
import { ApiErrorMessage } from "@/components/api-error-message";
import { Button } from "@/components/ui/button";
import { confirmNotSubmitted, confirmSubmitted } from "@/lib/api";
import { failureReasonInfo } from "@/lib/failure-reasons";
import { formatDateTime } from "@/lib/time";
import { asApiError, toast } from "@/lib/toast";
import type { RunDetail } from "@/lib/types";

type Resolution = "confirm_submitted" | "confirm_not_submitted";

const COPY: Record<Resolution, { button: string; confirm: string; done: string }> = {
  confirm_submitted: {
    button: "It was submitted",
    confirm: "Mark this application as submitted. YahBah will treat this job as applied to.",
    done: "Marked as submitted",
  },
  confirm_not_submitted: {
    button: "It wasn't submitted",
    confirm:
      "Mark this application as not submitted. This re-enables applying to this job, so it can be applied to again.",
    done: "Marked as not submitted. You can apply to this job again.",
  },
};

/** Resolve a SUBMIT_UNCERTAIN run (contract §6). Shown only for the confirm_* actions the run lists. */
export function UncertainResolution({ run }: { run: RunDetail }) {
  const queryClient = useQueryClient();
  const [pending, setPending] = useState<Resolution | null>(null);
  const reason = failureReasonInfo(run.failure_reason);
  const offered = (["confirm_submitted", "confirm_not_submitted"] as const).filter((a) =>
    run.actions.includes(a)
  );

  const mutation = useMutation({
    mutationFn: (resolution: Resolution) =>
      resolution === "confirm_submitted" ? confirmSubmitted(run.id) : confirmNotSubmitted(run.id),
    meta: { inlineErrors: true },
    onSuccess: (_data, resolution) => {
      setPending(null);
      toast.success(COPY[resolution].done);
      queryClient.invalidateQueries({ queryKey: ["run", run.id] });
      queryClient.invalidateQueries({ queryKey: ["applications"] });
      queryClient.invalidateQueries({ queryKey: ["application-stats"] });
    },
  });

  if (offered.length === 0) return null;

  return (
    <section
      id="resolve"
      aria-labelledby="resolve-title"
      className="scroll-mt-24 rounded-xl border border-orange-400/40 bg-orange-500/[0.08] p-5"
    >
      <div className="flex gap-3">
        <CircleHelp className="mt-0.5 h-5 w-5 shrink-0 text-orange-300" aria-hidden />
        <div className="min-w-0 flex-1 space-y-3">
          <div>
            <h2 id="resolve-title" className="text-base font-semibold text-orange-100">
              This application may have been submitted
            </h2>
            <p className="mt-1 text-sm text-orange-100/80">
              {reason ? `${reason.title}. ${reason.detail} ` : ""}
              {run.submit_attempted_at &&
                `Submit was attempted ${formatDateTime(run.submit_attempted_at)}. `}
              YahBah won&apos;t apply to this job again until you decide.
            </p>
            {run.error_message && (
              <p className="mt-1 whitespace-pre-wrap text-xs text-orange-100/60">{run.error_message}</p>
            )}
          </div>

          <p className="text-sm text-foreground/75">
            Check the screenshots below, the employer&apos;s site, or the inbox
            {run.account_email ? (
              <>
                {" "}for <span className="font-mono text-xs">{run.account_email}</span>
              </>
            ) : null}{" "}
            for a confirmation.
          </p>

          {pending ? (
            <div className="space-y-2 rounded-lg border border-white/10 bg-black/20 p-3">
              <p className="text-sm">{COPY[pending].confirm}</p>
              <div className="flex flex-wrap gap-2">
                <Button
                  size="sm"
                  onClick={() => mutation.mutate(pending)}
                  disabled={mutation.isPending}
                  className="bg-orange-500/25 text-orange-100 hover:bg-orange-500/35"
                >
                  {mutation.isPending && <Loader2 className="animate-spin" aria-hidden />}
                  Confirm: {COPY[pending].button.toLowerCase()}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    setPending(null);
                    mutation.reset();
                  }}
                  disabled={mutation.isPending}
                >
                  Back
                </Button>
              </div>
            </div>
          ) : (
            <div className="flex flex-wrap gap-2">
              {offered.map((resolution) => (
                <Button
                  key={resolution}
                  size="sm"
                  variant="outline"
                  onClick={() => setPending(resolution)}
                  className="gap-1.5"
                >
                  {resolution === "confirm_submitted" ? (
                    <CheckCircle2 className="text-emerald-400" aria-hidden />
                  ) : (
                    <XCircle className="text-red-400" aria-hidden />
                  )}
                  {COPY[resolution].button}
                </Button>
              ))}
            </div>
          )}
          {offered.includes("confirm_not_submitted") && !pending && (
            <p className="text-xs text-muted-foreground">
              &ldquo;It wasn&apos;t submitted&rdquo; re-enables applying to this job. Choose it only if
              you&apos;re sure nothing reached the employer.
            </p>
          )}

          {mutation.error && (
            <ApiErrorMessage
              error={asApiError(mutation.error)}
              title="Couldn't record that"
            />
          )}
        </div>
      </div>
    </section>
  );
}
