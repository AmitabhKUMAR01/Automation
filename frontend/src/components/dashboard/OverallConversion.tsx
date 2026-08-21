import { GlassCard, SectionHeader } from "./primitives";
import type { ConversionSet } from "@/lib/analytics/types";

export function OverallConversion({ conversions }: { conversions: ConversionSet }) {
  const raw = conversions.rawCounts;

  const steps = [
    {
      label: "Sent → Accepted",
      value: conversions.sentToAccepted,
      sub: raw ? `${raw.accepted} of ${raw.connectionsSent} accepted` : undefined,
    },
    {
      label: "Accepted → DM",
      value: conversions.acceptedToDm,
      sub: raw ? `${raw.dmsSent} of ${raw.accepted} pitched` : undefined,
    },
    {
      label: "DM → Reply",
      value: conversions.dmToReply,
      sub: raw ? `${raw.replies} of ${raw.dmsSent} replied` : undefined,
    },
  ];

  return (
    <GlassCard>
      <SectionHeader
        title="Overall Conversion"
        description="Stage-to-stage efficiency of the outreach engine."
      />
      <div className="grid gap-3 sm:grid-cols-3">
        {steps.map((s) => (
          <div key={s.label} className="rounded-xl border border-border/70 bg-white/5 p-4">
            <p className="text-xs text-muted-foreground">{s.label}</p>
            <p className="mt-1 font-display text-2xl font-semibold text-foreground">
              {s.value}%
            </p>
            {s.sub && (
              <p className="mt-0.5 text-[0.7rem] text-muted-foreground">{s.sub}</p>
            )}
            <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-white/10">
              <div
                className="h-full rounded-full bg-primary transition-[width] duration-500"
                style={{ width: `${Math.min(s.value, 100)}%` }}
              />
            </div>
          </div>
        ))}
      </div>
      <div className="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-xl border border-primary/30 bg-primary/10 px-4 py-3">
        <div>
          <p className="text-sm font-medium text-foreground">Overall: Sent → Reply</p>
          {raw && (
            <p className="text-[0.7rem] text-muted-foreground">
              {raw.replies} replies from {raw.connectionsSent} connections sent
            </p>
          )}
        </div>
        <p className="font-display text-2xl font-semibold text-foreground">
          {conversions.overallSentToReply}%
        </p>
      </div>
    </GlassCard>
  );
}
