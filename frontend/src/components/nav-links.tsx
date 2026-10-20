"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { CircleHelp } from "lucide-react";
import { useApplicationStats } from "@/lib/queries";

function linkClass(active: boolean) {
  return `inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded-md transition-colors ${
    active
      ? "text-cyan-400 bg-cyan-500/10"
      : "text-muted-foreground hover:text-foreground hover:bg-white/5"
  }`;
}

export function NavLinks() {
  const pathname = usePathname();
  const { data } = useApplicationStats();
  const awaiting = data?.counts.awaiting_review ?? 0;
  const uncertain = data?.counts.submit_uncertain ?? 0;

  return (
    <nav aria-label="Main" className="flex items-center gap-1">
      <Link href="/applications" className={linkClass(pathname.startsWith("/applications"))}>
        Applications
      </Link>
      {uncertain > 0 && (
        <Link
          href="/applications?filter=submit_uncertain"
          className="flex items-center gap-1 rounded-full border border-orange-400/45 bg-orange-500/15 px-1.5 py-0.5 text-xs font-semibold tabular-nums text-orange-200 hover:bg-orange-500/25"
          title={`${uncertain} may have been submitted: resolve`}
        >
          <CircleHelp className="h-3 w-3" aria-hidden />
          {uncertain}
          <span className="sr-only"> may have been submitted</span>
        </Link>
      )}
      <Link href="/review" className={linkClass(pathname.startsWith("/review"))}>
        Review
        {awaiting > 0 && (
          <span className="rounded-full bg-amber-500/20 px-1.5 text-xs font-semibold tabular-nums text-amber-200">
            {awaiting}
            <span className="sr-only"> waiting</span>
          </span>
        )}
      </Link>
      <Link href="/scheduled" className={linkClass(pathname.startsWith("/scheduled"))}>
        Scheduled
      </Link>
    </nav>
  );
}
