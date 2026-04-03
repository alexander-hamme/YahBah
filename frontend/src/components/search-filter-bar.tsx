"use client";

import { Search, SlidersHorizontal, ArrowUpDown, X } from "lucide-react";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Button } from "@/components/ui/button";

interface SearchFilterBarProps {
  search: string;
  onSearchChange: (value: string) => void;
  status: string;
  onStatusChange: (value: string) => void;
  sort: string;
  onSortChange: (value: string) => void;
}

function handleSelect(fn: (v: string) => void) {
  return (value: string | null) => {
    if (value !== null) fn(value);
  };
}

const STATUSES = [
  { value: "all", label: "All Statuses" },
  { value: "PENDING", label: "Pending" },
  { value: "RUNNING", label: "Running" },
  { value: "COMPLETED", label: "Completed" },
  { value: "FAILED", label: "Failed" },
  { value: "DUPLICATE", label: "Duplicate" },
];

const SORTS = [
  { value: "created_at_desc", label: "Newest First" },
  { value: "created_at_asc", label: "Oldest First" },
  { value: "updated_at_desc", label: "Recently Updated" },
];

export function SearchFilterBar({
  search,
  onSearchChange,
  status,
  onStatusChange,
  sort,
  onSortChange,
}: SearchFilterBarProps) {
  const hasActiveFilters = search || status !== "all";

  return (
    <div className="flex flex-col sm:flex-row gap-2">
      <div className="relative flex-1 group/search">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground transition-colors group-focus-within/search:text-primary" />
        <Input
          type="search"
          placeholder="Search by company, title, or URL..."
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          className="pl-9 transition-shadow focus:shadow-[0_0_0_3px_oklch(0.55_0.22_270_/_0.15)] focus:border-primary"
        />
      </div>
      <div className="flex gap-2">
        <div className="relative">
          <SlidersHorizontal className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground pointer-events-none z-10" />
          <Select value={status} onValueChange={handleSelect(onStatusChange)}>
            <SelectTrigger className="w-[170px] pl-9">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {STATUSES.map((s) => (
                <SelectItem key={s.value} value={s.value}>
                  {s.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="relative">
          <ArrowUpDown className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground pointer-events-none z-10" />
          <Select value={sort} onValueChange={handleSelect(onSortChange)}>
            <SelectTrigger className="w-[180px] pl-9">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {SORTS.map((s) => (
                <SelectItem key={s.value} value={s.value}>
                  {s.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        {hasActiveFilters && (
          <Button
            variant="ghost"
            size="icon"
            onClick={() => {
              onSearchChange("");
              onStatusChange("all");
            }}
            className="text-muted-foreground hover:text-foreground"
            title="Clear filters"
          >
            <X className="h-4 w-4" />
          </Button>
        )}
      </div>
    </div>
  );
}
