"use client";

import { useState } from "react";
import { Building2 } from "lucide-react";

function logoUrl(domain: string): string {
  return `https://logo.clearbit.com/${domain}`;
}

function domainFromWebsite(website: string | null): string | null {
  if (!website) return null;
  // Strip protocol if present, strip trailing slashes/paths
  return website
    .replace(/^https?:\/\//, "")
    .replace(/\/.*$/, "")
    .toLowerCase();
}

export function CompanyLogo({
  companyWebsite,
  companyName,
  size = 32,
  className = "",
}: {
  companyWebsite: string | null;
  companyName: string | null;
  size?: number;
  className?: string;
}) {
  const [failed, setFailed] = useState(false);
  const domain = domainFromWebsite(companyWebsite);

  if (!domain || failed) {
    return (
      <div
        className={`flex items-center justify-center rounded-lg bg-muted text-muted-foreground shrink-0 ${className}`}
        style={{ width: size, height: size }}
      >
        <Building2 className="h-1/2 w-1/2" />
      </div>
    );
  }

  return (
    /* eslint-disable-next-line @next/next/no-img-element */
    <img
      src={logoUrl(domain)}
      alt={companyName ?? "Company logo"}
      width={size}
      height={size}
      className={`rounded-lg bg-white object-contain shrink-0 ${className}`}
      onError={() => setFailed(true)}
    />
  );
}
