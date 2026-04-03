"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getApplications } from "@/lib/api";
import { SummaryCards } from "@/components/summary-cards";
import { SubmitJobForm } from "@/components/submit-job-form";
import { SearchFilterBar } from "@/components/search-filter-bar";
import { ApplicationList } from "@/components/application-list";
import { Pagination } from "@/components/pagination";

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
  const [status, setStatus] = useState("all");
  const [sort, setSort] = useState("created_at_desc");

  const { data, isLoading } = useQuery({
    queryKey: ["applications", { page, search, status, sort }],
    queryFn: () =>
      getApplications({
        page,
        per_page: 25,
        status: status === "all" ? undefined : status,
        search: search || undefined,
        sort,
      }),
    refetchInterval: 10_000,
  });

  function handleSearchChange(value: string) {
    setSearch(value);
    setPage(1);
  }

  function handleStatusChange(value: string) {
    setStatus(value);
    setPage(1);
  }

  return (
    <div className="max-w-7xl mx-auto px-6 py-8 space-y-6 animate-fade-in">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Applications</h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            Track and manage your job applications
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

      {isLoading ? (
        <LoadingSplash />
      ) : data ? (
        <div className="space-y-4">
          <ApplicationList items={data.items} sort={sort} onSort={setSort} />
          <Pagination
            page={data.page}
            perPage={data.per_page}
            total={data.total}
            onPageChange={setPage}
          />
        </div>
      ) : null}
    </div>
  );
}
