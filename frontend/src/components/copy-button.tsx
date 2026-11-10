"use client";

import { useState } from "react";
import { Check, Clipboard } from "lucide-react";
import { cn } from "@/lib/utils";

/** Icon button that copies `text` and shows a check for two seconds. */
export function CopyButton({
  text,
  label = "Copy to clipboard",
  className,
}: {
  text: string;
  /** Accessible name and tooltip. */
  label?: string;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={() => {
        navigator.clipboard.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      }}
      className={cn("text-muted-foreground hover:text-cyan-400 transition-colors", className)}
      title={label}
      aria-label={label}
    >
      {copied ? <Check className="h-3 w-3 text-emerald-400" /> : <Clipboard className="h-3 w-3" />}
    </button>
  );
}
