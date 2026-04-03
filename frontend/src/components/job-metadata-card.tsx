"use client";

import {
  Building2,
  Briefcase,
  MapPin,
  DollarSign,
  ExternalLink,
  Monitor,
  CalendarDays,
  Info,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import type { JobPostingInfo } from "@/lib/types";

function formatSalary(min: number | null, max: number | null): string | null {
  if (!min && !max) return null;
  const fmt = (n: number) =>
    n >= 1000 ? `$${Math.round(n / 1000)}k` : `$${n}`;
  if (min && max) return `${fmt(min)} - ${fmt(max)}`;
  if (min) return `${fmt(min)}+`;
  return `Up to ${fmt(max!)}`;
}

function Field({
  icon,
  label,
  children,
}: {
  icon: React.ReactNode;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-0.5">
      <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground uppercase tracking-wider">
        {icon}
        {label}
      </div>
      <div className="text-sm font-medium text-foreground">{children}</div>
    </div>
  );
}

export function JobMetadataCard({
  jobPosting,
  jobUrl,
}: {
  jobPosting: JobPostingInfo;
  jobUrl: string;
}) {
  const salary = formatSalary(jobPosting.salary_min, jobPosting.salary_max);

  return (
    <div className="glass-panel rounded-xl overflow-hidden">
      <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-white/10 to-transparent" />
      <div className="px-5 pt-4 pb-2">
        <h3 className="text-sm font-semibold text-foreground" style={{ fontFamily: "var(--font-heading)" }}>
          Job Details
        </h3>
        {jobPosting.company_description && (
          <p className="text-xs text-muted-foreground mt-0.5 flex items-center gap-1">
            <Info className="h-3 w-3 shrink-0" />
            {jobPosting.company_description}
          </p>
        )}
      </div>
      <div className="px-5 pb-5 space-y-4">
        <div className="grid grid-cols-2 gap-x-6 gap-y-3">
          <Field icon={<Building2 className="h-3 w-3" />} label="Company">
            {jobPosting.company ?? "Unknown"}
          </Field>
          <Field icon={<Briefcase className="h-3 w-3" />} label="Title">
            {jobPosting.title ?? "Untitled"}
          </Field>
          <Field icon={<MapPin className="h-3 w-3" />} label="Location">
            {jobPosting.location ?? "-"}
          </Field>
          <Field icon={<DollarSign className="h-3 w-3" />} label="Salary">
            {salary ? (
              <span className="text-emerald-400 font-semibold text-glow-green">
                {salary}
              </span>
            ) : (
              "-"
            )}
          </Field>
          {jobPosting.posted_date && (
            <Field icon={<CalendarDays className="h-3 w-3" />} label="Posted">
              {jobPosting.posted_date}
            </Field>
          )}
          <Field icon={<ExternalLink className="h-3 w-3" />} label="Job URL">
            <a
              href={jobUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="text-cyan-400 hover:text-cyan-300 hover:underline text-xs break-all inline-flex items-center gap-1 transition-colors"
            >
              View posting
              <ExternalLink className="h-3 w-3 shrink-0" />
            </a>
          </Field>
        </div>

        {jobPosting.technologies && jobPosting.technologies.length > 0 && (
          <div className="space-y-1.5">
            <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
              Technologies
            </span>
            <div className="flex flex-wrap gap-1.5">
              {jobPosting.technologies.map((tech) => (
                <Badge
                  key={tech}
                  className="bg-cyan-500/10 text-cyan-400 border-cyan-500/20 hover:bg-cyan-500/20 transition-colors text-xs"
                >
                  {tech}
                </Badge>
              ))}
            </div>
          </div>
        )}

        {jobPosting.specialties && jobPosting.specialties.length > 0 && (
          <div className="space-y-1.5">
            <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
              Specialties
            </span>
            <div className="flex flex-wrap gap-1.5">
              {jobPosting.specialties.map((s) => (
                <Badge
                  key={s}
                  variant="outline"
                  className="text-xs border-violet-500/30 text-violet-400 bg-violet-500/10"
                >
                  {s}
                </Badge>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
