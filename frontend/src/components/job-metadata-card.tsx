"use client";

import {
  Building2,
  Briefcase,
  MapPin,
  DollarSign,
  ExternalLink,
  CalendarDays,
  Info,
  Laptop,
  Factory,
  Target,
  Award,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import type { JobPostingInfo } from "@/lib/types";

function formatPostedDate(iso: string | null): string {
  if (!iso) return "-";
  const d = new Date(iso + "T00:00:00");
  const day = d.getDate();
  const suffix =
    day === 1 || day === 21 || day === 31
      ? "st"
      : day === 2 || day === 22
      ? "nd"
      : day === 3 || day === 23
      ? "rd"
      : "th";
  const month = d.toLocaleDateString("en-US", { month: "short" });
  const year = d.getFullYear();
  return `${month} ${day}${suffix}, ${year}`;
}

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
  matchScore,
  matchRationale,
}: {
  jobPosting: JobPostingInfo;
  jobUrl: string;
  matchScore?: number | null;
  matchRationale?: string | null;
}) {
  const salary = formatSalary(jobPosting.salary_min, jobPosting.salary_max);

  return (
    <div className="glass-panel rounded-xl overflow-hidden">
      <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-white/10 to-transparent" />
      <div className="px-5 pt-4 pb-2">
        <h3 className="text-sm font-semibold text-foreground" style={{ fontFamily: "var(--font-heading)" }}>
          Job Details
        </h3>
      </div>
      <div className="px-5 pb-5 space-y-4">
        <div className="grid grid-cols-2 gap-x-6 gap-y-3">
          <Field icon={<Briefcase className="h-3 w-3" />} label="Title">
            {jobPosting.title ?? "Untitled"}
          </Field>
          <Field icon={<Building2 className="h-3 w-3" />} label="Company">
            {jobPosting.company ?? "Unknown"}
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
          <Field icon={<MapPin className="h-3 w-3" />} label="Location">
            {jobPosting.location ?? "-"}
          </Field>
          <Field icon={<ExternalLink className="h-3 w-3" />} label="Job URL">
            <a
              href={jobUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium text-cyan-400 bg-cyan-500/10 border border-cyan-500/20 hover:bg-cyan-500/20 hover:shadow-[0_0_12px_rgba(56,189,248,0.15)] transition-all"
            >
              <ExternalLink className="h-3 w-3" />
              View posting
            </a>
          </Field>
          <Field icon={<Laptop className="h-3 w-3" />} label="Flexibility">
            {jobPosting.work_model ?? "-"}
          </Field>
          <Field icon={<Award className="h-3 w-3" />} label="Seniority">
            {jobPosting.seniority_level || jobPosting.experience_required ? (
              <span>
                {jobPosting.seniority_level ?? "-"}
                {jobPosting.experience_required && (
                  <span className="text-muted-foreground ml-1">
                    ({jobPosting.experience_required} yrs)
                  </span>
                )}
              </span>
            ) : (
              "-"
            )}
          </Field>
          <Field icon={<CalendarDays className="h-3 w-3" />} label="Posted">
            {formatPostedDate(jobPosting.posted_date)}
          </Field>
        </div>

        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground uppercase tracking-wider">
              <Info className="h-3 w-3" />
              About the Company
            </div>
            {jobPosting.industry && (
              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-white/5 border border-white/10 text-muted-foreground">
                <Factory className="h-2.5 w-2.5" />
                {jobPosting.industry}
              </span>
            )}
          </div>
          <p className="text-sm font-medium text-foreground/80">
            {jobPosting.company_description ?? "-"}
          </p>
        </div>

        {matchScore != null && (
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground uppercase tracking-wider">
                <Target className="h-3 w-3" />
                Match Score
              </div>
              <span
                className={`text-2xl font-bold tabular-nums ${
                  matchScore >= 85
                    ? "text-emerald-400"
                    : matchScore >= 75
                    ? "text-teal-400"
                    : matchScore >= 50
                    ? "text-cyan-400"
                    : matchScore >= 35
                    ? "text-amber-400"
                    : "text-red-400"
                }`}
                style={{ fontFamily: "var(--font-heading)" }}
              >
                {matchScore}%
              </span>
            </div>
            <div className="h-2 rounded-full overflow-hidden bg-white/5">
              <div
                className={`h-full rounded-full transition-all duration-500 ${
                  matchScore >= 85
                    ? "bg-emerald-500 shadow-[0_0_8px_rgba(52,211,153,0.4)]"
                    : matchScore >= 75
                    ? "bg-teal-500 shadow-[0_0_8px_rgba(20,184,166,0.4)]"
                    : matchScore >= 50
                    ? "bg-cyan-500 shadow-[0_0_8px_rgba(56,189,248,0.4)]"
                    : matchScore >= 35
                    ? "bg-amber-500 shadow-[0_0_8px_rgba(251,191,36,0.4)]"
                    : "bg-red-500 shadow-[0_0_8px_rgba(239,68,68,0.4)]"
                }`}
                style={{ width: `${matchScore}%` }}
              />
            </div>
            {matchRationale && (
              <p className="text-xs text-muted-foreground">
                {matchRationale}
              </p>
            )}
          </div>
        )}

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
