"use client";

import { useId } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Loader2, RotateCw } from "lucide-react";
import { setSubmissionMode } from "@/lib/api";
import { RUNTIME_SETTINGS_KEY, useRuntimeSettings } from "@/lib/queries";
import { toast } from "@/lib/toast";
import type { SubmissionMode } from "@/lib/types";
import { cn } from "@/lib/utils";

interface ModeOption {
  value: SubmissionMode;
  label: string;
  description: string;
}

// Batch 2 appends { value: "auto_reviewed", ... } once the reviewer pass exists.
export const MODE_OPTIONS: readonly ModeOption[] = [
  { value: "testing", label: "Testing", description: "Fill forms, never submit." },
  { value: "review", label: "Review", description: "Fill, then wait for your approval before submitting." },
];

export function modeLabel(mode: SubmissionMode | null | undefined): string {
  return MODE_OPTIONS.find((o) => o.value === mode)?.label ?? "Unknown";
}

/** Segmented control naming the submission mode new runs get. Changing it never touches started runs. */
export function SubmissionModeControl() {
  const queryClient = useQueryClient();
  const hintId = useId();
  const { data, isPending, isError, error, refetch, isFetching } = useRuntimeSettings();

  const mutation = useMutation({
    mutationFn: setSubmissionMode,
    meta: { errorTitle: "Couldn't change the mode" },
    onSuccess: (settings) => {
      queryClient.setQueryData(RUNTIME_SETTINGS_KEY, settings);
      toast.success(
        `New runs: ${modeLabel(settings.submission_mode)}`,
        "Runs that already started keep the mode they started with."
      );
    },
  });

  if (isError) {
    // Never guess: an unreachable API is "unknown", not a mode.
    return (
      <button
        type="button"
        onClick={() => refetch()}
        title={`Can't load settings: ${error.message} Click to retry.`}
        className="inline-flex items-center gap-1.5 rounded-lg border border-amber-500/40 bg-amber-500/10 px-2.5 py-1 text-xs font-semibold text-amber-300 hover:bg-amber-500/20"
      >
        {isFetching ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
        ) : (
          <AlertTriangle className="h-3.5 w-3.5" aria-hidden />
        )}
        Mode unknown
        <RotateCw className="h-3 w-3 opacity-60" aria-hidden />
      </button>
    );
  }

  const current = mutation.isPending ? mutation.variables : data?.submission_mode;

  return (
    <div className="flex items-center gap-2">
      <span id={`${hintId}-label`} className="hidden text-xs text-muted-foreground sm:inline">
        New runs
      </span>
      <div
        role="radiogroup"
        aria-labelledby={`${hintId}-label`}
        aria-describedby={hintId}
        aria-busy={isPending || mutation.isPending}
        className="flex rounded-lg border border-white/10 bg-white/[0.04] p-0.5"
      >
        {MODE_OPTIONS.map((option) => {
          const selected = current === option.value;
          return (
            <label
              key={option.value}
              title={option.description}
              className={cn(
                "relative cursor-pointer select-none rounded-md px-2.5 py-1 text-xs font-semibold transition-colors",
                "has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-cyan-400/60",
                selected
                  ? option.value === "testing"
                    ? "bg-violet-500/20 text-violet-200"
                    : "bg-cyan-500/20 text-cyan-200"
                  : "text-muted-foreground hover:text-foreground",
                (isPending || mutation.isPending) && "cursor-wait"
              )}
            >
              <input
                type="radio"
                name={`${hintId}-mode`}
                value={option.value}
                checked={selected}
                disabled={isPending || mutation.isPending}
                onChange={() => mutation.mutate(option.value)}
                className="sr-only"
              />
              <span className="inline-flex items-center gap-1">
                {mutation.isPending && mutation.variables === option.value && (
                  <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
                )}
                {option.label}
              </span>
            </label>
          );
        })}
      </div>
      <span id={hintId} className="sr-only">
        Submission mode for new runs. {MODE_OPTIONS.map((o) => `${o.label}: ${o.description}`).join(" ")}{" "}
        Changing it affects new runs only.
      </span>
    </div>
  );
}
