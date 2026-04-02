"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { artifactDownloadUrl } from "@/lib/api";
import type { Artifact } from "@/lib/types";

function artifactLabel(type: string): string {
  return type.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function isImage(type: string): boolean {
  return type === "screenshot" || type === "confirmation";
}

export function ArtifactViewer({
  artifacts,
  runId,
}: {
  artifacts: Artifact[];
  runId: string;
}) {
  if (artifacts.length === 0) return null;

  const screenshots = artifacts.filter((a) => isImage(a.artifact_type));
  const others = artifacts.filter((a) => !isImage(a.artifact_type));

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-lg">Artifacts</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {screenshots.length > 0 && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {screenshots.map((a) => {
              const url = artifactDownloadUrl(runId, a.id);
              return (
                <div key={a.id} className="space-y-1">
                  <Badge variant="secondary" className="text-xs">
                    {artifactLabel(a.artifact_type)}
                  </Badge>
                  <a href={url} target="_blank" rel="noopener noreferrer">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={url}
                      alt={a.artifact_type}
                      className="rounded border w-full hover:opacity-90 transition"
                    />
                  </a>
                </div>
              );
            })}
          </div>
        )}

        {others.length > 0 && (
          <div className="space-y-2">
            {others.map((a) => {
              const url = artifactDownloadUrl(runId, a.id);
              return (
                <div
                  key={a.id}
                  className="flex items-center justify-between p-2 rounded border text-sm"
                >
                  <div className="flex items-center gap-2">
                    <Badge variant="outline" className="text-xs">
                      {artifactLabel(a.artifact_type)}
                    </Badge>
                    <span className="text-gray-500 text-xs">
                      {new Date(a.created_at).toLocaleTimeString()}
                    </span>
                  </div>
                  <a
                    href={url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-blue-600 hover:underline text-xs"
                  >
                    Download
                  </a>
                </div>
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
