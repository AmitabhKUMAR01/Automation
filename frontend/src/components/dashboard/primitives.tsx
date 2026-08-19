import type { ReactNode } from "react";
import { AlertTriangle, Inbox } from "lucide-react";

import { cn } from "@/lib/utils";
import { Skeleton } from "@/components/ui/skeleton";

export function GlassCard({
  className,
  children,
}: {
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className={cn("glass rounded-2xl p-5 transition-colors duration-200", className)}>
      {children}
    </div>
  );
}

export function SectionHeader({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h2 className="font-display text-lg font-semibold tracking-tight text-foreground">{title}</h2>
        {description ? (
          <p className="mt-1 text-sm text-muted-foreground">{description}</p>
        ) : null}
      </div>
      {action}
    </div>
  );
}

export function ChartSkeleton({ height = 260 }: { height?: number }) {
  return <Skeleton className="w-full rounded-xl" style={{ height }} />;
}

export function EmptyState({ message = "No data for the selected filters." }: { message?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-border/70 py-12 text-center">
      <Inbox className="size-6 text-muted-foreground" aria-hidden />
      <p className="text-sm text-muted-foreground">{message}</p>
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-xl border border-destructive/40 bg-destructive/10 py-10 text-center">
      <AlertTriangle className="size-6 text-destructive" aria-hidden />
      <p className="max-w-sm text-sm text-foreground">{message}</p>
      {onRetry ? (
        <button
          onClick={onRetry}
          className="rounded-md border border-border px-3 py-1.5 text-sm font-medium text-foreground transition-colors hover:bg-accent"
        >
          Retry
        </button>
      ) : null}
    </div>
  );
}

export function StatusPill({ status }: { status: string }) {
  const tone =
    status === "active" || status === "success"
      ? "bg-success/15 text-success border-success/30"
      : status === "cooldown" || status === "running"
        ? "bg-warning/15 text-warning border-warning/30"
        : status === "failed"
          ? "bg-destructive/15 text-destructive border-destructive/30"
          : "bg-muted text-muted-foreground border-border";
  return (
    <span className={cn("inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium capitalize", tone)}>
      {status}
    </span>
  );
}

export function formatJobType(jobType: string) {
  return jobType.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
