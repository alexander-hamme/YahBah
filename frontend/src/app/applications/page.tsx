"use client";

import { Suspense, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useQuery } from "@/lib/queries";
import { bulkDeleteRuns, getApplications } from "@/lib/api";
import { DEFAULT_FILTER, filterByValue } from "@/lib/outcomes";
import { asApiError, toast } from "@/lib/toast";
import { ApiErrorMessage } from "@/components/api-error-message";
import { AttentionStrip } from "@/components/attention-strip";
import { SummaryCards } from "@/components/summary-cards";
import { SubmitJobForm } from "@/components/submit-job-form";
import { SearchFilterBar } from "@/components/search-filter-bar";
import { ApplicationList } from "@/components/application-list";
import { Pagination } from "@/components/pagination";
import { DeleteAllButton } from "@/components/delete-all-button";

const PER_PAGE = 25;
const NO_SELECTION: ReadonlySet<string> = new Set();
const ignore = () => undefined; // bulk-delete errors are toasted by the MutationCache

function LoadingSplash() {
  return (
    <div className="flex flex-col items-center justify-center py-24">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src="/YahBah_Logo.png"
        alt=""
        width={180}
        height={180}
        className="animate-breathe drop-shadow-lg"
      />
      <p className="text-sm text-muted-foreground mt-4 animate-pulse">
        Loading applications...
      </p>
    </div>
  );
}

function ApplicationsDashboard() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const queryClient = useQueryClient();

  // The outcome filter lives in the URL (?filter=), so nav badges and cards can link to it.
  const filter = filterByValue(searchParams.get("filter"));
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState("created_at_desc");

  // Page and selection belong to one filter + search + sort; changing any resets them.
  const viewKey = `${filter.value}|${search}|${sort}`;
  const [paging, setPaging] = useState({ key: viewKey, page: 1 });
  const page = paging.key === viewKey ? paging.page : 1;
  const selectionKey = `${viewKey}|${page}`;
  const [selection, setSelection] = useState<{ key: string; ids: ReadonlySet<string> }>({
    key: selectionKey,
    ids: NO_SELECTION,
  });
  const selectedIds = selection.key === selectionKey ? selection.ids : NO_SELECTION;

  const { data, isPending, error } = useQuery({
    queryKey: ["applications", { page, search, outcome: filter.outcomes, sort }],
    queryFn: () =>
      getApplications({
        page,
        per_page: PER_PAGE,
        outcome: filter.outcomes,
        search: search || undefined,
        sort,
      }),
    refetchInterval: 10_000,
  });

  const bulkDelete = useMutation({
    mutationFn: bulkDeleteRuns,
    meta: { errorTitle: "Couldn't delete the runs" },
    onSuccess: (result) => {
      toast.success(
        `Deleted ${result.deleted} run${result.deleted === 1 ? "" : "s"}`,
        result.skipped
          ? `${result.skipped} kept: in flight or possibly submitted runs can't be deleted.`
          : undefined
      );
      setSelection({ key: selectionKey, ids: NO_SELECTION });
      queryClient.invalidateQueries({ queryKey: ["applications"] });
      queryClient.invalidateQueries({ queryKey: ["application-stats"] });
    },
  });

  function setFilter(value: string) {
    const query = value === DEFAULT_FILTER ? "" : `?filter=${encodeURIComponent(value)}`;
    router.replace(`${pathname}${query}`, { scroll: false });
  }

  function setSelected(update: (prev: ReadonlySet<string>) => Set<string>) {
    setSelection({ key: selectionKey, ids: update(selectedIds) });
  }

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 py-8 space-y-6 animate-fade-in">
      <div>
        <h1
          className="text-5xl text-white uppercase"
          style={{
            fontFamily: "var(--font-bungee)",
            textShadow: "0 0 40px rgba(255,255,255,0.15), 0 0 80px rgba(255,255,255,0.05)",
            letterSpacing: "0.04em",
          }}
        >
          Dashboard
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">Every application, by what actually happened to it</p>
      </div>

      <AttentionStrip />

      <SummaryCards activeFilter={filter.value} onFilter={setFilter} />

      <div className="space-y-3">
        <SubmitJobForm />
        <SearchFilterBar
          search={search}
          onSearchChange={setSearch}
          filter={filter.value}
          onFilterChange={setFilter}
          sort={sort}
          onSortChange={setSort}
        />
      </div>

      {error && (
        <ApiErrorMessage
          error={asApiError(error)}
          title={data ? "Couldn't refresh; showing the last list" : "Couldn't load applications"}
        />
      )}

      {filter.value === "failed" && data && data.total > 0 && (
        <div className="flex items-center justify-between px-1">
          <span className="text-xs text-muted-foreground">
            {data.total} failed run{data.total !== 1 ? "s" : ""}
          </span>
          <DeleteAllButton
            count={data.total}
            onConfirm={() => bulkDelete.mutateAsync({ status: "FAILED" }).then(ignore, ignore)}
          />
        </div>
      )}

      {selectedIds.size > 0 && (
        <div className="flex items-center gap-3 px-3 py-2 rounded-lg bg-white/[0.04] border border-white/10 text-sm">
          <span className="text-muted-foreground">{selectedIds.size} selected</span>
          <button
            type="button"
            onClick={() => setSelection({ key: selectionKey, ids: NO_SELECTION })}
            className="text-muted-foreground hover:text-foreground transition-colors"
          >
            Clear
          </button>
          <DeleteAllButton
            count={selectedIds.size}
            label={`Delete (${selectedIds.size})`}
            onConfirm={() => bulkDelete.mutateAsync({ run_ids: [...selectedIds] }).then(ignore, ignore)}
          />
        </div>
      )}

      {isPending ? (
        <LoadingSplash />
      ) : data ? (
        <div className="space-y-4">
          <ApplicationList
            items={data.items}
            sort={sort}
            onSort={setSort}
            filtered={filter.value !== "all" || !!search}
            selectedIds={selectedIds}
            onToggleSelect={(id) =>
              setSelected((prev) => {
                const next = new Set(prev);
                if (next.has(id)) next.delete(id);
                else next.add(id);
                return next;
              })
            }
            onToggleAll={(ids, selectAll) =>
              setSelected((prev) => {
                const next = new Set(prev);
                ids.forEach((id) => (selectAll ? next.add(id) : next.delete(id)));
                return next;
              })
            }
          />
          <Pagination
            page={data.page}
            perPage={data.per_page}
            total={data.total}
            onPageChange={(p) => setPaging({ key: viewKey, page: p })}
          />
        </div>
      ) : null}
    </div>
  );
}

export default function ApplicationsPage() {
  // The dashboard reads ?filter= (useSearchParams), which needs a Suspense boundary to prerender.
  return (
    <Suspense fallback={<LoadingSplash />}>
      <ApplicationsDashboard />
    </Suspense>
  );
}
