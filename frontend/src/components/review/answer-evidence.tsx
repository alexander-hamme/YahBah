"use client";

import { useState } from "react";
import { AlertTriangle, CheckCircle2, FileText, Sparkles } from "lucide-react";
import { useDevMode } from "@/components/dev-mode-context";
import type { FillEvidenceRow } from "@/lib/types";
import { cn } from "@/lib/utils";

// Renders the §8 fill evidence: what YahBah intended, why (basis), and what the
// page showed afterwards (page_value, ok). Flags only steer the eye; the
// approve/reject decision is the user's and the server's.

const FILE_SOURCES: Record<string, string> = {
  resume: "Résumé file",
  cover_letter: "Cover letter file",
};

export interface EvidenceFlags {
  mismatch: boolean; // the page didn't keep the value (ok = false)
  generated: boolean; // model-written prose
  unbacked: boolean; // a factual answer with no stored basis
  isNew: boolean; // added in a later review round
  blankRequired: boolean; // a required field left blank
}

function isFileSource(source: string | null): source is keyof typeof FILE_SOURCES {
  return source != null && source in FILE_SOURCES;
}

export function evidenceFlags(row: FillEvidenceRow, isNew = false): EvidenceFlags {
  const answered = !!row.answer?.trim();
  return {
    mismatch: !row.ok,
    generated: row.generated,
    unbacked: answered && !row.generated && !isFileSource(row.source) && (row.basis ?? []).length === 0,
    isNew,
    blankRequired: row.required && !answered,
  };
}

export function needsLook(flags: EvidenceFlags): boolean {
  return flags.mismatch || flags.generated || flags.unbacked || flags.isNew || flags.blankRequired;
}

/**
 * Rows in the §8 shape pass through with defaults for missing keys. The {form_label, mapped_to, value}
 * branch reads legacy pre-0018 artifacts; remove if those runs are deleted.
 */
export function normalizeEvidenceRow(raw: Record<string, unknown>, index: number): FillEvidenceRow {
  if ("label" in raw || "answer" in raw) {
    const row = raw as Partial<FillEvidenceRow>;
    return {
      key: row.key ?? String(index),
      label: row.label ?? row.key ?? `Field ${index + 1}`,
      type: row.type ?? "text",
      required: row.required ?? false,
      options: row.options ?? [],
      answer: row.answer ?? null,
      source: row.source ?? null,
      basis: row.basis ?? [],
      generated: row.generated ?? false,
      written: row.written ?? null,
      page_value: row.page_value ?? null,
      ok: row.ok ?? true,
    };
  }
  const value = raw.value == null ? null : String(raw.value);
  return {
    key: String(index),
    label: String(raw.form_label ?? `Field ${index + 1}`),
    type: "text",
    required: false,
    options: [],
    answer: value,
    source: String(raw.mapped_to ?? "custom"),
    basis: [],
    generated: false,
    written: value,
    page_value: null,
    ok: true,
  };
}

function Chip({ className, children, title }: { className: string; children: React.ReactNode; title?: string }) {
  return (
    <span
      title={title}
      className={cn("inline-flex items-center gap-1 rounded-md border px-1.5 py-px text-[11px] font-semibold", className)}
    >
      {children}
    </span>
  );
}

export function GeneratedChip() {
  return (
    <Chip
      className="border-violet-400/30 bg-violet-500/15 text-violet-200"
      title="Written by the model for this application, not a stored fact. Check the wording."
    >
      <Sparkles className="h-3 w-3" aria-hidden />
      Generated
    </Chip>
  );
}

function ExpandableText({ text, lines }: { text: string; lines: 2 | 3 }) {
  const [open, setOpen] = useState(false);
  const long = text.length > 160 || text.split("\n").length > lines;
  return (
    <div>
      <p
        className={cn(
          "whitespace-pre-wrap break-words",
          !open && long && (lines === 2 ? "line-clamp-2" : "line-clamp-3")
        )}
      >
        {text}
      </p>
      {long && (
        <button
          type="button"
          onClick={() => setOpen(!open)}
          className="mt-0.5 text-xs font-medium text-cyan-300 hover:text-cyan-200"
          aria-expanded={open}
        >
          {open ? "Show less" : "Show all"}
        </button>
      )}
    </div>
  );
}

function AnswerValue({ row }: { row: FillEvidenceRow }) {
  if (row.source === "skip") {
    return <p className="italic text-muted-foreground">Left blank</p>;
  }
  if (isFileSource(row.source)) {
    return (
      <p className="inline-flex items-center gap-1.5 break-all text-foreground/90">
        <FileText className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
        {FILE_SOURCES[row.source]}
        {row.answer ? `: ${row.answer}` : ""}
      </p>
    );
  }
  if (!row.answer) return <p className="italic text-muted-foreground">No answer</p>;
  return <ExpandableText text={row.answer} lines={3} />;
}

function ReadBack({ row, isNew }: { row: FillEvidenceRow; isNew: boolean }) {
  const same = row.page_value != null && row.answer != null && row.page_value.trim() === row.answer.trim();
  if (isNew && row.written == null && row.page_value == null) {
    // new_fields are mapped but not filled yet: nothing to read back
    return <p className="text-xs text-muted-foreground">Filled after you approve</p>;
  }
  if (row.ok) {
    return (
      <div className="flex gap-1.5 text-xs text-muted-foreground">
        <CheckCircle2 className="mt-px h-3.5 w-3.5 shrink-0 text-emerald-400" aria-hidden />
        <div className="min-w-0">
          <span className="sr-only">Read back from the page, matches: </span>
          {row.page_value == null ? (
            <span>Not read back</span>
          ) : (
            <span className={cn("block break-words", same && "line-clamp-1")} title={row.page_value}>
              <span aria-hidden>Page shows: </span>
              {row.page_value}
            </span>
          )}
        </div>
      </div>
    );
  }
  return (
    <div className="flex gap-1.5 text-xs text-amber-200">
      <AlertTriangle className="mt-px h-3.5 w-3.5 shrink-0 text-amber-400" aria-hidden />
      <div className="min-w-0 break-words">
        <span className="font-semibold">Differs on the page: </span>
        {row.page_value == null || row.page_value === "" ? (
          <span className="italic">nothing there</span>
        ) : (
          <span className="whitespace-pre-wrap">{row.page_value}</span>
        )}
      </div>
    </div>
  );
}

function EvidenceRow({ row, isNew }: { row: FillEvidenceRow; isNew: boolean }) {
  const { devMode } = useDevMode();
  const flags = evidenceFlags(row, isNew);
  const basis = row.basis ?? [];
  return (
    <li
      data-flagged={needsLook(flags) || undefined}
      className={cn(
        "grid gap-x-5 gap-y-1.5 border-b border-white/[0.05] px-4 py-3 text-sm last:border-b-0",
        "md:grid-cols-[minmax(0,13rem)_minmax(0,1fr)_minmax(0,15rem)]",
        flags.mismatch || flags.blankRequired
          ? "bg-amber-500/[0.07]"
          : isNew
            ? "bg-cyan-500/[0.06]"
            : undefined
      )}
    >
      <div className="min-w-0">
        <p className="break-words font-medium text-foreground">
          {row.label}
          {row.required && (
            <span className="ml-0.5 text-red-300" title="Required">
              *<span className="sr-only"> (required)</span>
            </span>
          )}
        </p>
        <div className="mt-1 flex flex-wrap gap-1">
          {isNew && (
            <Chip className="border-cyan-400/40 bg-cyan-500/15 text-cyan-200" title="Added in this review round">
              New
            </Chip>
          )}
          {flags.blankRequired && (
            <Chip className="border-amber-400/40 bg-amber-500/15 text-amber-200">Required, blank</Chip>
          )}
        </div>
      </div>

      <div className="min-w-0 text-foreground/90">
        <span className="sr-only">Answer: </span>
        <AnswerValue row={row} />
        {devMode && (
          <p className="mt-1 font-mono text-[11px] text-white/40">
            source {row.source} · written {JSON.stringify(row.written)}
          </p>
        )}
      </div>

      <div className="min-w-0 space-y-1.5">
        <span className="sr-only">Evidence: </span>
        <div className="flex flex-wrap gap-1">
          {row.generated && <GeneratedChip />}
          {basis.map((key) => (
            <Chip key={key} className="border-white/10 bg-white/[0.05] font-mono font-normal text-foreground/80" title="Stored fact this answer comes from">
              {key}
            </Chip>
          ))}
          {flags.unbacked && (
            <Chip className="border-amber-400/40 bg-amber-500/15 text-amber-200" title="Factual answers should come from a stored fact">
              No stored basis
            </Chip>
          )}
        </div>
        <ReadBack row={row} isNew={isNew} />
      </div>
    </li>
  );
}

/** Fill evidence rows in form order, with what to check highlighted. */
export function AnswerEvidenceList({
  rows,
  newKeys,
  onlyFlagged = false,
  label,
}: {
  rows: readonly FillEvidenceRow[];
  newKeys?: ReadonlySet<string>;
  onlyFlagged?: boolean;
  label: string;
}) {
  const shown = onlyFlagged
    ? rows.filter((r) => needsLook(evidenceFlags(r, newKeys?.has(r.key))))
    : rows;
  if (shown.length === 0) return null;
  return (
    <div className="overflow-hidden rounded-lg border border-white/[0.07]">
      <div
        aria-hidden
        className="hidden border-b border-white/[0.07] bg-white/[0.03] px-4 py-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground md:grid md:grid-cols-[minmax(0,13rem)_minmax(0,1fr)_minmax(0,15rem)] md:gap-x-5"
      >
        <span>Question</span>
        <span>Answer</span>
        <span>Basis · read back</span>
      </div>
      <ol aria-label={label}>
        {shown.map((row) => (
          <EvidenceRow key={row.key} row={row} isNew={newKeys?.has(row.key) ?? false} />
        ))}
      </ol>
    </div>
  );
}
