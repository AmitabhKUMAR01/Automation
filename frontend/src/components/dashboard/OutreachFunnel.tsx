import { ChevronDown } from "lucide-react";

import { GlassCard, EmptyState, SectionHeader } from "./primitives";
import type { FunnelStage } from "@/lib/analytics/types";

export function OutreachFunnel({ stages }: { stages: FunnelStage[] }) {
  const max = Math.max(...stages.map((s) => s.value), 1);
  if (!stages.length || max <= 1) {
    return (
      <GlassCard>
        <SectionHeader title="Outreach Funnel" />
        <EmptyState />
      </GlassCard>
    );
  }

  return (
    <GlassCard>
      <SectionHeader title="Outreach Funnel" description="Stage volumes with conversion between steps." />
      <ol className="space-y-2">
        {stages.map((stage, i) => (
          <li key={stage.key}>
            {i > 0 ? (
              <div className="flex items-center gap-2 pb-2 pl-1 text-xs text-muted-foreground">
                <ChevronDown className="size-3.5" aria-hidden />
                <span className="font-medium text-foreground">{stage.conversionFromPrev}%</span>
                <span>conversion from {stages[i - 1]?.label}</span>
              </div>
            ) : null}
            <div className="relative overflow-hidden rounded-xl border border-border/70 bg-white/5">
              <div
                className="absolute inset-y-0 left-0 bg-gradient-to-r from-primary/45 to-primary/10 transition-[width] duration-500"
                style={{ width: `${Math.max((stage.value / max) * 100, 4)}%` }}
              />
              <div className="relative flex items-center justify-between px-4 py-3">
                <span className="text-sm font-medium text-foreground">{stage.label}</span>
                <span className="font-display text-sm font-semibold text-foreground">
                  {stage.value.toLocaleString()}
                </span>
              </div>
            </div>
          </li>
        ))}
      </ol>
    </GlassCard>
  );
}
