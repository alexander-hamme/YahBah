import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ArtifactViewer } from "@/components/artifact-viewer";
import { latestEvidence } from "@/components/field-mappings-table";
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

function fillShot(step: string, n: number, created_at: string): Artifact {
  return {
    id: `00000000-0000-0000-0000-0000000000f${n}`,
    artifact_type: "screenshot",
    path: `artifacts/run/${step}.png`,
    metadata: { step, review_round: Number(step.split("_")[1]) },
    created_at,
  };
}

describe("ArtifactViewer fill captures", () => {
  it("shows the newest fill capture: highest round, then the latest registered", () => {
    const shots = [
      fillShot("filled_1_1-1", 1, "2026-10-19T10:00:00+00:00"),
      fillShot("filled_2_1-1", 2, "2026-10-19T11:00:00+00:00"),
      fillShot("filled_2_2-2", 3, "2026-10-19T11:05:00+00:00"), // a retried attempt in round 2
      fillShot("filled_1_2-1", 4, "2026-10-19T12:00:00+00:00"), // registered later, but an older round
    ];
    render(<ArtifactViewer artifacts={shots} runId="run-1" />);

    const img = screen.getByAltText("Filled (review round 2)") as HTMLImageElement;
    expect(img.getAttribute("src")).toBe("/api/runs/run-1/artifacts/00000000-0000-0000-0000-0000000000f3/download");
  });

  it("doesn't read other labels as fill captures", () => {
    render(<ArtifactViewer artifacts={[fillShot("filled_3", 5, "2026-10-19T10:00:00+00:00")]} runId="run-1" />);
    expect(screen.queryByAltText(/Filled \(review round/)).toBeNull();
  });
});

describe("fill evidence file", () => {
  it("uses the newest submitted_values: highest round, then the latest registered", () => {
    const evidence = (n: number, review_round: number, created_at: string): Artifact => ({
      id: `e${n}`,
      artifact_type: "submitted_values",
      path: `artifacts/run/submitted_values_${review_round}_${n}-1.json`,
      metadata: { review_round },
      created_at,
    });
    const files = [
      evidence(1, 1, "2026-10-19T10:00:00+00:00"),
      evidence(2, 2, "2026-10-19T11:00:00+00:00"),
      evidence(3, 2, "2026-10-19T11:05:00+00:00"),
      evidence(4, 1, "2026-10-19T12:00:00+00:00"),
    ];
    expect(latestEvidence(files)?.id).toBe("e3");
    expect(latestEvidence([])).toBeUndefined();
  });
});
