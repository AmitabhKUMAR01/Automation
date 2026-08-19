import { GlassCard, SectionHeader } from "./primitives";
import type { ConversionSet } from "@/lib/analytics/types";

export function OverallConversion({ conversions }: { conversions: ConversionSet }) {
  const steps = [
    { label: "Lead → Connection", value: conversions.leadToConnection },
    { label: "Connection → Acceptance", value: conversions.connectionToAcceptance },
    { label: "Acceptance → DM", value: conversions.acceptanceToDm },
    { label: "DM → Reply", value: conversions.dmToReply },
  ];

  return (
    <GlassCard>
      <SectionHeader title="Overall Conversion" description="Stage-to-stage efficiency of the outreach engine." />
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {steps.map((s) => (
          <div key={s.label} className="rounded-xl border border-border/70 bg-white/5 p-4">
            <p className="text-xs text-muted-foreground">{s.label}</p>
            <p className="mt-1 font-display text-2xl font-semibold text-foreground">{s.value}%</p>
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
        <p className="text-sm font-medium text-foreground">Overall Lead → Reply</p>
        <p className="font-display text-2xl font-semibold text-foreground">{conversions.leadToReply}%</p>
      </div>
    </GlassCard>
  );
}
