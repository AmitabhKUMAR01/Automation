import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { Loader2, RefreshCw, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { FiltersBar } from "@/components/dashboard/FiltersBar";
import { KpiCards } from "@/components/dashboard/KpiCards";
import { OutreachFunnel } from "@/components/dashboard/OutreachFunnel";
import { DailyActivity } from "@/components/dashboard/DailyActivity";
import { ProfileComparison } from "@/components/dashboard/ProfileComparison";
import { QuotaHealthPanel } from "@/components/dashboard/QuotaHealthPanel";
import { SchedulerHealthPanel } from "@/components/dashboard/SchedulerHealthPanel";
import { SegmentPerformanceTable } from "@/components/dashboard/SegmentPerformanceTable";
import { OverallConversion } from "@/components/dashboard/OverallConversion";
import { ErrorState } from "@/components/dashboard/primitives";
import { dashboardQueryOptions } from "@/lib/analytics/api";
import { defaultFilters, type AnalyticsFilters, type KpiSet, type FunnelStage, type ProfileComparisonRow, type DailyActivityPoint, type SchedulerHealth, type QuotaHealth } from "@/lib/analytics/types";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "LinkedIn Profile Performance Analytics Dashboard" },
      {
        name: "description",
        content:
          "Track LinkedIn outreach performance: connections, acceptance rate, DM replies, quota health and scheduler jobs in one analytics dashboard.",
      },
      { property: "og:title", content: "LinkedIn Profile Performance Analytics Dashboard" },
      {
        property: "og:description",
        content:
          "Monitor outreach funnels, daily activity, profile quotas and automation job health across every LinkedIn profile.",
      },
    ],
  }),
  component: DashboardPage,
});

function DashboardPage() {
  const [filters, setFilters] = useState<AnalyticsFilters>(defaultFilters);
  const query = useQuery(dashboardQueryOptions(filters));
  const { data, isPending, isFetching, isError, error, refetch } = query;

  const kpisQuery = useQuery({
    queryKey: ["profile-kpis", filters] as const,
    queryFn: async () => {
      const params = new URLSearchParams();
      if (filters.profileId) params.set("profileId", filters.profileId);
      if (filters.timeframe) params.set("timeframe", filters.timeframe);
      if (filters.position) params.set("position", filters.position);
      if (filters.location) params.set("location", filters.location);
      if (filters.jobType) params.set("jobType", filters.jobType);
      if (filters.jobStatus) params.set("jobStatus", filters.jobStatus);
      if (filters.profileStatus) params.set("profileStatus", filters.profileStatus);

      const res = await fetch(`/api/profile/kpis?${params.toString()}`);
      if (!res.ok) throw new Error("Failed to load KPI metrics");
      const result = await res.json();
      return result.data.kpis as KpiSet;
    },
  });

  const funnelQuery = useQuery({
    queryKey: ["profile-funnel", filters] as const,
    queryFn: async () => {
      const params = new URLSearchParams();
      if (filters.profileId) params.set("profileId", filters.profileId);
      if (filters.timeframe) params.set("timeframe", filters.timeframe);
      if (filters.position) params.set("position", filters.position);
      if (filters.location) params.set("location", filters.location);
      if (filters.jobType) params.set("jobType", filters.jobType);
      if (filters.jobStatus) params.set("jobStatus", filters.jobStatus);
      if (filters.profileStatus) params.set("profileStatus", filters.profileStatus);

      const res = await fetch(`/api/profile/funnel?${params.toString()}`);
      if (!res.ok) throw new Error("Failed to load funnel data");
      const result = await res.json();
      return result.data as FunnelStage[];
    },
  });

  const comparisonQuery = useQuery({
    queryKey: ["profile-comparison", filters] as const,
    queryFn: async () => {
      const params = new URLSearchParams();
      if (filters.timeframe) params.set("timeframe", filters.timeframe);
      if (filters.position) params.set("position", filters.position);
      if (filters.location) params.set("location", filters.location);
      if (filters.jobType) params.set("jobType", filters.jobType);
      if (filters.jobStatus) params.set("jobStatus", filters.jobStatus);
      if (filters.profileStatus) params.set("profileStatus", filters.profileStatus);

      const res = await fetch(`/api/profile/comparison?${params.toString()}`);
      if (!res.ok) throw new Error("Failed to load profile comparison");
      const result = await res.json();
      return result.data as ProfileComparisonRow[];
    },
  });

  const dailyQuery = useQuery({
    queryKey: ["profile-daily", filters] as const,
    queryFn: async () => {
      const params = new URLSearchParams();
      if (filters.profileId) params.set("profileId", filters.profileId);
      if (filters.timeframe) params.set("timeframe", filters.timeframe);
      if (filters.position) params.set("position", filters.position);
      if (filters.location) params.set("location", filters.location);
      if (filters.jobType) params.set("jobType", filters.jobType);
      if (filters.jobStatus) params.set("jobStatus", filters.jobStatus);
      if (filters.profileStatus) params.set("profileStatus", filters.profileStatus);

      const res = await fetch(`/api/profile/daily?${params.toString()}`);
      if (!res.ok) throw new Error("Failed to load daily activity");
      const result = await res.json();
      return result.data as DailyActivityPoint[];
    },
  });

  const schedulerHealthQuery = useQuery({
    queryKey: ["scheduler-health", filters] as const,
    queryFn: async () => {
      const params = new URLSearchParams();
      if (filters.timeframe) params.set("timeframe", filters.timeframe);
      if (filters.jobStatus) params.set("jobStatus", filters.jobStatus);
      if (filters.jobType) params.set("jobType", filters.jobType);

      const res = await fetch(`/api/scheduler/health?${params.toString()}`);
      if (!res.ok) throw new Error("Failed to load scheduler health");
      const result = await res.json();
      return result.data as SchedulerHealth;
    },
  });
  const quotaHealthQuery = useQuery({
    queryKey: ["quota-health", filters] as const,
    queryFn: async () => {
      const params = new URLSearchParams();
      if (filters.profileStatus) params.set("profileStatus", filters.profileStatus);

      const res = await fetch(`/api/profile/quota-health?${params.toString()}`);
      if (!res.ok) throw new Error("Failed to load quota health");
      const result = await res.json();
      return result.data as QuotaHealth[];
    },
  });
  const handleRefresh = () => {
    refetch();
    kpisQuery.refetch();
    funnelQuery.refetch();
    comparisonQuery.refetch();
    dailyQuery.refetch();
    schedulerHealthQuery.refetch();
    quotaHealthQuery.refetch();
  };

  const isKpisLoading = kpisQuery.isPending;
  const isKpisFetching = kpisQuery.isFetching;
  const isFunnelFetching = funnelQuery.isFetching;
  const isComparisonFetching = comparisonQuery.isFetching;
  const isDailyFetching = dailyQuery.isFetching;
  const isSchedulerFetching = schedulerHealthQuery.isFetching;
  const isQuotaFetching = quotaHealthQuery.isFetching;

  return (
    <main className="mx-auto w-full max-w-[1400px] px-4 pb-16 pt-6 sm:px-6">
      <header className="mb-5 flex flex-wrap items-center justify-between gap-4">
        <div>
          <p className="flex items-center gap-2 text-xs font-medium uppercase tracking-[0.18em] text-primary">
            <Sparkles className="size-3.5" aria-hidden />
            Outreach intelligence
          </p>
          <h1 className="mt-2 font-display text-2xl font-semibold tracking-tight text-foreground sm:text-3xl">
            LinkedIn Profile Performance
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Connections, conversations and automation health across every connected profile.
          </p>
        </div>
        <Button variant="outline" onClick={handleRefresh} disabled={isFetching || isKpisFetching || isFunnelFetching || isComparisonFetching || isDailyFetching || isSchedulerFetching || isQuotaFetching} className="gap-2">
          {isFetching || isKpisFetching || isFunnelFetching || isComparisonFetching || isDailyFetching || isSchedulerFetching || isQuotaFetching ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
          Refresh
        </Button>
      </header>

      <FiltersBar filters={filters} onChange={setFilters} />

      <div className="mt-5 space-y-5">
        {isError || kpisQuery.isError || funnelQuery.isError || comparisonQuery.isError || dailyQuery.isError || schedulerHealthQuery.isError || quotaHealthQuery.isError ? (
          <ErrorState
            message={
              (error as Error)?.message ?? 
              (kpisQuery.error as Error)?.message ?? 
              (funnelQuery.error as Error)?.message ?? 
              (comparisonQuery.error as Error)?.message ?? 
              (dailyQuery.error as Error)?.message ?? 
              (schedulerHealthQuery.error as Error)?.message ?? 
              (quotaHealthQuery.error as Error)?.message ?? 
              "We couldn't load analytics data."
            }
            onRetry={handleRefresh}
          />
        ) : (
          <>
            <KpiCards kpis={kpisQuery.data} loading={isKpisLoading} />

            {isPending || kpisQuery.isPending || funnelQuery.isPending || comparisonQuery.isPending || dailyQuery.isPending || schedulerHealthQuery.isPending || quotaHealthQuery.isPending || !data || !funnelQuery.data || !comparisonQuery.data || !dailyQuery.data || !schedulerHealthQuery.data || !quotaHealthQuery.data ? (
              <>
                <div className="grid gap-5 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.4fr)]">
                  <Skeleton className="h-[380px] rounded-2xl" />
                  <Skeleton className="h-[380px] rounded-2xl" />
                </div>
                <Skeleton className="h-[420px] rounded-2xl" />
                <Skeleton className="h-[360px] rounded-2xl" />
              </>
            ) : (
              <>
                <div className="grid gap-5 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.4fr)]">
                  <OutreachFunnel stages={funnelQuery.data} />
                  <DailyActivity data={dailyQuery.data} />
                </div>

                <ProfileComparison rows={comparisonQuery.data} />
                 <QuotaHealthPanel quota={quotaHealthQuery.data} />
                <SchedulerHealthPanel scheduler={schedulerHealthQuery.data} filters={filters} />

                <div className="grid gap-5 xl:grid-cols-2">
                  <SegmentPerformanceTable
                    title="Keyword Performance"
                    description="Results broken down by target position."
                    columnLabel="Target position"
                    rows={data.byPosition}
                  />
                  <SegmentPerformanceTable
                    title="Location Performance"
                    description="Results broken down by target location."
                    columnLabel="Location"
                    rows={data.byLocation}
                  />
                </div>

                <OverallConversion conversions={data.conversions} />
              </>
            )}
          </>
        )}
      </div>
    </main>
  );
}
