import { EmptyState, GlassCard, SectionHeader } from "./primitives";
import type { SegmentPerformance } from "@/lib/analytics/types";

export function SegmentPerformanceTable({
  title,
  description,
  columnLabel,
  rows,
}: {
  title: string;
  description: string;
  columnLabel: string;
  rows: SegmentPerformance[];
}) {
  return (
    <GlassCard>
      <SectionHeader title={title} description={description} />
      {rows.length === 0 ? (
        <EmptyState />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[620px] text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-muted-foreground">
                <th className="pb-2 font-medium">{columnLabel}</th>
                {/* <th className="pb-2 text-right font-medium">Leads</th> */}
                <th className="pb-2 text-right font-medium">Connections</th>
                <th className="pb-2 text-right font-medium">Acceptance %</th>
                <th className="pb-2 text-right font-medium">Messages</th>
                <th className="pb-2 text-right font-medium">Replies</th>
                <th className="pb-2 text-right font-medium">Reply %</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.key} className="border-t border-border/60">
                  <td className="py-2.5 font-medium text-foreground">{r.label}</td>
                  {/* <td className="py-2.5 text-right tabular-nums">{r.leads.toLocaleString()}</td> */}
                  <td className="py-2.5 text-right tabular-nums">{r.connections.toLocaleString()}</td>
                  <td className="py-2.5 text-right tabular-nums">{r.acceptanceRate}%</td>
                  <td className="py-2.5 text-right tabular-nums">{r.messages.toLocaleString()}</td>
                  <td className="py-2.5 text-right tabular-nums">{r.replies.toLocaleString()}</td>
                  <td className="py-2.5 text-right tabular-nums">{r.replyRate}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </GlassCard>
  );
}
