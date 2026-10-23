"use client";

import { useEffect, useId, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Info, Loader2, RotateCw } from "lucide-react";
import { setHeadedFallback } from "@/lib/api";
import { RUNTIME_SETTINGS_KEY, useRuntimeSettings } from "@/lib/queries";
import { toast } from "@/lib/toast";
import { cn } from "@/lib/utils";

const NAME = "Visible-browser fallback for blocked sites";
const HELP =
  "When a job page blocks the hidden browser and no board route works, YahBah reopens it once in a " +
  "visible Chrome window, and that run stays visible. A Chrome window may open on this Mac during such " +
  "runs. Applies to new runs only.";

/**
 * Switch for `browser_headed_fallback` (contract §2), beside the mode control. The value
 * shown is always the server's: unknown when settings can't load, never a guess.
 */
export function HeadedFallbackControl() {
  const queryClient = useQueryClient();
  const id = useId();
  const [helpOpen, setHelpOpen] = useState(false);
  const helpRef = useRef<HTMLDivElement>(null);
  const { data, isPending, isError, error, refetch, isFetching } = useRuntimeSettings();

  const mutation = useMutation({
    mutationFn: setHeadedFallback,
    meta: { errorTitle: "Couldn't change the visible-browser fallback" },
    onSuccess: (settings) => {
      queryClient.setQueryData(RUNTIME_SETTINGS_KEY, settings);
      toast.success(
        `Visible-browser fallback ${settings.browser_headed_fallback ? "on" : "off"} for new runs`,
        "Runs that already started keep the setting they started with."
      );
    },
  });

  // Close the help on Escape or a click elsewhere.
  useEffect(() => {
    if (!helpOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setHelpOpen(false);
    const onClick = (e: MouseEvent) => {
      if (!helpRef.current?.contains(e.target as Node)) setHelpOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onClick);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onClick);
    };
  }, [helpOpen]);

  // An older server without the key is as unknown as an unreachable one.
  const known = typeof data?.browser_headed_fallback === "boolean";
  if (isError || (!isPending && !known)) {
    return (
      <button
        type="button"
        onClick={() => refetch()}
        title={`Can't load the visible-browser fallback setting${error ? `: ${error.message}` : ""}. Click to retry.`}
        className="inline-flex items-center gap-1.5 rounded-lg border border-amber-500/40 bg-amber-500/10 px-2.5 py-1 text-xs font-semibold text-amber-300 hover:bg-amber-500/20"
      >
        {isFetching ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
        ) : (
          <AlertTriangle className="h-3.5 w-3.5" aria-hidden />
        )}
        Visible browser unknown
        <RotateCw className="h-3 w-3 opacity-60" aria-hidden />
      </button>
    );
  }

  if (isPending) {
    // Loading: no switch yet, since a switch would have to claim on or off.
    return (
      <span aria-busy="true" className="inline-flex items-center gap-2 px-1.5 py-1 text-xs font-semibold text-muted-foreground">
        <span aria-hidden className="h-4 w-7 animate-pulse rounded-full bg-white/5" />
        Visible browser
      </span>
    );
  }

  const busy = mutation.isPending;
  const on = mutation.isPending ? mutation.variables : data.browser_headed_fallback;

  return (
    <div ref={helpRef} className="relative flex items-center gap-1.5">
      <button
        type="button"
        role="switch"
        aria-checked={on === true}
        aria-label={NAME}
        aria-describedby={`${id}-help`}
        aria-busy={busy}
        disabled={busy}
        onClick={() => mutation.mutate(!on)}
        className={cn(
          "inline-flex items-center gap-2 rounded-lg px-1.5 py-1 text-xs font-semibold text-muted-foreground hover:text-foreground",
          "focus-visible:outline-2 focus-visible:outline-cyan-400/60",
          busy && "cursor-wait"
        )}
      >
        <span
          aria-hidden
          className={cn(
            "relative inline-flex h-4 w-7 shrink-0 rounded-full border transition-colors",
            on ? "border-cyan-400/50 bg-cyan-500/40" : "border-white/15 bg-white/10"
          )}
        >
          <span
            className={cn(
              "absolute top-0.5 h-2.5 w-2.5 rounded-full bg-white transition-transform motion-reduce:transition-none",
              on ? "translate-x-3.5" : "translate-x-0.5"
            )}
          />
        </span>
        <span className="inline-flex items-center gap-1">
          {mutation.isPending && <Loader2 className="h-3 w-3 animate-spin" aria-hidden />}
          Visible browser
          <span className={on ? "text-cyan-200" : undefined}>{on ? "on" : "off"}</span>
        </span>
      </button>
      <button
        type="button"
        onClick={() => setHelpOpen(!helpOpen)}
        aria-expanded={helpOpen}
        aria-controls={`${id}-popover`}
        aria-label="About the visible-browser fallback"
        className="rounded-md p-0.5 text-muted-foreground hover:text-foreground"
      >
        <Info className="h-3.5 w-3.5" aria-hidden />
      </button>
      <span id={`${id}-help`} className="sr-only">
        {HELP}
      </span>
      {helpOpen && (
        <div
          id={`${id}-popover`}
          className="absolute right-0 top-full z-50 mt-2 w-72 rounded-xl border border-white/10 bg-[#0d121c]/95 p-3 text-xs leading-relaxed text-foreground/85 shadow-xl backdrop-blur-md"
        >
          <p className="mb-1 font-semibold text-foreground">{NAME}</p>
          <p>{HELP}</p>
        </div>
      )}
    </div>
  );
}
