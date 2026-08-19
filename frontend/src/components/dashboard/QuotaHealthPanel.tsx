import { EmptyState, GlassCard, SectionHeader, StatusPill } from "./primitives";
import type { QuotaHealth } from "@/lib/analytics/types";

function Meter({ label, used, limit }: { label: string; used: number; limit: number }) {
  const p = limit > 0 ? Math.min((used / limit) * 100, 100) : 0;
  const tone = p > 90 ? "bg-destructive" : p > 70 ? "bg-warning" : "bg-primary";
  return (
    <div>
      <div className="mb-1 flex items-center justify-between text-xs">
        <span className="text-muted-foreground">{label}</span>
        <span className="tabular-nums text-foreground">
          {used} / {limit}
        </span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-white/10">
        <div className={`h-full rounded-full ${tone} transition-[width] duration-500`} style={{ width: `${p}%` }} />
      </div>
    </div>
  );
}

function countdown(iso: string) {
  const ms = new Date(iso).getTime() - Date.now();
  if (ms <= 0) return "resetting now";
  const days = Math.floor(ms / 86400000);
  const hours = Math.floor((ms % 86400000) / 3600000);
  return `${days}d ${hours}h`;
}

export function QuotaHealthPanel({ quota }: { quota: QuotaHealth[] }) {
  return (
    <GlassCard>
      <SectionHeader title="Quota & Profile Health" description="Daily and weekly capacity per profile." />
      {quota.length === 0 ? (
        <EmptyState message="No profiles match the current filters." />
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          {quota.map((q) => {
            const remaining = Math.max(q.connectionsLimit - q.connectionsUsed, 0);
            const weeklyOver = q.weeklyUsed >= q.weeklyLimit;
            return (
              <div key={q.profileId} className="rounded-xl border border-border/70 bg-white/5 p-4">
                <div className="mb-3 flex items-center justify-between gap-2">
                  <p className="truncate font-medium text-foreground">{q.profileName}</p>
                  <StatusPill status={q.status} />
                </div>
                <div className="space-y-3">
                  <Meter label="Connections today" used={q.connectionsUsed} limit={q.connectionsLimit} />
                  <Meter label="Messages today" used={q.messagesUsed} limit={q.messagesLimit} />
                  <Meter label="Weekly usage" used={q.weeklyUsed} limit={q.weeklyLimit} />
                </div>
                <dl className="mt-4 grid grid-cols-3 gap-2 text-xs">
                  <div>
                    <dt className="text-muted-foreground">Remaining</dt>
                    <dd className="mt-0.5 font-medium tabular-nums text-foreground">{remaining} invites</dd>
                  </div>
                  <div>
                    <dt className="text-muted-foreground">Weekly limit</dt>
                    <dd className={`mt-0.5 font-medium ${weeklyOver ? "text-destructive" : "text-success"}`}>
                      {weeklyOver ? "Reached" : "Within limit"}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-muted-foreground">Resets in</dt>
                    <dd className="mt-0.5 font-medium tabular-nums text-foreground">{countdown(q.weeklyResetAt)}</dd>
                  </div>
                </dl>
              </div>
            );
          })}
        </div>
      )}
    </GlassCard>
  );
}
