"use client";

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Clock,
  CheckCircle2,
  SkipForward,
  Rocket,
  Search,
  SlidersHorizontal,
  ArrowUpDown,
  X,
  Pause,
  Play,
  Trash2,
  Zap,
  ThumbsUp,
  ThumbsDown,
  ExternalLink,
  Plus,
  CalendarDays,
} from "lucide-react";
import { DayPicker } from "react-day-picker";
import "react-day-picker/style.css";
import {
  getScheduledJobs,
  getScheduledStats,
  addScheduledJob,
  triggerAlertBackfill,
  approveScheduledJob,
  rejectScheduledJob,
  toggleHoldScheduledJob,
  promoteScheduledJob,
  deleteScheduledJob,
} from "@/lib/api";
import type { ScheduledJobItem, ScheduledStats } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Pagination } from "@/components/pagination";

// ── Helpers ────────────────────────────────────────────────────────────

function timeAgo(iso: string): string {
  const seconds = Math.floor(
    (Date.now() - new Date(iso).getTime()) / 1000
  );
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function timeUntil(iso: string): string {
  const ms = new Date(iso).getTime() - Date.now();
  if (ms <= 0) return "now";
  const minutes = Math.floor(ms / 60000);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ${minutes % 60}m`;
  const days = Math.floor(hours / 24);
  return `${days}d ${hours % 24}h`;
}

function scoreColor(score: number | null): string {
  if (score === null) return "text-slate-500";
  if (score >= 70) return "text-emerald-400";
  if (score >= 40) return "text-amber-400";
  return "text-red-400";
}

function scoreBg(score: number | null): string {
  if (score === null) return "bg-slate-500/10 border-slate-500/20";
  if (score >= 70) return "bg-emerald-500/10 border-emerald-500/20";
  if (score >= 40) return "bg-amber-500/10 border-amber-500/20";
  return "bg-red-500/10 border-red-500/20";
}

function sourceBadge(source: string): string {
  const map: Record<string, string> = {
    linkedin: "bg-blue-500/15 text-blue-400 border-blue-500/30",
    indeed: "bg-violet-500/15 text-violet-400 border-violet-500/30",
    glassdoor: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30",
    greenhouse: "bg-cyan-500/15 text-cyan-400 border-cyan-500/30",
    manual: "bg-white/10 text-slate-300 border-white/20",
  };
  return map[source] ?? "bg-white/5 text-slate-400 border-white/10";
}

const statusConfig: Record<
  string,
  { label: string; icon: React.ReactNode; className: string }
> = {
  SCHEDULED: {
    label: "Scheduled",
    icon: <Clock className="h-3 w-3" />,
    className: "bg-cyan-500/15 text-cyan-400 border-cyan-500/30",
  },
  APPROVED: {
    label: "Approved",
    icon: <CheckCircle2 className="h-3 w-3" />,
    className: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30",
  },
  SKIPPED: {
    label: "Skipped",
    icon: <SkipForward className="h-3 w-3" />,
    className: "bg-slate-500/15 text-slate-400 border-slate-500/30",
  },
  PROMOTED: {
    label: "Promoted",
    icon: <Rocket className="h-3 w-3" />,
    className: "bg-violet-500/15 text-violet-400 border-violet-500/30",
  },
};

function ScheduledStatusBadge({ status }: { status: string }) {
  const config = statusConfig[status] ?? {
    label: status,
    icon: null,
    className: "bg-white/5 text-slate-400 border-white/10",
  };
  return (
    <span
      className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold border ${config.className}`}
    >
      {config.icon}
      {config.label}
    </span>
  );
}

// ── Stats Cards ────────────────────────────────────────────────────────

function StatsCards({
  stats,
  onFilter,
}: {
  stats: ScheduledStats;
  onFilter: (status: string) => void;
}) {
  const pills = [
    { key: "scheduled", label: "Scheduled", count: stats.scheduled, icon: <Clock className="h-3.5 w-3.5" />, color: "text-cyan-400 bg-cyan-500/15 border-cyan-500/30" },
    { key: "approved", label: "Approved", count: stats.approved, icon: <CheckCircle2 className="h-3.5 w-3.5" />, color: "text-emerald-400 bg-emerald-500/15 border-emerald-500/30" },
    { key: "skipped", label: "Skipped", count: stats.skipped, icon: <SkipForward className="h-3.5 w-3.5" />, color: "text-slate-400 bg-slate-500/15 border-slate-500/30" },
    { key: "promoted", label: "Promoted", count: stats.promoted, icon: <Rocket className="h-3.5 w-3.5" />, color: "text-violet-400 bg-violet-500/15 border-violet-500/30" },
  ];

  return (
    <div className="glass-panel rounded-xl p-5">
      <div className="flex items-center gap-6">
        <div>
          <div className="text-4xl font-bold tracking-tight">{stats.total}</div>
          <div className="text-xs text-muted-foreground mt-0.5">total jobs</div>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {pills.map((p) => (
            <button
              key={p.key}
              onClick={() => onFilter(p.key.toUpperCase())}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-xs font-semibold transition-all hover:scale-105 ${p.color}`}
            >
              {p.icon}
              {p.label}
              <span className="ml-1 opacity-70">{p.count}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

// ── Add Job Form ───────────────────────────────────────────────────────

function AddJobForm() {
  const [url, setUrl] = useState("");
  const [loading, setLoading] = useState(false);
  const queryClient = useQueryClient();

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!url.trim()) return;
    setLoading(true);
    try {
      await addScheduledJob(url.trim());
      setUrl("");
      queryClient.invalidateQueries({ queryKey: ["scheduled"] });
      queryClient.invalidateQueries({ queryKey: ["scheduled-stats"] });
    } catch {
      // TODO: toast
    } finally {
      setLoading(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex gap-2">
      <div className="relative flex-1">
        <Plus className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
        <Input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="Add job URL to schedule..."
          className="pl-9 bg-white/5 border-white/10 focus:border-cyan-500/40"
        />
      </div>
      <Button
        type="submit"
        disabled={loading || !url.trim()}
        className="bg-cyan-500/20 text-cyan-400 border border-cyan-500/30 hover:bg-cyan-500/30 px-4"
      >
        {loading ? "Adding..." : "Add"}
      </Button>
    </form>
  );
}

// ── Backfill Picker ────────────────────────────────────────────────────

function BackfillPicker() {
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<Date | undefined>();
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const queryClient = useQueryClient();

  async function handleBackfill() {
    if (!selected) return;
    setLoading(true);
    setResult(null);
    try {
      const since = selected.toISOString().split("T")[0]; // YYYY-MM-DD
      const res = await triggerAlertBackfill(since);
      setResult(`Found ${res.processed} job(s)`);
      queryClient.invalidateQueries({ queryKey: ["scheduled"] });
      queryClient.invalidateQueries({ queryKey: ["scheduled-stats"] });
    } catch {
      setResult("Backfill failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-muted-foreground bg-white/5 border border-white/10 hover:bg-white/10 hover:border-white/20 transition-all"
      >
        <CalendarDays className="h-3.5 w-3.5" />
        Scan Past Emails
      </button>

      {open && (
        <div className="absolute right-0 top-full mt-2 z-50 rounded-xl border border-white/10 bg-black/95 backdrop-blur-md shadow-xl p-4 space-y-3">
          <p className="text-xs text-muted-foreground">
            Search Gmail for job alert emails since:
          </p>
          <DayPicker
            mode="single"
            selected={selected}
            onSelect={setSelected}
            disabled={{ after: new Date() }}
            classNames={{
              root: "text-sm",
              day: "text-foreground",
              selected: "bg-cyan-500/30 text-cyan-300 rounded-md",
              today: "font-bold text-cyan-400",
              chevron: "fill-muted-foreground",
            }}
          />
          <div className="flex items-center gap-2">
            <Button
              onClick={handleBackfill}
              disabled={!selected || loading}
              className="bg-cyan-500/20 text-cyan-400 border border-cyan-500/30 hover:bg-cyan-500/30 text-xs px-3 py-1"
              size="sm"
            >
              {loading ? "Scanning..." : "Scan"}
            </Button>
            <Button
              onClick={() => { setOpen(false); setResult(null); }}
              variant="ghost"
              size="sm"
              className="text-xs text-muted-foreground"
            >
              Cancel
            </Button>
            {result && (
              <span className="text-xs text-muted-foreground">{result}</span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Row Actions ────────────────────────────────────────────────────────

function RowActions({
  job,
  onAction,
}: {
  job: ScheduledJobItem;
  onAction: () => void;
}) {
  const [loading, setLoading] = useState<string | null>(null);

  async function act(fn: () => Promise<unknown>, key: string) {
    setLoading(key);
    try {
      await fn();
      onAction();
    } finally {
      setLoading(null);
    }
  }

  const canAct = job.status === "SCHEDULED" || job.status === "APPROVED" || job.status === "SKIPPED";
  const canPromote = job.status === "SCHEDULED" || job.status === "APPROVED";

  return (
    <div className="flex items-center gap-1">
      {canAct && job.status !== "SKIPPED" && (
        <button
          onClick={() => act(() => approveScheduledJob(job.id), "approve")}
          disabled={loading !== null || job.status === "APPROVED"}
          className="p-1.5 rounded-md text-emerald-400/60 hover:text-emerald-400 hover:bg-emerald-500/10 transition-colors disabled:opacity-30"
          title="Approve"
        >
          <ThumbsUp className="h-3.5 w-3.5" />
        </button>
      )}
      {canAct && (
        <button
          onClick={() => act(() => rejectScheduledJob(job.id), "reject")}
          disabled={loading !== null || job.status === "SKIPPED"}
          className="p-1.5 rounded-md text-slate-400/60 hover:text-slate-400 hover:bg-slate-500/10 transition-colors disabled:opacity-30"
          title="Skip"
        >
          <ThumbsDown className="h-3.5 w-3.5" />
        </button>
      )}
      {canPromote && (
        <button
          onClick={() => act(() => toggleHoldScheduledJob(job.id), "hold")}
          disabled={loading !== null}
          className={`p-1.5 rounded-md transition-colors ${
            job.hold
              ? "text-amber-400 hover:bg-amber-500/10"
              : "text-slate-400/60 hover:text-slate-400 hover:bg-slate-500/10"
          }`}
          title={job.hold ? "Release hold" : "Hold"}
        >
          {job.hold ? <Play className="h-3.5 w-3.5" /> : <Pause className="h-3.5 w-3.5" />}
        </button>
      )}
      {canPromote && (
        <button
          onClick={() => act(() => promoteScheduledJob(job.id), "promote")}
          disabled={loading !== null}
          className="p-1.5 rounded-md text-violet-400/60 hover:text-violet-400 hover:bg-violet-500/10 transition-colors disabled:opacity-30"
          title="Promote now"
        >
          <Zap className="h-3.5 w-3.5" />
        </button>
      )}
      <button
        onClick={() => act(() => deleteScheduledJob(job.id), "delete")}
        disabled={loading !== null}
        className="p-1.5 rounded-md text-red-400/40 hover:text-red-400 hover:bg-red-500/10 transition-colors disabled:opacity-30"
        title="Delete"
      >
        <Trash2 className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

// ── Page ───────────────────────────────────────────────────────────────

export default function ScheduledPage() {
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("all");
  const [sort, setSort] = useState("created_at_desc");
  const queryClient = useQueryClient();

  const { data, isLoading } = useQuery({
    queryKey: ["scheduled", { page, search, status, sort }],
    queryFn: () =>
      getScheduledJobs({
        page,
        per_page: 25,
        status: status === "all" ? undefined : status,
        search: search || undefined,
        sort,
      }),
    refetchInterval: 10_000,
  });

  const { data: stats } = useQuery({
    queryKey: ["scheduled-stats"],
    queryFn: getScheduledStats,
    refetchInterval: 10_000,
  });

  function invalidate() {
    queryClient.invalidateQueries({ queryKey: ["scheduled"] });
    queryClient.invalidateQueries({ queryKey: ["scheduled-stats"] });
  }

  const hasFilters = search || status !== "all";

  return (
    <div className="max-w-7xl mx-auto px-6 py-8 space-y-6 animate-fade-in">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1
            className="text-2xl font-bold tracking-tight"
            style={{ fontFamily: "var(--font-heading)" }}
          >
            Scheduled Jobs
          </h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            Jobs from email alerts and manual additions, awaiting promotion
          </p>
        </div>
        <BackfillPicker />
      </div>

      {/* Stats */}
      {stats && (
        <StatsCards stats={stats} onFilter={(s) => setStatus(s)} />
      )}

      {/* Add job form */}
      <AddJobForm />

      {/* Search + Filters */}
      <div className="flex items-center gap-3 flex-wrap">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setPage(1);
            }}
            placeholder="Search by company, title, or URL..."
            className="pl-9 bg-white/5 border-white/10 focus:border-cyan-500/40"
          />
        </div>

        <div className="flex items-center gap-2">
          <SlidersHorizontal className="h-3.5 w-3.5 text-muted-foreground" />
          <select
            value={status}
            onChange={(e) => {
              setStatus(e.target.value);
              setPage(1);
            }}
            className="text-sm bg-white/5 border border-white/10 rounded-md px-3 py-1.5 text-foreground focus:border-cyan-500/40 focus:outline-none"
          >
            <option value="all">All Statuses</option>
            <option value="SCHEDULED">Scheduled</option>
            <option value="APPROVED">Approved</option>
            <option value="SKIPPED">Skipped</option>
            <option value="PROMOTED">Promoted</option>
          </select>
        </div>

        <div className="flex items-center gap-2">
          <ArrowUpDown className="h-3.5 w-3.5 text-muted-foreground" />
          <select
            value={sort}
            onChange={(e) => setSort(e.target.value)}
            className="text-sm bg-white/5 border border-white/10 rounded-md px-3 py-1.5 text-foreground focus:border-cyan-500/40 focus:outline-none"
          >
            <option value="created_at_desc">Newest First</option>
            <option value="created_at_asc">Oldest First</option>
            <option value="match_score_desc">Highest Score</option>
            <option value="match_score_asc">Lowest Score</option>
            <option value="promote_after_asc">Promotes Soonest</option>
          </select>
        </div>

        {hasFilters && (
          <button
            onClick={() => {
              setSearch("");
              setStatus("all");
              setPage(1);
            }}
            className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-white/5 transition-colors"
            title="Clear filters"
          >
            <X className="h-4 w-4" />
          </button>
        )}
      </div>

      {/* Table */}
      {isLoading ? (
        <div className="space-y-2">
          {[...Array(5)].map((_, i) => (
            <div
              key={i}
              className="h-16 bg-white/5 rounded-lg animate-pulse"
            />
          ))}
        </div>
      ) : !data || data.items.length === 0 ? (
        <div className="text-center py-16 text-muted-foreground">
          <p className="text-sm">No scheduled jobs yet</p>
          <p className="text-xs mt-1 text-white/20">
            Jobs from email alerts or manual additions will appear here
          </p>
        </div>
      ) : (
        <div className="space-y-1">
          {data.items.map((job) => (
            <div
              key={job.id}
              className="group flex items-center gap-4 px-4 py-3 rounded-lg bg-white/[0.02] border border-white/5 hover:bg-white/[0.04] hover:border-white/10 transition-all"
            >
              {/* Score */}
              <div
                className={`flex items-center justify-center w-10 h-10 rounded-lg border text-sm font-bold ${scoreBg(job.match_score)} ${scoreColor(job.match_score)}`}
              >
                {job.match_score ?? "?"}
              </div>

              {/* Info */}
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-foreground truncate">
                    {job.title || job.snippet || job.job_url}
                  </span>
                  <a
                    href={job.job_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-muted-foreground hover:text-cyan-400 transition-colors shrink-0"
                  >
                    <ExternalLink className="h-3 w-3" />
                  </a>
                </div>
                <div className="flex items-center gap-2 mt-0.5">
                  {job.company && (
                    <span className="text-xs text-muted-foreground">
                      {job.company}
                    </span>
                  )}
                  {job.location && (
                    <span className="text-xs text-white/20">
                      {job.location}
                    </span>
                  )}
                </div>
              </div>

              {/* Source */}
              <span
                className={`px-2 py-0.5 rounded-full text-[10px] font-semibold border ${sourceBadge(job.source)}`}
              >
                {job.source}
              </span>

              {/* Status */}
              <ScheduledStatusBadge status={job.status} />

              {/* Promote countdown */}
              <div className="text-xs text-muted-foreground w-20 text-right shrink-0">
                {job.status === "PROMOTED" ? (
                  <span className="text-violet-400">promoted</span>
                ) : job.hold ? (
                  <span className="text-amber-400">on hold</span>
                ) : job.status === "SKIPPED" ? (
                  <span className="text-slate-500">skipped</span>
                ) : (
                  <span title={new Date(job.promote_after).toLocaleString()}>
                    in {timeUntil(job.promote_after)}
                  </span>
                )}
              </div>

              {/* Added */}
              <div className="text-xs text-white/20 w-16 text-right shrink-0">
                {timeAgo(job.created_at)}
              </div>

              {/* Actions */}
              <RowActions job={job} onAction={invalidate} />
            </div>
          ))}
        </div>
      )}

      {/* Pagination */}
      {data && data.total > 0 && (
        <Pagination
          page={page}
          perPage={25}
          total={data.total}
          onPageChange={setPage}
        />
      )}
    </div>
  );
}
