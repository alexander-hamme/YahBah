"use client";

import Link from "next/link";
import { AlertCircle, ArrowRight } from "lucide-react";
import type { ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";

const RUN_LINK_LABEL: Record<string, string> = {
  already_applied: "See the earlier application",
  run_in_flight: "See the active run",
};

/** The §8 error body in words: its message, a link to the run it names, and the code. */
export function ApiErrorBody({ error }: { error: ApiError }) {
  const linkLabel = RUN_LINK_LABEL[error.code] ?? "Open the run";
  return (
    <>
      <p className="text-sm">{error.message}</p>
      <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1">
        {error.runId && (
          <Link
            href={`/applications/${error.runId}`}
            className="inline-flex items-center gap-1 text-xs font-semibold text-cyan-300 hover:text-cyan-200 underline-offset-2 hover:underline"
          >
            {linkLabel}
            <ArrowRight className="h-3 w-3" aria-hidden />
          </Link>
        )}
        <code className="text-[11px] text-white/35">{error.code}</code>
      </div>
    </>
  );
}

/** Inline error, for forms and panels. */
export function ApiErrorMessage({
  error,
  title,
  className,
}: {
  error: ApiError;
  title?: string;
  className?: string;
}) {
  return (
    <div
      role="alert"
      className={cn(
        "flex gap-2.5 rounded-lg border border-red-500/25 bg-red-500/10 px-3 py-2 text-red-200",
        className
      )}
    >
      <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-red-400" aria-hidden />
      <div className="min-w-0">
        {title && <p className="text-sm font-semibold text-red-300">{title}</p>}
        <ApiErrorBody error={error} />
      </div>
    </div>
  );
}
