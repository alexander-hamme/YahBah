"use client";

import { useQuery } from "@/lib/queries";
import { AnswerEvidenceList, normalizeEvidenceRow } from "@/components/review/answer-evidence";
import { artifactDownloadUrl } from "@/lib/api";
import type { Artifact, FillEvidenceRow, Outcome } from "@/lib/types";

/** One `submitted_values` file per fill attempt: the newest is the highest review round, then the latest registered. */
export function latestEvidence(artifacts: readonly Artifact[]): Artifact | undefined {
  const round = (a: Artifact) => Number(a.metadata?.review_round ?? 0);
  return artifacts
    .filter((a) => a.artifact_type === "submitted_values")
    .reduce<Artifact | undefined>(
      (best, a) =>
        !best || round(a) > round(best) || (round(a) === round(best) && a.created_at >= best.created_at) ? a : best,
      undefined
    );
}

/** The run's fill evidence (`submitted_values`, contract §8): each answer, its basis and the read-back. */
export function FieldMappingsTable({
  artifacts,
  runId,
  outcome,
}: {
  artifacts: Artifact[];
  runId: string;
  outcome: Outcome;
}) {
  const artifact = latestEvidence(artifacts);

  const { data: rows } = useQuery<FillEvidenceRow[]>({
    queryKey: ["submitted-values", runId, artifact?.id],
    queryFn: async () => {
      if (!artifact) return [];
      const res = await fetch(artifactDownloadUrl(runId, artifact.id));
      if (!res.ok) return [];
      const data: unknown = await res.json();
      return Array.isArray(data) ? data.map((row, i) => normalizeEvidenceRow(row, i)) : [];
    },
    enabled: !!artifact,
  });

  if (!artifact || !rows || rows.length === 0) return null;

  const title = outcome === "submitted" || outcome === "email_verified" ? "Submitted values" : "Filled values";
  return (
    <section aria-labelledby="fill-evidence" className="glass-panel rounded-xl px-5 pb-5 pt-4 space-y-3">
      <h3 id="fill-evidence" className="text-sm font-semibold text-foreground" style={{ fontFamily: "var(--font-heading)" }}>
        {title}
      </h3>
      <AnswerEvidenceList rows={rows} label={title} />
    </section>
  );
}
