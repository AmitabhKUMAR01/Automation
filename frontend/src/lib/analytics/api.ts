import { buildMockDashboard } from "./mock";
import type { AnalyticsFilters, DashboardData } from "./types";

/**
 * Single data-access point for the dashboard.
 *
 * To wire the real backend, replace the body of `fetchDashboard` with a fetch
 * call to your endpoint and keep the `DashboardData` shape (see ./types.ts):
 *
 *   const res = await fetch(`${API_BASE}/analytics/dashboard?${toQuery(filters)}`);
 *   if (!res.ok) throw new Error(await res.text());
 *   return (await res.json()) as DashboardData;
 */
export const USE_MOCK_DATA = true;
export const API_BASE = "/api";

export function toQuery(filters: AnalyticsFilters): string {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([key, value]) => {
    if (value && value !== "all") params.set(key, String(value));
  });
  return params.toString();
}

export async function fetchDashboard(filters: AnalyticsFilters): Promise<DashboardData> {
  if (USE_MOCK_DATA) {
    await new Promise((r) => setTimeout(r, 450));
    return buildMockDashboard(filters);
  }

  const res = await fetch(`${API_BASE}/analytics/dashboard?${toQuery(filters)}`);
  if (!res.ok) throw new Error(`Failed to load analytics (${res.status})`);
  return (await res.json()) as DashboardData;
}

export const dashboardQueryOptions = (filters: AnalyticsFilters) => ({
  queryKey: ["dashboard", filters] as const,
  queryFn: () => fetchDashboard(filters),
  staleTime: 30_000,
});
