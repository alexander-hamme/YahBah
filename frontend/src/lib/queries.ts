import { useSyncExternalStore } from "react";
import {
  useQuery as useTanstackQuery,
  type DefaultError,
  type QueryKey,
  type UseQueryOptions,
  type UseQueryResult,
} from "@tanstack/react-query";
import { getApplicationStats, getApplications, getRuntimeSettings, type ApplicationsParams } from "./api";

// ── Hydration ────────────────────────────────────────────────────────────
// The server renders every query as pending: it never fetches. On the client, a subtree can
// hydrate after the queries it reads were already fetched by something hydrated earlier
// (the nav badges and the mode control sit outside each page's Suspense boundary). Its
// first client render must still match the server's HTML, so while a component hydrates it
// sees the result the server rendered, then the cached one straight after. Client-side
// navigations aren't hydration: they get the cached result at once.

const subscribeNothing = () => () => {};

/** False while this component is hydrating (and on the server); true from then on. */
export function useHydrated(): boolean {
  return useSyncExternalStore(
    subscribeNothing,
    () => true,
    () => false
  );
}

/** The server's render of a query: pending, nothing fetched yet. */
function serverResult<TData, TError>(
  result: UseQueryResult<TData, TError>,
  enabled: boolean
): UseQueryResult<TData, TError> {
  return {
    data: undefined,
    error: null,
    status: "pending",
    fetchStatus: enabled ? "fetching" : "idle",
    isPending: true,
    isLoading: enabled,
    isInitialLoading: enabled,
    isFetching: enabled,
    isSuccess: false,
    isError: false,
    isLoadingError: false,
    isRefetchError: false,
    isRefetching: false,
    isPaused: false,
    isPlaceholderData: false,
    isFetched: false,
    isFetchedAfterMount: false,
    isStale: true,
    isEnabled: enabled,
    dataUpdatedAt: 0,
    errorUpdatedAt: 0,
    errorUpdateCount: 0,
    failureCount: 0,
    failureReason: null,
    refetch: result.refetch,
    promise: result.promise,
  } as UseQueryResult<TData, TError>;
}

/**
 * `useQuery` for this app: identical, except that it renders as the server did while the
 * component hydrates (see above). Import it from here, never from TanStack directly; the
 * lint config enforces that.
 */
export function useQuery<
  TQueryFnData = unknown,
  TError = DefaultError,
  TData = TQueryFnData,
  TQueryKey extends QueryKey = QueryKey,
>(options: UseQueryOptions<TQueryFnData, TError, TData, TQueryKey>): UseQueryResult<TData, TError> {
  const result = useTanstackQuery(options);
  const hydrated = useHydrated();
  return hydrated ? result : serverResult(result, options.enabled !== false);
}

// ── Shared queries ───────────────────────────────────────────────────────
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

export const REVIEW_QUEUE_KEY = ["applications", REVIEW_QUEUE_PARAMS] as const;

export function useReviewQueue({ enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: REVIEW_QUEUE_KEY,
    queryFn: () => getApplications(REVIEW_QUEUE_PARAMS),
    enabled,
    refetchInterval: 30_000,
  });
}
