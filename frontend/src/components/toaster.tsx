"use client";

import { useSyncExternalStore } from "react";
import { AlertCircle, CheckCircle2, Info, X } from "lucide-react";
import { ApiErrorBody } from "@/components/api-error-message";
import { dismissToast, getToasts, subscribeToasts, type ToastItem } from "@/lib/toast";
import { cn } from "@/lib/utils";

const NO_TOASTS: readonly ToastItem[] = [];

const KIND_STYLE = {
  success: { icon: CheckCircle2, className: "border-emerald-500/30 text-emerald-200", iconClass: "text-emerald-400" },
  info: { icon: Info, className: "border-cyan-500/30 text-cyan-100", iconClass: "text-cyan-400" },
  error: { icon: AlertCircle, className: "border-red-500/35 text-red-200", iconClass: "text-red-400" },
} as const;

function ToastCard({ item }: { item: ToastItem }) {
  const style = KIND_STYLE[item.kind];
  const Icon = style.icon;
  return (
    <div
      role={item.kind === "error" ? "alert" : "status"}
      className={cn(
        "pointer-events-auto flex w-full gap-2.5 rounded-xl border bg-[#0d121c]/95 px-3.5 py-3 shadow-xl backdrop-blur-md animate-fade-in",
        style.className
      )}
    >
      <Icon className={cn("mt-0.5 h-4 w-4 shrink-0", style.iconClass)} aria-hidden />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold">{item.title}</p>
        {item.body && <p className="mt-0.5 text-sm text-foreground/75">{item.body}</p>}
        {item.error && (
          <div className="mt-0.5 text-foreground/80">
            <ApiErrorBody error={item.error} />
          </div>
        )}
      </div>
      <button
        type="button"
        onClick={() => dismissToast(item.id)}
        className="-m-1 h-6 w-6 shrink-0 rounded-md p-1 text-white/40 hover:bg-white/10 hover:text-white"
        aria-label="Dismiss notification"
      >
        <X className="h-4 w-4" aria-hidden />
      </button>
    </div>
  );
}

export function Toaster() {
  const items = useSyncExternalStore(subscribeToasts, getToasts, () => NO_TOASTS);
  return (
    <section
      aria-label="Notifications"
      className="pointer-events-none fixed bottom-4 right-4 z-[60] flex w-[min(24rem,calc(100vw-2rem))] flex-col gap-2"
    >
      {items.map((item) => (
        <ToastCard key={item.id} item={item} />
      ))}
    </section>
  );
}
