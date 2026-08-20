import { useState, useEffect } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { EmptyState, GlassCard, SectionHeader, StatusPill } from "./primitives";
import type { QuotaHealth } from "@/lib/analytics/types";

const PAGE_SIZE = 4;

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
  const [page, setPage] = useState(1);

  // Reset to first page if total number of items changes (due to filtering)
  useEffect(() => {
    setPage(1);
  }, [quota.length]);

  const totalPages = Math.ceil(quota.length / PAGE_SIZE);
  const startIndex = (page - 1) * PAGE_SIZE;
  const paginatedQuota = quota.slice(startIndex, startIndex + PAGE_SIZE);

  return (
    <GlassCard>
      <SectionHeader 
        title="Quota & Profile Health" 
        description="Daily and weekly capacity per profile."
        action={
          quota.length > 0 && (
            <span className="text-xs text-muted-foreground font-medium">
              {quota.length} profile{quota.length !== 1 ? "s" : ""} total
            </span>
          )
        }
      />
      {quota.length === 0 ? (
        <EmptyState message="No profiles match the current filters." />
      ) : (
        <>
          <div className="grid gap-3 md:grid-cols-2">
            {paginatedQuota.map((q) => {
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

          {/* Pagination Controls */}
          {totalPages > 1 && (
            <div className="mt-4 flex items-center justify-between border-t border-border/60 pt-3">
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page <= 1}
                className="flex items-center gap-1 rounded-md px-2.5 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-white/10 hover:text-foreground disabled:pointer-events-none disabled:opacity-40"
              >
                <ChevronLeft className="size-3.5" />
                Previous
              </button>

              <span className="text-xs text-muted-foreground">
                Page <span className="font-medium text-foreground">{page}</span> of{" "}
                <span className="font-medium text-foreground">{totalPages}</span>
              </span>

              <button
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={page >= totalPages}
                className="flex items-center gap-1 rounded-md px-2.5 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-white/10 hover:text-foreground disabled:pointer-events-none disabled:opacity-40"
              >
                Next
                <ChevronRight className="size-3.5" />
              </button>
            </div>
          )}
        </>
      )}
    </GlassCard>
  );
}
