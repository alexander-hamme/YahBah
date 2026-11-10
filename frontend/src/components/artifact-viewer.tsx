"use client";

import { useState, useCallback } from "react";
import { TransformWrapper, TransformComponent } from "react-zoom-pan-pinch";
import Lightbox from "yet-another-react-lightbox";
import Zoom from "yet-another-react-lightbox/plugins/zoom";
import "yet-another-react-lightbox/styles.css";
import dynamic from "next/dynamic";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";

const PdfDocument = dynamic(
  () => import("react-pdf").then((mod) => {
    mod.pdfjs.GlobalWorkerOptions.workerSrc = "/pdf.worker.min.mjs";
    return mod.Document;
  }),
  { ssr: false }
);
const PdfPage = dynamic(
  () => import("react-pdf").then((mod) => mod.Page),
  { ssr: false }
);
import { Maximize2, Download, Eye, EyeOff, FileDown } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { artifactDownloadUrl } from "@/lib/api";
import { useDevMode } from "@/components/dev-mode-context";
import type { Artifact, Outcome } from "@/lib/types";

// Screenshot labels written by workflows/activities/browser.py.
const SCREENSHOT_LABELS: Record<string, string> = {
  form_found: "Form Found",
  before_fill: "Before Fill",
  before_submit: "Before Submit",
  confirmation: "After Submit",
  discover_failed: "Discover Failed",
  map_failed: "Mapping Failed",
  fill_failed: "Fill Failed",
  submit_failed: "Submit Failed",
};

// Each fill attempt saves a full-page capture labelled "filled_<round>_<seq>-<attempt>"
// (workflows/activities/browser.py). Returns the review round, or null for any other label.
const FILLED_STEP = /^filled_(\d+)_\d+-\d+$/;

function filledRound(step: string): number | null {
  const m = FILLED_STEP.exec(step);
  return m ? Number(m[1]) : null;
}

function screenshotLabel(step: string): string {
  const round = filledRound(step);
  if (round != null) return `Filled (review round ${round})`;
  return SCREENSHOT_LABELS[step] ?? step.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

const OUTCOME_SLOT_MESSAGE: Record<Outcome, string> = {
  queued: "In progress...",
  in_progress: "In progress...",
  awaiting_review: "Waiting for your review",
  test_only: "Not submitted (testing mode)",
  submitted: "No confirmation screenshot",
  email_verified: "No confirmation screenshot",
  submit_uncertain: "No screenshot after submit",
  failed: "Not submitted",
  cancelled: "Not submitted",
  expired: "Not submitted",
  duplicate: "Not submitted",
};

function screenshotByStep(screenshots: Artifact[]): Map<string, Artifact> {
  const byStep = new Map<string, Artifact>();
  for (const a of screenshots) {
    const step = (a.metadata as Record<string, string>)?.step;
    if (step) byStep.set(step, a);
  }
  return byStep;
}

// Left slot: latest progress screenshot (upgrades as workflow progresses)
const LEFT_PRIORITY = ["before_fill", "form_found"];

/** The newest fill capture: the highest review round, then the latest registered within it. */
export function latestFilled(screenshots: readonly Artifact[]): Artifact | null {
  let best: { round: number; artifact: Artifact } | null = null;
  for (const artifact of screenshots) {
    const round = filledRound(String(artifact.metadata?.step ?? ""));
    if (round == null) continue;
    if (
      !best ||
      round > best.round ||
      (round === best.round && artifact.created_at >= best.artifact.created_at)
    ) {
      best = { round, artifact };
    }
  }
  return best?.artifact ?? null;
}

function pickLeftScreenshot(byStep: Map<string, Artifact>, screenshots: readonly Artifact[]): Artifact | null {
  const before = byStep.get("before_submit");
  if (before) return before;
  const filled = latestFilled(screenshots);
  if (filled) return filled;
  for (const step of LEFT_PRIORITY) {
    const a = byStep.get(step);
    if (a) return a;
  }
  return null;
}

// Right slot: outcome screenshot (appears when workflow completes or fails)
const RIGHT_PRIORITY = ["confirmation", "submit_failed", "fill_failed", "map_failed", "discover_failed"];

function pickRightScreenshot(byStep: Map<string, Artifact>): Artifact | null {
  for (const step of RIGHT_PRIORITY) {
    const a = byStep.get(step);
    if (a) return a;
  }
  return null;
}

function ScreenshotThumbnail({
  url,
  label,
  onExpand,
}: {
  url: string;
  label: string;
  onExpand: () => void;
}) {
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between">
        <Badge variant="secondary" className="text-xs">
          {label}
        </Badge>
        <button
          onClick={onExpand}
          className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium text-cyan-400 bg-cyan-500/10 border border-cyan-500/20 hover:bg-cyan-500/20 hover:shadow-[0_0_12px_rgba(56,189,248,0.15)] transition-all"
        >
          <Maximize2 className="h-3 w-3" />
          Fullscreen
        </button>
      </div>
      <TransformWrapper
        initialScale={1}
        minScale={1}
        maxScale={4}
        wheel={{ step: 0.1 }}
        doubleClick={{ mode: "reset" }}
      >
        <TransformComponent
          wrapperStyle={{ width: "100%", maxHeight: "400px", overflow: "hidden" }}
          contentStyle={{ width: "100%" }}
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={url}
            alt={label}
            className="rounded border border-white/10 w-full cursor-grab active:cursor-grabbing"
          />
        </TransformComponent>
      </TransformWrapper>
      <p className="text-xs text-muted-foreground text-center">
        Scroll to zoom &middot; drag to pan &middot; double-click to reset
      </p>
    </div>
  );
}

function ScreenshotPlaceholder({
  label,
  message = "Waiting...",
}: {
  label: string;
  message?: string;
}) {
  return (
    <div className="space-y-1">
      <Badge variant="secondary" className="text-xs">
        {label}
      </Badge>
      <div className="rounded-lg border border-white/5 bg-white/[0.02] flex items-center justify-center h-48 text-sm text-muted-foreground">
        {message}
      </div>
    </div>
  );
}

function CoverLetterViewer({
  runId,
  artifact,
}: {
  runId: string;
  artifact: Artifact;
}) {
  const [expanded, setExpanded] = useState(false);
  const [numPages, setNumPages] = useState<number>(0);
  const url = artifactDownloadUrl(runId, artifact.id);

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between">
        <Badge variant="outline" className="text-xs">
          Cover Letter
        </Badge>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setExpanded(!expanded)}
            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium text-cyan-400 bg-cyan-500/10 border border-cyan-500/20 hover:bg-cyan-500/20 hover:shadow-[0_0_12px_rgba(56,189,248,0.15)] transition-all"
          >
            {expanded ? <><EyeOff className="h-3 w-3" />Hide</> : <><Eye className="h-3 w-3" />Preview</>}
          </button>
          <a
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium text-cyan-400 bg-cyan-500/10 border border-cyan-500/20 hover:bg-cyan-500/20 hover:shadow-[0_0_12px_rgba(56,189,248,0.15)] transition-all"
          >
            <FileDown className="h-3 w-3" />
            Download PDF
          </a>
        </div>
      </div>
      {expanded && (
        <div className="rounded-lg border border-white/5 bg-white/[0.02] p-4 overflow-y-auto max-h-[600px]">
          <PdfDocument
            file={url}
            onLoadSuccess={({ numPages: n }: { numPages: number }) => setNumPages(n)}
            loading={
              <div className="text-sm text-gray-400 text-center py-8">
                Loading PDF...
              </div>
            }
            error={
              <div className="text-sm text-red-500 text-center py-8">
                Failed to load PDF
              </div>
            }
          >
            {Array.from({ length: numPages }, (_, i) => (
              <PdfPage
                key={i}
                pageNumber={i + 1}
                width={560}
                className="mb-4 last:mb-0"
              />
            ))}
          </PdfDocument>
        </div>
      )}
    </div>
  );
}

export function ArtifactViewer({
  artifacts,
  runId,
  outcome,
}: {
  artifacts: Artifact[];
  runId: string;
  outcome?: Outcome;
}) {
  const { devMode } = useDevMode();
  const [lightboxIndex, setLightboxIndex] = useState(-1);
  const openLightbox = useCallback(
    (index: number) => setLightboxIndex(index),
    []
  );

  if (artifacts.length === 0) return null;

  const screenshots = artifacts.filter((a) => a.artifact_type === "screenshot");
  const byStep = screenshotByStep(screenshots);
  const leftShot = pickLeftScreenshot(byStep, screenshots);
  const rightShot = pickRightScreenshot(byStep);

  // Build lightbox slides from the two primary slots + all in dev mode
  const primarySlots = [leftShot, rightShot].filter(Boolean) as Artifact[];
  const lightboxSources = devMode ? screenshots : primarySlots;
  const lightboxSlides = lightboxSources.map((a) => ({
    src: artifactDownloadUrl(runId, a.id),
  }));

  // Inline content artifacts
  const coverLetter = artifacts.find((a) => a.artifact_type === "cover_letter");

  // Everything else — confirmation is now shown in RunInfoCard
  const inlineTypes = new Set(["screenshot", "confirmation", "confirmation_html", "cover_letter"]);
  const others = artifacts.filter((a) => !inlineTypes.has(a.artifact_type));
  const downloadable = devMode
    ? others
    : others.filter(
        (a) =>
          a.artifact_type !== "submitted_values" &&
          a.artifact_type !== "form_schema" &&
          a.artifact_type !== "answer_plan"
      );

  return (
    <div className="glass-panel rounded-xl overflow-hidden">
      <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-white/10 to-transparent" />
      <div className="px-5 pt-4 pb-2">
        <h3 className="text-sm font-semibold text-foreground" style={{ fontFamily: "var(--font-heading)" }}>
          Artifacts
        </h3>
      </div>
      <div className="px-5 pb-5 space-y-4">
        {devMode ? (
          /* Dev mode: show all screenshots in a grid */
          screenshots.length > 0 && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {screenshots.map((a, i) => {
                const url = artifactDownloadUrl(runId, a.id);
                const step =
                  (a.metadata as Record<string, string>)?.step ?? a.artifact_type;
                return (
                  <ScreenshotThumbnail
                    key={a.id}
                    url={url}
                    label={screenshotLabel(step)}
                    onExpand={() => openLightbox(i)}
                  />
                );
              })}
            </div>
          )
        ) : (
          /* Normal mode: two fixed slots */
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {leftShot ? (
              <ScreenshotThumbnail
                url={artifactDownloadUrl(runId, leftShot.id)}
                label={screenshotLabel(
                  (leftShot.metadata as Record<string, string>)?.step ?? "progress"
                )}
                onExpand={() => openLightbox(0)}
              />
            ) : (
              <ScreenshotPlaceholder label="Progress" message="In Progress..." />
            )}
            {rightShot ? (
              <ScreenshotThumbnail
                url={artifactDownloadUrl(runId, rightShot.id)}
                label={screenshotLabel(
                  (rightShot.metadata as Record<string, string>)?.step ?? "outcome"
                )}
                onExpand={() => openLightbox(leftShot ? 1 : 0)}
              />
            ) : (
              <ScreenshotPlaceholder label="Outcome" message={outcome ? OUTCOME_SLOT_MESSAGE[outcome] : "Waiting..."} />
            )}
          </div>
        )}

        <Lightbox
          open={lightboxIndex >= 0}
          close={() => setLightboxIndex(-1)}
          index={lightboxIndex}
          slides={lightboxSlides}
          plugins={[Zoom]}
          zoom={{ maxZoomPixelRatio: 5, scrollToZoom: true }}
        />

        {coverLetter && (
          <CoverLetterViewer runId={runId} artifact={coverLetter} />
        )}

        {downloadable.length > 0 && (
          <div className="space-y-2">
            {downloadable.map((a) => {
              const url = artifactDownloadUrl(runId, a.id);
              return (
                <div
                  key={a.id}
                  className="flex items-center justify-between p-2 rounded-lg border border-white/5 bg-white/[0.02] text-sm"
                >
                  <div className="flex items-center gap-2">
                    <Badge variant="outline" className="text-xs">
                      {a.artifact_type
                        .replace(/_/g, " ")
                        .replace(/\b\w/g, (c) => c.toUpperCase())}
                    </Badge>
                    <span className="text-gray-500 text-xs">
                      {new Date(a.created_at).toLocaleTimeString()}
                    </span>
                  </div>
                  <a
                    href={url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium text-cyan-400 bg-cyan-500/10 border border-cyan-500/20 hover:bg-cyan-500/20 hover:shadow-[0_0_12px_rgba(56,189,248,0.15)] transition-all"
                  >
                    <Download className="h-3 w-3" />
                    Download
                  </a>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
