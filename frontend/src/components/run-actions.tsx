"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { XCircle, RotateCw, Trash2, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cancelRun, retryRun, deleteRun } from "@/lib/api";

interface RunActionsProps {
  runId: string;
  status: string;
  testMode?: boolean;
  compact?: boolean;
}

export function RunActions({ runId, status, testMode = false, compact = false }: RunActionsProps) {
  const queryClient = useQueryClient();
  const router = useRouter();
  const [confirmAction, setConfirmAction] = useState<"cancel" | "delete" | null>(null);

  const cancelMutation = useMutation({
    mutationFn: () => cancelRun(runId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["applications"] });
      queryClient.invalidateQueries({ queryKey: ["application-stats"] });
      queryClient.invalidateQueries({ queryKey: ["run", runId] });
      setConfirmAction(null);
    },
  });

  const retryMutation = useMutation({
    mutationFn: () => retryRun(runId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["applications"] });
      queryClient.invalidateQueries({ queryKey: ["application-stats"] });
      queryClient.invalidateQueries({ queryKey: ["run", runId] });
      queryClient.invalidateQueries({ queryKey: ["run-steps", runId] });
      queryClient.invalidateQueries({ queryKey: ["run-artifacts", runId] });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => deleteRun(runId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["applications"] });
      queryClient.invalidateQueries({ queryKey: ["application-stats"] });
      setConfirmAction(null);
      // Navigate back to list if on detail page
      router.push("/applications");
    },
  });

  const canCancel = status === "PENDING" || status === "RUNNING";
  const canRetry = status === "FAILED" || status === "DUPLICATE" || testMode;
  const canDelete = status === "FAILED" || status === "DUPLICATE" || status === "COMPLETED";

  if (!canCancel && !canRetry && !canDelete) return null;

  if (compact) {
    return (
      <div className="flex items-center gap-1" onClick={(e) => e.preventDefault()}>
        {canCancel && (
          <button
            onClick={() => cancelMutation.mutate()}
            disabled={cancelMutation.isPending}
            className="p-1.5 rounded-lg text-muted-foreground hover:text-red-400 bg-white/5 hover:bg-red-500/10 border border-transparent hover:border-red-500/20 transition-all"
            title="Cancel"
          >
            {cancelMutation.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <XCircle className="h-3.5 w-3.5" />
            )}
          </button>
        )}
        {canRetry && (
          <button
            onClick={() => retryMutation.mutate()}
            disabled={retryMutation.isPending}
            className="p-1.5 rounded-lg text-muted-foreground hover:text-cyan-400 bg-white/5 hover:bg-cyan-500/10 border border-transparent hover:border-cyan-500/20 transition-all"
            title="Retry"
          >
            {retryMutation.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <RotateCw className="h-3.5 w-3.5" />
            )}
          </button>
        )}
        {canDelete && (
          <button
            onClick={() => deleteMutation.mutate()}
            disabled={deleteMutation.isPending}
            className="p-1.5 rounded-lg text-muted-foreground hover:text-red-400 bg-white/5 hover:bg-red-500/10 border border-transparent hover:border-red-500/20 transition-all"
            title="Delete"
          >
            {deleteMutation.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Trash2 className="h-3.5 w-3.5" />
            )}
          </button>
        )}
      </div>
    );
  }

  // Full-size buttons (detail page)
  return (
    <div className="flex items-center gap-2">
      {/* Cancel */}
      {canCancel && confirmAction !== "cancel" && (
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setConfirmAction("cancel")}
          className="gap-1.5 text-xs text-muted-foreground hover:text-red-400 hover:bg-red-500/10"
        >
          <XCircle className="h-3.5 w-3.5" />
          Cancel
        </Button>
      )}
      {canCancel && confirmAction === "cancel" && (
        <div className="flex items-center gap-1.5 animate-fade-in">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => cancelMutation.mutate()}
            disabled={cancelMutation.isPending}
            className="gap-1.5 text-xs text-red-400 bg-red-500/10 hover:bg-red-500/20"
          >
            {cancelMutation.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <XCircle className="h-3.5 w-3.5" />
            )}
            Confirm Cancel
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setConfirmAction(null)}
            className="text-xs text-muted-foreground"
          >
            Nevermind
          </Button>
        </div>
      )}

      {/* Retry */}
      {canRetry && (
        <Button
          variant="ghost"
          size="sm"
          onClick={() => retryMutation.mutate()}
          disabled={retryMutation.isPending}
          className="gap-1.5 text-xs text-muted-foreground hover:text-cyan-400 hover:bg-cyan-500/10"
        >
          {retryMutation.isPending ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <RotateCw className="h-3.5 w-3.5" />
          )}
          Retry
        </Button>
      )}

      {/* Delete */}
      {canDelete && confirmAction !== "delete" && (
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setConfirmAction("delete")}
          className="gap-1.5 text-xs text-muted-foreground hover:text-red-400 hover:bg-red-500/10"
        >
          <Trash2 className="h-3.5 w-3.5" />
          Delete
        </Button>
      )}
      {canDelete && confirmAction === "delete" && (
        <div className="flex items-center gap-1.5 animate-fade-in">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => deleteMutation.mutate()}
            disabled={deleteMutation.isPending}
            className="gap-1.5 text-xs text-red-400 bg-red-500/10 hover:bg-red-500/20"
          >
            {deleteMutation.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Trash2 className="h-3.5 w-3.5" />
            )}
            Confirm Delete
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setConfirmAction(null)}
            className="text-xs text-muted-foreground"
          >
            Nevermind
          </Button>
        </div>
      )}
    </div>
  );
}
