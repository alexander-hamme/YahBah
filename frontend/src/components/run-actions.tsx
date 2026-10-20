"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CircleHelp, Eye, Loader2, RotateCw, Trash2, XCircle, type LucideIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { modeLabel } from "@/components/submission-mode-control";
import { cancelRun, deleteRun, retryRun } from "@/lib/api";
import { useRuntimeSettings } from "@/lib/queries";
import { toast } from "@/lib/toast";
import type { RunAction } from "@/lib/types";
import { cn } from "@/lib/utils";

type DirectAction = "cancel" | "retry" | "delete";

interface ActionSpec {
  label: string;
  icon: LucideIcon;
  confirm: string;
  confirmLabel: string;
  danger: boolean;
}

function invalidateRunQueries(queryClient: ReturnType<typeof useQueryClient>, runId: string) {
  queryClient.invalidateQueries({ queryKey: ["applications"] });
  queryClient.invalidateQueries({ queryKey: ["application-stats"] });
  queryClient.invalidateQueries({ queryKey: ["run", runId] });
}

/**
 * The buttons for exactly the actions the server lists for this run (allowed_actions()).
 * Approve/reject and the uncertain-run confirmations need context, so here they are links
 * to the review or resolution panel; the panels own those requests.
 */
export function RunActions({
  runId,
  actions,
  compact = false,
}: {
  runId: string;
  actions: readonly RunAction[];
  compact?: boolean;
}) {
  const queryClient = useQueryClient();
  const router = useRouter();
  const { data: settings, isError: settingsUnknown } = useRuntimeSettings();
  const [confirming, setConfirming] = useState<DirectAction | null>(null);

  const retryMode = settingsUnknown ? "an unknown mode" : `${modeLabel(settings?.submission_mode)} mode`;
  const specs: Record<DirectAction, ActionSpec> = {
    cancel: {
      label: "Cancel",
      icon: XCircle,
      confirm: "Cancel this run?",
      confirmLabel: "Yes, cancel",
      danger: true,
    },
    retry: {
      label: "Retry",
      icon: RotateCw,
      confirm: `Start a new run in ${retryMode}?`,
      confirmLabel: "Start new run",
      danger: false,
    },
    delete: {
      label: "Delete",
      icon: Trash2,
      confirm: "Delete this run and its evidence?",
      confirmLabel: "Yes, delete",
      danger: true,
    },
  };

  const mutation = useMutation({
    mutationFn: async (action: DirectAction) => {
      if (action === "cancel") return cancelRun(runId);
      if (action === "retry") return retryRun(runId);
      return deleteRun(runId);
    },
    meta: { errorTitle: "The action failed" },
    onSuccess: (response, action) => {
      setConfirming(null);
      invalidateRunQueries(queryClient, runId);
      if (action === "retry" && response.run_id) {
        toast.success("Retry started", "It runs as a new application; this one stays as it was.");
        router.push(`/applications/${response.run_id}`);
      } else if (action === "cancel") {
        toast.info("Cancel requested", "The run stops at its next safe point.");
      } else if (action === "delete") {
        toast.success("Run deleted");
        if (!compact) router.push("/applications");
      }
    },
  });

  const direct = (["cancel", "retry", "delete"] as const).filter((a) => actions.includes(a));
  const reviewable = actions.includes("approve") || actions.includes("reject");
  const resolvable = actions.includes("confirm_submitted") || actions.includes("confirm_not_submitted");
  if (direct.length === 0 && !(compact && (reviewable || resolvable))) return null;

  if (confirming) {
    const spec = specs[confirming];
    return (
      <div
        className={cn("flex items-center gap-2 animate-fade-in", compact ? "text-xs" : "text-sm")}
        onClick={(e) => e.stopPropagation()}
      >
        <span className="whitespace-nowrap text-muted-foreground">{spec.confirm}</span>
        <button
          type="button"
          onClick={() => mutation.mutate(confirming)}
          disabled={mutation.isPending}
          className={cn(
            "inline-flex items-center gap-1 whitespace-nowrap font-semibold disabled:opacity-50",
            spec.danger ? "text-red-300 hover:text-red-200" : "text-cyan-300 hover:text-cyan-200"
          )}
        >
          {mutation.isPending && <Loader2 className="h-3 w-3 animate-spin" aria-hidden />}
          {spec.confirmLabel}
        </button>
        <button
          type="button"
          onClick={() => setConfirming(null)}
          disabled={mutation.isPending}
          className="whitespace-nowrap text-muted-foreground hover:text-foreground"
        >
          Never mind
        </button>
      </div>
    );
  }

  if (compact) {
    return (
      <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
        {reviewable && (
          <Link
            href={`/review?run=${runId}`}
            className="inline-flex items-center gap-1 rounded-lg border border-amber-500/30 bg-amber-500/10 px-2 py-1 text-xs font-semibold text-amber-200 hover:bg-amber-500/20"
          >
            <Eye className="h-3.5 w-3.5" aria-hidden />
            Review
          </Link>
        )}
        {resolvable && (
          <Link
            href={`/applications/${runId}#resolve`}
            className="inline-flex items-center gap-1 rounded-lg border border-orange-400/40 bg-orange-500/15 px-2 py-1 text-xs font-semibold text-orange-200 hover:bg-orange-500/25"
          >
            <CircleHelp className="h-3.5 w-3.5" aria-hidden />
            Resolve
          </Link>
        )}
        {direct.map((action) => {
          const spec = specs[action];
          const Icon = spec.icon;
          return (
            <button
              key={action}
              type="button"
              onClick={() => setConfirming(action)}
              aria-label={spec.label}
              title={spec.label}
              className={cn(
                "rounded-lg border border-transparent bg-white/5 p-1.5 text-muted-foreground transition-colors",
                spec.danger
                  ? "hover:border-red-500/20 hover:bg-red-500/10 hover:text-red-300"
                  : "hover:border-cyan-500/20 hover:bg-cyan-500/10 hover:text-cyan-300"
              )}
            >
              <Icon className="h-3.5 w-3.5" aria-hidden />
            </button>
          );
        })}
      </div>
    );
  }

  return (
    <div className="flex items-center gap-2">
      {direct.map((action) => {
        const spec = specs[action];
        const Icon = spec.icon;
        return (
          <Button
            key={action}
            variant="ghost"
            size="sm"
            onClick={() => setConfirming(action)}
            className={cn(
              "gap-1.5 text-xs text-muted-foreground",
              spec.danger ? "hover:bg-red-500/10 hover:text-red-300" : "hover:bg-cyan-500/10 hover:text-cyan-300"
            )}
          >
            <Icon className="h-3.5 w-3.5" aria-hidden />
            {spec.label}
          </Button>
        );
      })}
    </div>
  );
}
