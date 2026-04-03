"use client";

import { Button } from "@/components/ui/button";
import { ChevronLeft, ChevronRight } from "lucide-react";

interface PaginationProps {
  page: number;
  perPage: number;
  total: number;
  onPageChange: (page: number) => void;
}

export function Pagination({
  page,
  perPage,
  total,
  onPageChange,
}: PaginationProps) {
  const totalPages = Math.max(1, Math.ceil(total / perPage));
  if (totalPages <= 1) return null;

  const range: number[] = [];
  const start = Math.max(1, page - 2);
  const end = Math.min(totalPages, start + 4);
  for (let i = start; i <= end; i++) range.push(i);

  return (
    <div className="flex items-center justify-between">
      <span className="text-sm text-muted-foreground">
        <span className="font-semibold text-foreground">{total}</span> total
        &middot; page {page} of {totalPages}
      </span>
      <div className="flex items-center gap-1">
        <Button
          variant="outline"
          size="icon"
          className="h-8 w-8 bg-white/5 border-white/10 hover:bg-white/10 hover:border-white/20"
          onClick={() => onPageChange(page - 1)}
          disabled={page <= 1}
        >
          <ChevronLeft className="h-4 w-4" />
        </Button>
        {range.map((p) => (
          <Button
            key={p}
            variant={p === page ? "default" : "outline"}
            size="icon"
            className={`h-8 w-8 text-xs ${
              p === page
                ? "bg-cyan-500/20 text-cyan-400 border-cyan-500/30 shadow-[0_0_8px_rgba(56,189,248,0.15)]"
                : "bg-white/5 border-white/10 hover:bg-white/10 hover:border-white/20"
            }`}
            onClick={() => onPageChange(p)}
          >
            {p}
          </Button>
        ))}
        <Button
          variant="outline"
          size="icon"
          className="h-8 w-8 bg-white/5 border-white/10 hover:bg-white/10 hover:border-white/20"
          onClick={() => onPageChange(page + 1)}
          disabled={page >= totalPages}
        >
          <ChevronRight className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}
