"use client";

import {
  Building2,
  Briefcase,
  MapPin,
  DollarSign,
  ExternalLink,
  Monitor,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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
    <Card className="shadow-sm">
      <CardHeader className="pb-3">
        <CardTitle className="text-base font-semibold">Job Details</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-2 gap-x-6 gap-y-3">
          <Field
            icon={<Building2 className="h-3 w-3" />}
            label="Company"
          >
            {jobPosting.company ?? "Unknown"}
          </Field>
          <Field
            icon={<Briefcase className="h-3 w-3" />}
            label="Title"
          >
            {jobPosting.title ?? "Untitled"}
          </Field>
          <Field icon={<MapPin className="h-3 w-3" />} label="Location">
            {jobPosting.location ?? "-"}
          </Field>
          <Field
            icon={<DollarSign className="h-3 w-3" />}
            label="Salary"
          >
            {salary ? (
              <span className="text-emerald-700 dark:text-emerald-400 font-semibold">
                {salary}
              </span>
            ) : (
              "-"
            )}
          </Field>
          <Field icon={<Monitor className="h-3 w-3" />} label="ATS">
            <span className="capitalize">{jobPosting.ats_type}</span>
          </Field>
          <Field
            icon={<ExternalLink className="h-3 w-3" />}
            label="Job URL"
          >
            <a
              href={jobUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary hover:underline text-xs break-all inline-flex items-center gap-1"
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
                  className="bg-primary/10 text-primary border-primary/20 hover:bg-primary/20 transition-colors text-xs"
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
                  className="text-xs border-violet-300 text-violet-700 dark:border-violet-700 dark:text-violet-300"
                >
                  {s}
                </Badge>
              ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
