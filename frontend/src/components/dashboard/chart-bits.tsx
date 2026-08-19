import type { TooltipProps } from "recharts";

export const axisProps = {
  stroke: "var(--muted-foreground)",
  tickLine: false,
  axisLine: false,
  fontSize: 11,
} as const;

export const gridStroke = "oklch(1 0 0 / 8%)";

export function ChartTooltip({
  active,
  payload,
  label,
  suffix = "",
}: TooltipProps<number, string> & { suffix?: string }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="glass rounded-lg px-3 py-2 text-xs">
      {label ? <p className="mb-1 font-medium text-foreground">{String(label)}</p> : null}
      <ul className="space-y-1">
        {payload.map((entry) => (
          <li key={String(entry.dataKey)} className="flex items-center gap-2">
            <span
              className="size-2 rounded-full"
              style={{ background: entry.color ?? "var(--primary)" }}
            />
            <span className="text-muted-foreground">{entry.name}</span>
            <span className="ml-auto font-medium text-foreground">
              {typeof entry.value === "number" ? entry.value.toLocaleString() : entry.value}
              {suffix}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
