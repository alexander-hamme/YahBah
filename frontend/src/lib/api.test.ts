import { describe, expect, it, vi } from "vitest";
import { ApiError, applicationsQuery, getApplications, getRunDetail } from "@/lib/api";
import { mockApi } from "@/test/render";

describe("API errors", () => {
  it("reads the §8 error body: code, message and run_id", async () => {
    mockApi({
      "GET /runs/r1": {
        status: 409,
        body: { detail: { code: "already_applied", message: "Already applied to this job.", run_id: "r0" } },
      },
    });
    const err = await getRunDetail("r1").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 409, code: "already_applied", message: "Already applied to this job.", runId: "r0" });
  });

  it("keeps a plain FastAPI detail string as the message", async () => {
    mockApi({ "GET /runs/r1": { status: 404, body: { detail: "Run not found" } } });
    await expect(getRunDetail("r1")).rejects.toMatchObject({ code: "http_404", message: "Run not found", runId: null });
  });

  it("explains a body that isn't JSON", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("<html>Bad gateway</html>", { status: 502 })));
    await expect(getRunDetail("r1")).rejects.toMatchObject({ code: "http_502", message: "The YahBah API failed (HTTP 502)." });
  });

  it("names an unreachable API", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("Failed to fetch"))));
    await expect(getRunDetail("r1")).rejects.toMatchObject({ status: 0, code: "network_error" });
  });
});

describe("applications query", () => {
  it("sends the outcome filter as repeated parameters", async () => {
    expect(applicationsQuery({ outcome: ["awaiting_review", "submit_uncertain"], sort: "review_deadline_asc" })).toBe(
      "outcome=awaiting_review&outcome=submit_uncertain&sort=review_deadline_asc"
    );
    const { calls } = mockApi({ "GET /applications": { body: { items: [], total: 0, page: 1, per_page: 25 } } });
    await getApplications({ outcome: ["failed"], page: 2 });
    expect(calls[0].path).toBe("/applications?page=2&outcome=failed");
  });
});
