import { useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { EmptyState, GlassCard, SectionHeader, StatusPill } from "./primitives";
import { ChartTooltip, axisProps, gridStroke } from "./chart-bits";
import type { ProfileComparisonRow } from "@/lib/analytics/types";

const METRICS = [
  { key: "connections", label: "Connections", suffix: "" },
  { key: "acceptanceRate", label: "Acceptance %", suffix: "%" },
  { key: "messages", label: "Messages", suffix: "" },
  { key: "replyRate", label: "Reply %", suffix: "%" },
  { key: "quotaUtilization", label: "Quota Utilization", suffix: "%" },
] as const;

export function ProfileComparison({ rows }: { rows: ProfileComparisonRow[] }) {
  const [metric, setMetric] = useState<(typeof METRICS)[number]["key"]>("connections");
  const active = METRICS.find((m) => m.key === metric)!;

  return (
    <GlassCard>
      <SectionHeader
        title="Profile Comparison"
        description="Benchmark every connected profile on the selected metric."
        action={
          <div className="flex flex-wrap gap-1 rounded-md border border-border/70 bg-white/5 p-0.5">
            {METRICS.map((m) => (
              <button
                key={m.key}
                onClick={() => setMetric(m.key)}
                className={`rounded px-2.5 py-1 text-xs font-medium transition-colors ${
                  metric === m.key
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:text-foreground"
                }`}
              >
                {m.label}
              </button>
            ))}
          </div>
        }
      />
      {rows.length === 0 ? (
        <EmptyState message="No profiles match the current filters." />
      ) : (
        <>
          <div className="h-[260px] w-full">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={rows} margin={{ top: 6, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid stroke={gridStroke} vertical={false} />
                <XAxis dataKey="name" {...axisProps} interval={0} tickFormatter={(v: string) => v.split(" ")[0] ?? v} />
                <YAxis {...axisProps} width={44} />
                <Tooltip
                  content={<ChartTooltip suffix={active.suffix} />}
                  cursor={{ fill: "oklch(1 0 0 / 6%)" }}
                />
                <Bar dataKey={metric} name={active.label} fill="var(--chart-1)" radius={[6, 6, 0, 0]} maxBarSize={54} />
              </BarChart>
            </ResponsiveContainer>
          </div>

          <div className="mt-4 overflow-x-auto">
            <table className="w-full min-w-[620px] text-sm">
              <thead>
                <tr className="text-left text-xs uppercase tracking-wide text-muted-foreground">
                  <th className="pb-2 font-medium">Profile</th>
                  <th className="pb-2 font-medium">Status</th>
                  <th className="pb-2 text-right font-medium">Connections</th>
                  <th className="pb-2 text-right font-medium">Acceptance %</th>
                  <th className="pb-2 text-right font-medium">Messages</th>
                  <th className="pb-2 text-right font-medium">Reply %</th>
                  <th className="pb-2 text-right font-medium">Quota %</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id} className="border-t border-border/60">
                    <td className="py-2.5 font-medium text-foreground">{r.name}</td>
                    <td className="py-2.5"><StatusPill status={r.status} /></td>
                    <td className="py-2.5 text-right tabular-nums">{r.connections.toLocaleString()}</td>
                    <td className="py-2.5 text-right tabular-nums">{r.acceptanceRate}%</td>
                    <td className="py-2.5 text-right tabular-nums">{r.messages.toLocaleString()}</td>
                    <td className="py-2.5 text-right tabular-nums">{r.replyRate}%</td>
                    <td className="py-2.5 text-right tabular-nums">{r.quotaUtilization}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </GlassCard>
  );
}
