"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { FailedBadge } from "@/components/failed-badge";

function linkClass(active: boolean) {
  return `px-3 py-1.5 text-sm font-medium rounded-md transition-colors ${
    active
      ? "text-cyan-400 bg-cyan-500/10"
      : "text-muted-foreground hover:text-foreground hover:bg-white/5"
  }`;
}

export function NavLinks() {
  const pathname = usePathname();

  return (
    <nav className="flex gap-1 items-center">
      <div className="flex items-center gap-1.5">
        <Link href="/applications" className={linkClass(pathname.startsWith("/applications"))}>
          Applications
        </Link>
        <FailedBadge />
      </div>
      <Link href="/scheduled" className={linkClass(pathname.startsWith("/scheduled"))}>
        Scheduled
      </Link>
    </nav>
  );
}
