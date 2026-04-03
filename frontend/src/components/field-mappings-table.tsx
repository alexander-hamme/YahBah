"use client";

import { useQuery } from "@tanstack/react-query";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { artifactDownloadUrl } from "@/lib/api";
import { useDevMode } from "@/components/dev-mode-context";
import type { Artifact } from "@/lib/types";

interface SubmittedValue {
  form_label: string;
  mapped_to: string;
  value: string;
}

export function FieldMappingsTable({
  artifacts,
  runId,
}: {
  artifacts: Artifact[];
  runId: string;
}) {
  const { devMode } = useDevMode();

  // Prefer submitted_values (what was actually typed into the form);
  // fall back to field_mappings (pre-fill template values) for older runs.
  const submittedArtifact = artifacts.find(
    (a) => a.artifact_type === "submitted_values"
  );
  const fallbackArtifact = artifacts.find(
    (a) => a.artifact_type === "field_mappings"
  );
  const artifact = submittedArtifact ?? fallbackArtifact;

  const { data: rows } = useQuery<SubmittedValue[]>({
    queryKey: ["submitted-values", runId, artifact?.id],
    queryFn: async () => {
      if (!artifact) return [];
      const url = artifactDownloadUrl(runId, artifact.id);
      const res = await fetch(url);
      if (!res.ok) return [];
      const data = await res.json();
      return Array.isArray(data) ? data : [];
    },
    enabled: !!artifact,
  });

  if (!artifact || !rows || rows.length === 0) return null;

  return (
    <div className="glass-panel rounded-xl overflow-hidden">
      <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-white/10 to-transparent" />
      <div className="px-5 pt-4 pb-2">
        <h3 className="text-sm font-semibold text-foreground" style={{ fontFamily: "var(--font-heading)" }}>
          Submitted Values
        </h3>
      </div>
      <div className="px-5 pb-5">
        <div className="rounded-lg border border-white/5 overflow-hidden">
          <Table style={{ tableLayout: "fixed", width: "100%" }}>
            <TableHeader>
              <TableRow className="border-b border-white/5 bg-white/[0.02]">
                <TableHead
                  className={`font-semibold text-muted-foreground ${devMode ? "w-[25%]" : "w-[30%]"}`}
                >
                  Form Field
                </TableHead>
                {devMode && (
                  <TableHead className="w-[15%] font-semibold text-muted-foreground">Mapped To</TableHead>
                )}
                <TableHead className={`font-semibold text-muted-foreground ${devMode ? "w-[60%]" : "w-[70%]"}`}>
                  Value
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((m, i) => (
                <TableRow key={i} className="border-b border-white/[0.03]">
                  <TableCell className="text-sm font-medium whitespace-normal break-words align-top">
                    {m.form_label}
                  </TableCell>
                  {devMode && (
                    <TableCell className="text-sm text-muted-foreground font-mono whitespace-normal break-all align-top">
                      {m.mapped_to}
                    </TableCell>
                  )}
                  <TableCell className="text-sm whitespace-normal break-words align-top text-foreground/80">
                    {m.value}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </div>
    </div>
  );
}
