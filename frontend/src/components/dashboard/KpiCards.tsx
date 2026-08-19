import {
  Activity,
  CheckCircle2,
  Gauge,
  HeartPulse,
  MessageSquare,
  Percent,
  Reply,
  Send,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { Skeleton } from "@/components/ui/skeleton";
import { GlassCard } from "./primitives";
import type { KpiSet } from "@/lib/analytics/types";

interface Kpi {
  label: string;
  value: string;
  hint: string;
  icon: LucideIcon;
  tone: string;
}

function buildKpis(k: KpiSet): Kpi[] {
  return [
    { label: "Connections Sent", value: k.connectionsSent.toLocaleString(), hint: "Invites dispatched", icon: Send, tone: "text-chart-1" },
    { label: "Connections Accepted", value: k.connectionsAccepted.toLocaleString(), hint: "Invites accepted", icon: CheckCircle2, tone: "text-chart-2" },
    { label: "Acceptance Rate", value: `${k.acceptanceRate}%`, hint: "Accepted ÷ sent", icon: Percent, tone: "text-chart-2" },
    { label: "Messages Sent", value: k.messagesSent.toLocaleString(), hint: "DMs delivered", icon: MessageSquare, tone: "text-chart-4" },
    { label: "Replies Received", value: k.repliesReceived.toLocaleString(), hint: "Inbound responses", icon: Reply, tone: "text-chart-3" },
    { label: "Reply Rate", value: `${k.replyRate}%`, hint: "Replies ÷ DMs", icon: Activity, tone: "text-chart-3" },
    { label: "Daily Quota Usage", value: `${k.dailyQuotaUsage}%`, hint: "Avg across profiles", icon: Gauge, tone: "text-warning" },
    { label: "Profile Health", value: `${k.profileHealth}/100`, hint: "Composite score", icon: HeartPulse, tone: "text-success" },
  ];
}

export function KpiCards({ kpis, loading }: { kpis: KpiSet | undefined; loading: boolean }) {
  if (loading || !kpis) {
    return (
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {Array.from({ length: 8 }).map((_, i) => (
          <Skeleton key={i} className="h-[104px] rounded-2xl" />
        ))}
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
      {buildKpis(kpis).map((kpi) => (
        <GlassCard key={kpi.label} className="hover:border-primary/40">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="truncate text-xs font-medium uppercase tracking-wide text-muted-foreground">
                {kpi.label}
              </p>
              <p className="mt-2 font-display text-2xl font-semibold tracking-tight text-foreground">
                {kpi.value}
              </p>
              <p className="mt-1 text-xs text-muted-foreground">{kpi.hint}</p>
            </div>
            <span className="rounded-lg bg-white/5 p-2">
              <kpi.icon className={`size-4 ${kpi.tone}`} aria-hidden />
            </span>
          </div>
        </GlassCard>
      ))}
    </div>
  );
}
