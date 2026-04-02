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
import type { Artifact, FieldMapping } from "@/lib/types";

export function FieldMappingsTable({
  artifacts,
  runId,
}: {
  artifacts: Artifact[];
  runId: string;
}) {
  const { devMode } = useDevMode();

  const mappingsArtifact = artifacts.find(
    (a) => a.artifact_type === "field_mappings"
  );

  const { data: mappings } = useQuery<FieldMapping[]>({
    queryKey: ["field-mappings", runId],
    queryFn: async () => {
      if (!mappingsArtifact) return [];
      const url = artifactDownloadUrl(runId, mappingsArtifact.id);
      const res = await fetch(url);
      if (!res.ok) return [];
      const data = await res.json();
      return Array.isArray(data) ? data : [];
    },
    enabled: !!mappingsArtifact,
  });

  if (!mappingsArtifact || !mappings || mappings.length === 0) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-lg">Submitted Values</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="rounded-md border overflow-hidden">
          <Table style={{ tableLayout: "fixed", width: "100%" }}>
            <TableHeader>
              <TableRow>
                <TableHead
                  className={devMode ? "w-[25%]" : "w-[30%]"}
                >
                  Form Field
                </TableHead>
                {devMode && (
                  <TableHead className="w-[15%]">Mapped To</TableHead>
                )}
                <TableHead className={devMode ? "w-[60%]" : "w-[70%]"}>
                  Value
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {mappings.map((m, i) => (
                <TableRow key={i}>
                  <TableCell className="text-sm font-medium whitespace-normal break-words align-top">
                    {m.form_label}
                  </TableCell>
                  {devMode && (
                    <TableCell className="text-sm text-gray-500 font-mono whitespace-normal break-all align-top">
                      {m.mapped_to}
                    </TableCell>
                  )}
                  <TableCell className="text-sm whitespace-normal break-words align-top">
                    {m.value}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </CardContent>
    </Card>
  );
}
