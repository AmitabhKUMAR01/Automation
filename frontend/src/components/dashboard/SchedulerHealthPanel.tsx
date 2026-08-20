import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { ChevronLeft, ChevronRight } from "lucide-react";

import { EmptyState, GlassCard, SectionHeader, StatusPill, formatJobType } from "./primitives";
import { ChartTooltip, axisProps, gridStroke } from "./chart-bits";
import type { SchedulerHealth, PaginatedRuns } from "@/lib/analytics/types";
import type { AnalyticsFilters } from "@/lib/analytics/types";

const PAGE_SIZE = 10;

// Mirror the active outreach job types from scheduler.py
const PANEL_JOB_TYPES = [
  { id: "all", label: "All jobs" },
  { id: "daily_linkedin_search", label: "LinkedIn Search" },
  { id: "daily_linkedin_acceptance_check", label: "Acceptance Check" },
  { id: "daily_pitch_delivery", label: "Pitch Delivery" },
  { id: "daily_pitch_reply_check", label: "Pitch Replies" },
  { id: "periodic_followup_reply_check", label: "Follow-up Replies" },
];

function buildRunsParams(
  filters: AnalyticsFilters,
  page: number,
  selectedJobType: string,
): URLSearchParams {
  const p = new URLSearchParams();
  if (filters.timeframe) p.set("timeframe", filters.timeframe);
  if (filters.jobStatus) p.set("jobStatus", filters.jobStatus);
  // local job type overrides global jobType filter for the runs table
  if (selectedJobType !== "all") p.set("jobType", selectedJobType);
  p.set("page", String(page));
  p.set("pageSize", String(PAGE_SIZE));
  return p;
}

interface Props {
  scheduler: SchedulerHealth;
  filters: AnalyticsFilters;
}

export function SchedulerHealthPanel({ scheduler, filters }: Props) {
  const total = scheduler.totalSuccess + scheduler.totalFailed;
  const successRate = total ? Math.round((scheduler.totalSuccess / total) * 1000) / 10 : 0;

  const chartData = scheduler.byJobType.map((j) => ({
    ...j,
    label: formatJobType(j.jobType).replace("Daily ", ""),
  }));

  // ── Paginated recent runs ────────────────────────────────────────────────────
  const [page, setPage] = useState(1);
  const [selectedJobType, setSelectedJobType] = useState("all");

  // Reset page + local job type filter whenever global filters change
  useEffect(() => {
    setPage(1);
    setSelectedJobType("all");
  }, [filters.timeframe, filters.jobStatus, filters.jobType]);

  const runsQuery = useQuery<PaginatedRuns>({
    queryKey: ["scheduler-runs", filters, page, selectedJobType],
    queryFn: async () => {
      const params = buildRunsParams(filters, page, selectedJobType);
      const res = await fetch(`/api/scheduler/runs?${params.toString()}`);
      if (!res.ok) throw new Error("Failed to load scheduler runs");
      const result = await res.json();
      return result.data as PaginatedRuns;
    },
    placeholderData: (prev) => prev,
  });

  const runs = runsQuery.data;

  return (
    <GlassCard>
      <SectionHeader title="Scheduler Health" description="Automation job outcomes, durations and retries." />

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {[
          { label: "Successful jobs", value: scheduler.totalSuccess.toLocaleString(), tone: "text-success" },
          { label: "Failed jobs", value: scheduler.totalFailed.toLocaleString(), tone: "text-destructive" },
          { label: "Avg duration", value: `${scheduler.avgDurationSec}s`, tone: "text-foreground" },
          { label: "Retries", value: scheduler.totalRetries.toLocaleString(), tone: "text-warning" },
        ].map((s) => (
          <div key={s.label} className="rounded-xl border border-border/70 bg-white/5 p-3">
            <p className="text-xs text-muted-foreground">{s.label}</p>
            <p className={`mt-1 font-display text-xl font-semibold ${s.tone}`}>{s.value}</p>
          </div>
        ))}
      </div>

      <p className="mt-3 text-xs text-muted-foreground">
        Success rate <span className="font-medium text-foreground">{successRate}%</span> across {total.toLocaleString()} runs.
      </p>

      {chartData.length === 0 ? (
        <div className="mt-4"><EmptyState message="No jobs match the current filters." /></div>
      ) : (
        <div className="mt-4 h-[260px] w-full">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={chartData} margin={{ top: 6, right: 8, left: -18, bottom: 0 }}>
              <CartesianGrid stroke={gridStroke} vertical={false} />
              <XAxis dataKey="label" {...axisProps} interval={0} tick={{ fontSize: 10 }} />
              <YAxis {...axisProps} width={44} />
              <Tooltip content={<ChartTooltip />} cursor={{ fill: "oklch(1 0 0 / 6%)" }} />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              <Bar dataKey="success" name="Success" stackId="a" fill="var(--chart-2)" radius={[0, 0, 0, 0]} maxBarSize={48} />
              <Bar dataKey="failed" name="Failed" stackId="a" fill="var(--chart-5)" radius={[6, 6, 0, 0]} maxBarSize={48} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* ── Recent runs table ───────────────────────────────────────────────── */}
      <div className="mt-6 flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-medium text-foreground">Recent job activity</h3>
        {runs && runs.total > 0 && (
          <span className="text-xs text-muted-foreground">
            {runs.total.toLocaleString()} total runs
          </span>
        )}
      </div>

      {/* Job type filter pills */}
      <div className="mt-2 flex flex-wrap gap-1.5">
        {PANEL_JOB_TYPES.map((jt) => (
          <button
            key={jt.id}
            onClick={() => { setSelectedJobType(jt.id); setPage(1); }}
            className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
              selectedJobType === jt.id
                ? "bg-primary text-primary-foreground"
                : "border border-border/70 bg-white/5 text-muted-foreground hover:bg-white/10 hover:text-foreground"
            }`}
          >
            {jt.label}
          </button>
        ))}
      </div>

      {runsQuery.isError ? (
        <EmptyState message="Failed to load recent runs." />
      ) : !runs || runs.items.length === 0 ? (
        <EmptyState message="No recent runs." />
      ) : (
        <>
          <div className="mt-2 overflow-x-auto">
            <table className="w-full min-w-[560px] text-sm">
              <thead>
                <tr className="text-left text-xs uppercase tracking-wide text-muted-foreground">
                  <th className="pb-2 font-medium">Job</th>
                  <th className="pb-2 font-medium">Status</th>
                  <th className="pb-2 text-right font-medium">Duration</th>
                  <th className="pb-2 text-right font-medium">Retries</th>
                  <th className="pb-2 text-right font-medium">Started At</th>
                </tr>
              </thead>
              <tbody>
                {runs.items.map((r) => (
                  <tr key={r.id} className="border-t border-border/60">
                    <td className="py-2.5 pr-4">
                      <span className="font-medium text-foreground">{formatJobType(r.jobType)}</span>
                      <span className="block text-xs text-muted-foreground font-mono">{r.jobType}</span>
                      {r.message && (
                        <span className="block text-xs text-destructive mt-0.5">{r.message}</span>
                      )}
                    </td>
                    <td className="py-2.5"><StatusPill status={r.status} /></td>
                    <td className="py-2.5 text-right tabular-nums">{r.durationSec}s</td>
                    <td className="py-2.5 text-right tabular-nums">{r.retries}</td>
                    <td className="py-2.5 text-right tabular-nums">
                      <span className="block text-foreground">
                        {new Date(r.startedAt).toLocaleDateString([], { day: "2-digit", month: "short", year: "numeric" })}
                      </span>
                      <span className="block text-xs text-muted-foreground">
                        {new Date(r.startedAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Pagination controls */}
          {runs.totalPages > 1 && (
            <div className="mt-3 flex items-center justify-between border-t border-border/60 pt-3">
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page <= 1 || runsQuery.isFetching}
                className="flex items-center gap-1 rounded-md px-2.5 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-white/10 hover:text-foreground disabled:pointer-events-none disabled:opacity-40"
              >
                <ChevronLeft className="size-3.5" />
                Previous
              </button>

              <span className="text-xs text-muted-foreground">
                Page <span className="font-medium text-foreground">{runs.page}</span> of{" "}
                <span className="font-medium text-foreground">{runs.totalPages}</span>
              </span>

              <button
                onClick={() => setPage((p) => Math.min(runs.totalPages, p + 1))}
                disabled={page >= runs.totalPages || runsQuery.isFetching}
                className="flex items-center gap-1 rounded-md px-2.5 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-white/10 hover:text-foreground disabled:pointer-events-none disabled:opacity-40"
              >
                Next
                <ChevronRight className="size-3.5" />
              </button>
            </div>
          )}
        </>
      )}
    </GlassCard>
  );
}
