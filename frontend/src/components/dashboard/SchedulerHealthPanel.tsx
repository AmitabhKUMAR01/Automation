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

import { EmptyState, GlassCard, SectionHeader, StatusPill, formatJobType } from "./primitives";
import { ChartTooltip, axisProps, gridStroke } from "./chart-bits";
import type { SchedulerHealth } from "@/lib/analytics/types";

export function SchedulerHealthPanel({ scheduler }: { scheduler: SchedulerHealth }) {
  const total = scheduler.totalSuccess + scheduler.totalFailed;
  const successRate = total ? Math.round((scheduler.totalSuccess / total) * 1000) / 10 : 0;

  const chartData = scheduler.byJobType.map((j) => ({
    ...j,
    label: formatJobType(j.jobType).replace("Daily ", ""),
  }));

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

      <h3 className="mt-6 mb-2 text-sm font-medium text-foreground">Recent job activity</h3>
      {scheduler.recentRuns.length === 0 ? (
        <EmptyState message="No recent runs." />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[680px] text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-muted-foreground">
                <th className="pb-2 font-medium">Job</th>
                <th className="pb-2 font-medium">Profile</th>
                <th className="pb-2 font-medium">Status</th>
                <th className="pb-2 text-right font-medium">Duration</th>
                <th className="pb-2 text-right font-medium">Retries</th>
                <th className="pb-2 text-right font-medium">Started</th>
              </tr>
            </thead>
            <tbody>
              {scheduler.recentRuns.map((r) => (
                <tr key={r.id} className="border-t border-border/60">
                  <td className="py-2.5">
                    <span className="font-medium text-foreground">{formatJobType(r.jobType)}</span>
                    <span className="block text-xs text-muted-foreground">{r.message}</span>
                  </td>
                  <td className="py-2.5 text-muted-foreground">{r.profileName}</td>
                  <td className="py-2.5"><StatusPill status={r.status} /></td>
                  <td className="py-2.5 text-right tabular-nums">{r.durationSec}s</td>
                  <td className="py-2.5 text-right tabular-nums">{r.retries}</td>
                  <td className="py-2.5 text-right text-muted-foreground tabular-nums">
                    {new Date(r.startedAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </GlassCard>
  );
}
