"use client";

import { CopyButton } from "@/components/copy-button";
import { artifactDownloadUrl } from "@/lib/api";
import { useQuery } from "@/lib/queries";
import type { Artifact, UnresolvedQuestion, UnresolvedStatus } from "@/lib/types";

// The blocking questions of a `needs_answer` run, read from its newest
// `answer_plan` artifact. The artifact holds fact ids and templates, never fact
// values. Fixing them is the user's job: edit personal.yaml, then retry.

const STATUS_LABEL: Record<UnresolvedStatus, string> = {
  missing_fact: "No stored fact",
  needs_clarification: "Unclear question",
  unsupported: "Couldn't be backed by your facts",
  invalid_value: "No valid answer for this field",
};

/** The newest `answer_plan`: a later answering pass (fill, new fields) supersedes the first one. */
export function latestAnswerPlan(artifacts: readonly Artifact[]): Artifact | undefined {
  return artifacts
    .filter((a) => a.artifact_type === "answer_plan")
    .reduce<Artifact | undefined>((best, a) => (!best || a.created_at >= best.created_at ? a : best), undefined);
}

const text = (v: unknown): string => (typeof v === "string" ? v : "");

function parseUnresolved(data: unknown): UnresolvedQuestion[] {
  const rows = (data as { unresolved?: unknown } | null)?.unresolved;
  if (!Array.isArray(rows)) throw new Error("answer_plan has no unresolved list");
  return rows.map((raw: Record<string, unknown>, i) => ({
    key: text(raw.key) || String(i),
    label: text(raw.label) || text(raw.key) || `Question ${i + 1}`,
    type: text(raw.type),
    required: raw.required === true,
    status: text(raw.status) as UnresolvedStatus,
    reason: text(raw.reason),
    requested_info: text(raw.requested_info),
    fact_template: text(raw.fact_template),
  }));
}

const HEADING_ID = "unanswered-questions";

function Panel({ children, busy = false }: { children: React.ReactNode; busy?: boolean }) {
  return (
    <section aria-labelledby={HEADING_ID} aria-busy={busy} className="glass-panel rounded-xl px-5 pb-5 pt-4 space-y-3">
      <h3 id={HEADING_ID} className="text-sm font-semibold text-foreground" style={{ fontFamily: "var(--font-heading)" }}>
        Questions YahBah couldn&apos;t answer
      </h3>
      {children}
    </section>
  );
}

function Question({ q }: { q: UnresolvedQuestion }) {
  return (
    <li className="space-y-1.5 py-3 first:pt-0 last:pb-0">
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <p className="text-sm font-medium text-foreground">{q.label}</p>
        <span className="inline-flex items-center rounded-full border border-amber-500/25 bg-amber-500/10 px-2 py-0.5 text-xs font-medium text-amber-300">
          {STATUS_LABEL[q.status] ?? q.status.replace(/_/g, " ")}
        </span>
      </div>
      {q.reason && <p className="text-sm text-foreground/75">{q.reason}</p>}
      {q.requested_info && <p className="text-sm text-foreground/75">Needed: {q.requested_info}</p>}
      {q.fact_template && (
        <div className="relative">
          <pre
            aria-label={`Fact template for ${q.label}`}
            className="overflow-x-auto rounded-lg border border-white/10 bg-black/30 p-3 pr-9 font-mono text-xs text-foreground/90"
          >
            {q.fact_template}
          </pre>
          <CopyButton
            text={q.fact_template}
            label={`Copy fact template for ${q.label}`}
            className="absolute right-2.5 top-2.5"
          />
        </div>
      )}
    </li>
  );
}

/**
 * Why a `needs_answer` run stopped: each required question YahBah couldn't answer, what's needed,
 * and a YAML fact skeleton to paste into `config/personal.yaml`. `artifacts` undefined: still loading.
 */
export function UnansweredQuestions({ artifacts, runId }: { artifacts: readonly Artifact[] | undefined; runId: string }) {
  const artifact = artifacts && latestAnswerPlan(artifacts);

  const { data, isError } = useQuery<UnresolvedQuestion[]>({
    queryKey: ["answer-plan", runId, artifact?.id],
    queryFn: async () => {
      const res = await fetch(artifactDownloadUrl(runId, artifact!.id));
      if (!res.ok) throw new Error(`answer_plan download failed: ${res.status}`);
      return parseUnresolved(await res.json());
    },
    enabled: !!artifact,
    staleTime: Infinity, // an artifact file never changes
  });

  if (artifacts && (!artifact || isError)) {
    return (
      <Panel>
        <p className="text-sm text-muted-foreground">Couldn&apos;t load the unanswered questions.</p>
      </Panel>
    );
  }

  if (!data) {
    return (
      <Panel busy>
        <div className="h-4 w-1/2 animate-pulse rounded bg-white/5" />
        <div className="h-16 animate-pulse rounded-lg bg-white/5" />
      </Panel>
    );
  }

  const blocking = data.filter((q) => q.required);
  if (blocking.length === 0) return null;

  return (
    <Panel>
      <ul className="divide-y divide-white/10">
        {blocking.map((q) => (
          <Question key={q.key} q={q} />
        ))}
      </ul>
      <p className="text-xs text-muted-foreground">
        Add the facts to the <code className="font-mono">facts:</code> list in{" "}
        <code className="font-mono">config/personal.yaml</code>, then retry.
      </p>
    </Panel>
  );
}
