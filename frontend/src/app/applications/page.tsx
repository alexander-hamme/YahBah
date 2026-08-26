"use client";

import { useState, useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { getApplications, bulkDeleteRuns } from "@/lib/api";
import { SummaryCards } from "@/components/summary-cards";
import { SubmitJobForm } from "@/components/submit-job-form";
import { SearchFilterBar } from "@/components/search-filter-bar";
import { ApplicationList } from "@/components/application-list";
import { Pagination } from "@/components/pagination";
import { DeleteAllButton } from "@/components/delete-all-button";

function LoadingSplash() {
  return (
    <div className="flex flex-col items-center justify-center py-24">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src="/YahBah_Logo.png"
        alt="Loading..."
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

export default function ApplicationsPage() {
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("hide_failed");
  const [sort, setSort] = useState("created_at_desc");
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const queryClient = useQueryClient();

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const s = params.get("status");
    if (s) setStatus(s.toUpperCase());
  }, []);

  const { data, isLoading } = useQuery({
    queryKey: ["applications", { page, search, status, sort }],
    queryFn: () =>
      getApplications({
        page,
        per_page: 25,
        status: status === "all" || status === "hide_failed" ? undefined : status,
        exclude_status: status === "hide_failed" ? "FAILED" : undefined,
        search: search || undefined,
        sort,
      }),
    refetchInterval: 10_000,
  });

  function handleSearchChange(value: string) {
    setSearch(value);
    setPage(1);
    setSelectedIds(new Set());
  }

  function handleStatusChange(value: string) {
    setStatus(value);
    setPage(1);
    setSelectedIds(new Set());
  }

  function handleToggleSelect(id: string) {
    setSelectedIds(prev => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  function handleToggleAll(ids: string[], selectAll: boolean) {
    setSelectedIds(prev => {
      const next = new Set(prev);
      ids.forEach(id => selectAll ? next.add(id) : next.delete(id));
      return next;
    });
  }

  async function handleBulkDelete(params: { run_ids?: string[]; status?: string }) {
    await bulkDeleteRuns(params);
    setSelectedIds(new Set());
    queryClient.invalidateQueries({ queryKey: ["applications"] });
    queryClient.invalidateQueries({ queryKey: ["application-stats"] });
  }

  return (
    <div className="max-w-7xl mx-auto px-6 py-8 space-y-6 animate-fade-in">
      <div className="relative">
        <div className="absolute -top-6 -left-10 w-40 h-40 bg-white/[0.03] rounded-full blur-3xl pointer-events-none" />
        <h1
          className="text-7xl text-white uppercase"
          style={{
            fontFamily: "var(--font-bungee)",
            textShadow: "0 0 40px rgba(255,255,255,0.15), 0 0 80px rgba(255,255,255,0.05)",
            letterSpacing: "0.04em",
          }}
        >
          Dashboard
        </h1>
        <div className="flex items-center gap-2 mt-2">
          <span className="relative flex h-2 w-2">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-cyan-400 opacity-75" />
            <span className="relative inline-flex rounded-full h-2 w-2 bg-cyan-500" />
          </span>
          <p className="text-sm text-muted-foreground">
            Tracking your job applications in real-time
          </p>
        </div>
      </div>

      <SummaryCards onStatusClick={handleStatusChange} />

      <div className="space-y-3">
        <SubmitJobForm />
        <SearchFilterBar
          search={search}
          onSearchChange={handleSearchChange}
          status={status}
          onStatusChange={handleStatusChange}
          sort={sort}
          onSortChange={setSort}
        />
      </div>

      {status === "FAILED" && data && data.total > 0 && (
        <div className="flex items-center justify-between px-1">
          <span className="text-xs text-muted-foreground">
            {data.total} failed run{data.total !== 1 ? "s" : ""}
          </span>
          <DeleteAllButton
            count={data.total}
            onConfirm={() => handleBulkDelete({ status: "FAILED" })}
          />
        </div>
      )}

      {selectedIds.size > 0 && (
        <div className="flex items-center gap-3 px-3 py-2 rounded-lg bg-white/[0.04] border border-white/10 text-sm">
          <span className="text-muted-foreground">{selectedIds.size} selected</span>
          <button
            onClick={() => setSelectedIds(new Set())}
            className="text-muted-foreground hover:text-foreground transition-colors"
          >
            Clear
          </button>
          <DeleteAllButton
            count={selectedIds.size}
            label={`Delete (${selectedIds.size})`}
            onConfirm={() => handleBulkDelete({ run_ids: [...selectedIds] })}
          />
        </div>
      )}

      {isLoading ? (
        <LoadingSplash />
      ) : data ? (
        <div className="space-y-4">
          <ApplicationList
            items={data.items}
            sort={sort}
            onSort={setSort}
            selectedIds={selectedIds}
            onToggleSelect={handleToggleSelect}
            onToggleAll={handleToggleAll}
          />
          <Pagination
            page={data.page}
            perPage={data.per_page}
            total={data.total}
            onPageChange={(p) => { setPage(p); setSelectedIds(new Set()); }}
          />
        </div>
      ) : null}
    </div>
  );
}
