"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getApplications } from "@/lib/api";
import { SummaryCards } from "@/components/summary-cards";
import { SubmitJobForm } from "@/components/submit-job-form";
import { SearchFilterBar } from "@/components/search-filter-bar";
import { ApplicationList } from "@/components/application-list";
import { Pagination } from "@/components/pagination";

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
    <div className="max-w-7xl mx-auto px-6 py-8 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Applications</h1>
      </div>

      <SummaryCards />

      <div className="space-y-4">
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
        <div className="text-center py-12 text-gray-500">Loading...</div>
      ) : data ? (
        <>
          <ApplicationList items={data.items} />
          <Pagination
            page={data.page}
            perPage={data.per_page}
            total={data.total}
            onPageChange={setPage}
          />
        </>
      ) : null}
    </div>
  );
}
