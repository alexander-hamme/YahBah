"use client";

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

export function JobMetadataCard({
  jobPosting,
  jobUrl,
}: {
  jobPosting: JobPostingInfo;
  jobUrl: string;
}) {
  const salary = formatSalary(jobPosting.salary_min, jobPosting.salary_max);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-lg">Job Details</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-2 gap-x-8 gap-y-2 text-sm">
          <div>
            <span className="text-gray-500">Company</span>
            <p className="font-medium">{jobPosting.company ?? "Unknown"}</p>
          </div>
          <div>
            <span className="text-gray-500">Title</span>
            <p className="font-medium">{jobPosting.title ?? "Untitled"}</p>
          </div>
          <div>
            <span className="text-gray-500">Location</span>
            <p className="font-medium">{jobPosting.location ?? "-"}</p>
          </div>
          <div>
            <span className="text-gray-500">Salary</span>
            <p className="font-medium">{salary ?? "-"}</p>
          </div>
          <div>
            <span className="text-gray-500">ATS</span>
            <p className="font-medium capitalize">{jobPosting.ats_type}</p>
          </div>
          <div>
            <span className="text-gray-500">Job URL</span>
            <p>
              <a
                href={jobUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="text-blue-600 hover:underline text-sm break-all"
              >
                {jobUrl}
              </a>
            </p>
          </div>
        </div>

        {jobPosting.technologies && jobPosting.technologies.length > 0 && (
          <div>
            <span className="text-sm text-gray-500">Technologies</span>
            <div className="flex flex-wrap gap-1.5 mt-1">
              {jobPosting.technologies.map((tech) => (
                <Badge key={tech} variant="secondary">
                  {tech}
                </Badge>
              ))}
            </div>
          </div>
        )}

        {jobPosting.specialties && jobPosting.specialties.length > 0 && (
          <div>
            <span className="text-sm text-gray-500">Specialties</span>
            <div className="flex flex-wrap gap-1.5 mt-1">
              {jobPosting.specialties.map((s) => (
                <Badge key={s} variant="outline">
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
