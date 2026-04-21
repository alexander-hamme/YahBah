"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const links = [
  { href: "/applications", label: "Applications" },
  { href: "/scheduled", label: "Scheduled" },
];

export function NavLinks() {
  const pathname = usePathname();

  return (
    <nav className="flex gap-1">
      {links.map(({ href, label }) => {
        const active = pathname.startsWith(href);
        return (
          <Link
            key={href}
            href={href}
            className={`px-3 py-1.5 text-sm font-medium rounded-md transition-colors ${
              active
                ? "text-cyan-400 bg-cyan-500/10"
                : "text-muted-foreground hover:text-foreground hover:bg-white/5"
            }`}
          >
            {label}
          </Link>
        );
      })}
    </nav>
  );
}
