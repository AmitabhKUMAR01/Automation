import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { EmptyState, GlassCard, SectionHeader } from "./primitives";
import { ChartTooltip, axisProps, gridStroke } from "./chart-bits";
import type { DailyActivityPoint } from "@/lib/analytics/types";

const SERIES = [
  { key: "connectionsSent", name: "Connections Sent", color: "var(--chart-1)" },
  { key: "connectionsAccepted", name: "Connections Accepted", color: "var(--chart-2)" },
  { key: "dmsSent", name: "DMs Sent", color: "var(--chart-4)" },
  { key: "repliesReceived", name: "Replies Received", color: "var(--chart-3)" },
];

export function DailyActivity({ data }: { data: DailyActivityPoint[] }) {
  return (
    <GlassCard>
      <SectionHeader
        title="Daily Activity"
        description="Outreach volume per day across the selected timeframe."
        action={
          <div className="flex flex-wrap gap-3">
            {SERIES.map((s) => (
              <span key={s.key} className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <span className="size-2 rounded-full" style={{ background: s.color }} />
                {s.name}
              </span>
            ))}
          </div>
        }
      />
      {data.length === 0 ? (
        <EmptyState />
      ) : (
        <div className="h-[300px] w-full">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data} margin={{ top: 6, right: 8, left: -18, bottom: 0 }}>
              <CartesianGrid stroke={gridStroke} vertical={false} />
              <XAxis
                dataKey="date"
                {...axisProps}
                minTickGap={24}
                tickFormatter={(v: string) => v.slice(5)}
              />
              <YAxis {...axisProps} width={44} />
              <Tooltip content={<ChartTooltip />} cursor={{ stroke: "oklch(1 0 0 / 18%)" }} />
              {SERIES.map((s) => (
                <Line
                  key={s.key}
                  type="monotone"
                  dataKey={s.key}
                  name={s.name}
                  stroke={s.color}
                  strokeWidth={2}
                  dot={false}
                  activeDot={{ r: 4 }}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </GlassCard>
  );
}
