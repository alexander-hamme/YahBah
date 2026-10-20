import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ArtifactViewer } from "@/components/artifact-viewer";
import type { Artifact } from "@/lib/types";

const screenshot: Artifact = {
  id: "00000000-0000-0000-0000-0000000000a1",
  artifact_type: "screenshot",
  path: "artifacts/run/before_submit.png",
  metadata: { step: "before_submit" },
  created_at: "2026-10-01T00:00:00Z",
};

describe("ArtifactViewer", () => {
  it("survives artifacts arriving after an empty first render", () => {
    // Polling starts with no artifacts. A hook after the early return used to
    // throw "Rendered more hooks than during the previous render" here.
    const { rerender } = render(<ArtifactViewer artifacts={[]} runId="run-1" />);
    rerender(<ArtifactViewer artifacts={[screenshot]} runId="run-1" />);
    expect(screen.getByAltText("Before Submit")).toBeTruthy();
  });
});
