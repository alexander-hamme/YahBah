"use client";

import { useState, useCallback } from "react";
import { useQuery } from "@tanstack/react-query";
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
import type { Artifact } from "@/lib/types";

function screenshotLabel(step: string): string {
  switch (step) {
    case "before_submit":
      return "Before Submit";
    case "before_fill":
      return "Before Fill";
    case "submit_failed":
      return "Submit Failed";
    case "confirmation":
      return "After Submit";
    case "extract_form":
      return "After Extract";
    default:
      return step.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  }
}

function screenshotByStep(screenshots: Artifact[]): Map<string, Artifact> {
  const byStep = new Map<string, Artifact>();
  for (const a of screenshots) {
    const step = (a.metadata as Record<string, string>)?.step;
    if (step) byStep.set(step, a);
  }
  return byStep;
}

// Left slot: latest progress screenshot (upgrades as workflow progresses)
const LEFT_PRIORITY = ["before_submit", "before_fill", "extract_form", "after_auth"];

function pickLeftScreenshot(byStep: Map<string, Artifact>): Artifact | null {
  for (const step of LEFT_PRIORITY) {
    const a = byStep.get(step);
    if (a) return a;
  }
  return null;
}

// Right slot: outcome screenshot (appears when workflow completes or fails)
function pickRightScreenshot(byStep: Map<string, Artifact>): Artifact | null {
  return byStep.get("confirmation") ?? byStep.get("submit_failed") ?? null;
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

function InlineText({
  runId,
  artifact,
  label,
}: {
  runId: string;
  artifact: Artifact;
  label: string;
}) {
  const url = artifactDownloadUrl(runId, artifact.id);
  const { data: text } = useQuery<string>({
    queryKey: ["artifact-text", artifact.id],
    queryFn: async () => {
      const res = await fetch(url);
      if (!res.ok) return "";
      return res.text();
    },
  });

  if (!text) return null;

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between">
        <Badge variant="outline" className="text-xs">
          {label}
        </Badge>
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
      <pre className="text-sm whitespace-pre-wrap bg-white/[0.02] border border-white/5 rounded-lg p-3 max-h-64 overflow-y-auto text-foreground/80">
        {text}
      </pre>
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
}: {
  artifacts: Artifact[];
  runId: string;
}) {
  const { devMode } = useDevMode();
  const [lightboxIndex, setLightboxIndex] = useState(-1);

  if (artifacts.length === 0) return null;

  const screenshots = artifacts.filter((a) => a.artifact_type === "screenshot");
  const byStep = screenshotByStep(screenshots);
  const leftShot = pickLeftScreenshot(byStep);
  const rightShot = pickRightScreenshot(byStep);

  // Build lightbox slides from the two primary slots + all in dev mode
  const primarySlots = [leftShot, rightShot].filter(Boolean) as Artifact[];
  const lightboxSources = devMode ? screenshots : primarySlots;
  const lightboxSlides = lightboxSources.map((a) => ({
    src: artifactDownloadUrl(runId, a.id),
  }));

  const openLightbox = useCallback(
    (index: number) => setLightboxIndex(index),
    []
  );

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
          a.artifact_type !== "field_mappings"
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
              <ScreenshotPlaceholder label="Outcome" message="Submission Pending" />
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
