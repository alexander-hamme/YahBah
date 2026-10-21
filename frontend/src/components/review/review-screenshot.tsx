"use client";

import { useState } from "react";
import { ImageOff, Maximize2 } from "lucide-react";
import Lightbox from "yet-another-react-lightbox";
import Zoom from "yet-another-react-lightbox/plugins/zoom";
import "yet-another-react-lightbox/styles.css";
import { artifactDownloadUrl } from "@/lib/api";
import { cn } from "@/lib/utils";

/** The round's full-page capture of the filled form: scrolls in place, opens full size. */
export function ReviewScreenshot({
  runId,
  artifactId,
  reviewRound,
  compact = false,
}: {
  runId: string;
  artifactId: string | null;
  reviewRound: number;
  /** A short thumbnail of the top of the page (triage preview); still opens full size. */
  compact?: boolean;
}) {
  const [open, setOpen] = useState(false);

  if (!artifactId) {
    return (
      <div
        className={cn(
          "flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-white/10 text-sm text-muted-foreground",
          compact ? "h-24 text-xs" : "h-48"
        )}
      >
        <ImageOff className="h-5 w-5" aria-hidden />
        No screenshot was saved for this round.
      </div>
    );
  }

  const src = artifactDownloadUrl(runId, artifactId);
  const alt = `Screenshot of the filled form, full page (review round ${reviewRound})`;
  return (
    <figure className="space-y-1.5">
      <figcaption className="flex items-center justify-between text-xs text-muted-foreground">
        <span>Filled form · full page</span>
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="inline-flex items-center gap-1 font-medium text-cyan-300 hover:text-cyan-200"
        >
          <Maximize2 className="h-3 w-3" aria-hidden />
          Full size
        </button>
      </figcaption>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className={cn(
          "block w-full rounded-lg border border-white/10 bg-white focus-visible:outline-2 focus-visible:outline-cyan-400",
          compact ? "h-44 overflow-hidden" : "max-h-[calc(100vh-14rem)] overflow-y-auto"
        )}
        aria-label="Open the screenshot full size"
      >
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={src} alt={alt} className="block w-full" />
      </button>
      <Lightbox
        open={open}
        close={() => setOpen(false)}
        slides={[{ src, alt }]}
        plugins={[Zoom]}
        zoom={{ maxZoomPixelRatio: 5, scrollToZoom: true }}
        render={{ buttonPrev: () => null, buttonNext: () => null }}
      />
    </figure>
  );
}
