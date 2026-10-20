import { useQuery } from "@tanstack/react-query";
import { getApplicationStats, getApplications, getRuntimeSettings, type ApplicationsParams } from "./api";

// Queries several components share. Each key is the cache entry they all read and invalidate.

export const RUNTIME_SETTINGS_KEY = ["runtime-settings"] as const;

export function useRuntimeSettings() {
  return useQuery({
    queryKey: RUNTIME_SETTINGS_KEY,
    queryFn: getRuntimeSettings,
    refetchInterval: 30_000,
  });
}

export function useApplicationStats() {
  return useQuery({
    queryKey: ["application-stats"],
    queryFn: getApplicationStats,
    refetchInterval: 10_000,
  });
}

/** The review queue: awaiting_review runs, soonest deadline first. */
export const REVIEW_QUEUE_PARAMS: ApplicationsParams = {
  outcome: ["awaiting_review"],
  sort: "review_deadline_asc",
  per_page: 100,
};

export function useReviewQueue({ enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: ["applications", REVIEW_QUEUE_PARAMS],
    queryFn: () => getApplications(REVIEW_QUEUE_PARAMS),
    enabled,
    refetchInterval: 30_000,
  });
}
