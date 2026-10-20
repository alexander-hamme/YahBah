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
import { DEFAULT_FILTER, OUTCOME_FILTERS } from "@/lib/outcomes";

interface SearchFilterBarProps {
  search: string;
  onSearchChange: (value: string) => void;
  filter: string;
  onFilterChange: (value: string) => void;
  sort: string;
  onSortChange: (value: string) => void;
}

function handleSelect(fn: (v: string) => void) {
  return (value: string | null) => {
    if (value !== null) fn(value);
  };
}

const SORTS = [
  { value: "created_at_desc", label: "Newest First" },
  { value: "created_at_asc", label: "Oldest First" },
  { value: "updated_at_desc", label: "Recently Updated" },
  { value: "review_deadline_asc", label: "Review deadline" },
];

export function SearchFilterBar({
  search,
  onSearchChange,
  filter,
  onFilterChange,
  sort,
  onSortChange,
}: SearchFilterBarProps) {
  const hasActiveFilters = search || filter !== DEFAULT_FILTER;

  return (
    <div className="flex flex-col sm:flex-row gap-2">
      <div className="relative flex-1 group/search">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground transition-colors group-focus-within/search:text-cyan-400" />
        <Input
          type="search"
          aria-label="Search applications"
          placeholder="Search by company, title, or URL..."
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          className="pl-9 bg-white/5 border-white/10 placeholder:text-muted-foreground transition-all focus:shadow-[0_0_0_3px_rgba(56,189,248,0.15)] focus:border-cyan-500/40"
        />
      </div>
      <div className="flex gap-2">
        <div className="relative">
          <SlidersHorizontal className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground pointer-events-none z-10" />
          <Select value={filter} onValueChange={handleSelect(onFilterChange)}>
            <SelectTrigger aria-label="Filter by outcome" className="w-[180px] pl-9 bg-white/5 border-white/10">
              <SelectValue>
                {(value: string) => OUTCOME_FILTERS.find((f) => f.value === value)?.label ?? value}
              </SelectValue>
            </SelectTrigger>
            <SelectContent className="bg-[#111827] border-white/10">
              {OUTCOME_FILTERS.map((f) => (
                <SelectItem key={f.value} value={f.value}>
                  {f.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="relative">
          <ArrowUpDown className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground pointer-events-none z-10" />
          <Select value={sort} onValueChange={handleSelect(onSortChange)}>
            <SelectTrigger aria-label="Sort" className="w-[180px] pl-9 bg-white/5 border-white/10">
              <SelectValue>
                {(value: string) => SORTS.find((s) => s.value === value)?.label ?? "Sorted by column"}
              </SelectValue>
            </SelectTrigger>
            <SelectContent className="bg-[#111827] border-white/10">
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
              onFilterChange(DEFAULT_FILTER);
            }}
            className="text-muted-foreground hover:text-foreground hover:bg-white/5"
            aria-label="Clear filters"
            title="Clear filters"
          >
            <X className="h-4 w-4" />
          </Button>
        )}
      </div>
    </div>
  );
}
