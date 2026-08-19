import { RotateCcw } from "lucide-react";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { GlassCard, formatJobType } from "./primitives";
import { JOB_TYPES, LOCATIONS, POSITIONS, PROFILES } from "@/lib/analytics/mock";
import { defaultFilters, type AnalyticsFilters, type Timeframe } from "@/lib/analytics/types";

const TIMEFRAMES: { value: Timeframe; label: string }[] = [
  { value: "7d", label: "7 Days" },
  { value: "30d", label: "30 Days" },
  { value: "all", label: "All Time" },
];

function FilterSelect({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <label className="flex min-w-0 flex-col gap-1.5">
      <span className="text-[0.7rem] font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger className="h-9 w-full border-border/70 bg-white/5 text-sm">
          <SelectValue placeholder={label} />
        </SelectTrigger>
        <SelectContent>
          {options.map((o) => (
            <SelectItem key={o.value} value={o.value}>
              {o.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </label>
  );
}

export function FiltersBar({
  filters,
  onChange,
}: {
  filters: AnalyticsFilters;
  onChange: (next: AnalyticsFilters) => void;
}) {
  const set = <K extends keyof AnalyticsFilters>(key: K, value: AnalyticsFilters[K]) =>
    onChange({ ...filters, [key]: value });

  const all = (label: string) => ({ value: "all", label });

  return (
    <GlassCard className="sticky top-3 z-30">
      <div className="flex flex-wrap items-end gap-3">
        <div className="grid flex-1 grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
          <FilterSelect
            label="Profile"
            value={filters.profileId}
            onChange={(v) => set("profileId", v)}
            options={[all("All profiles"), ...PROFILES.map((p) => ({ value: p.id, label: p.name }))]}
          />
          <FilterSelect
            label="Position"
            value={filters.position}
            onChange={(v) => set("position", v)}
            options={[all("All positions"), ...POSITIONS.map((p) => ({ value: p, label: p }))]}
          />
          <FilterSelect
            label="Location"
            value={filters.location}
            onChange={(v) => set("location", v)}
            options={[all("All locations"), ...LOCATIONS.map((l) => ({ value: l, label: l }))]}
          />
          <FilterSelect
            label="Job Type"
            value={filters.jobType}
            onChange={(v) => set("jobType", v)}
            options={[all("All job types"), ...JOB_TYPES.map((j) => ({ value: j, label: formatJobType(j) }))]}
          />
          <FilterSelect
            label="Job Status"
            value={filters.jobStatus}
            onChange={(v) => set("jobStatus", v)}
            options={[all("Any status"), { value: "success", label: "Success" }, { value: "failed", label: "Failed" }]}
          />
          <FilterSelect
            label="Profile Status"
            value={filters.profileStatus}
            onChange={(v) => set("profileStatus", v)}
            options={[
              all("Any status"),
              { value: "active", label: "Active" },
              { value: "inactive", label: "Inactive" },
              { value: "cooldown", label: "Cooldown" },
            ]}
          />
        </div>

        <div className="flex items-end gap-2">
          <div className="flex flex-col gap-1.5">
            <span className="text-[0.7rem] font-medium uppercase tracking-wide text-muted-foreground">
              Timeframe
            </span>
            <div className="inline-flex rounded-md border border-border/70 bg-white/5 p-0.5">
              {TIMEFRAMES.map((t) => (
                <button
                  key={t.value}
                  onClick={() => set("timeframe", t.value)}
                  className={`rounded px-3 py-1.5 text-xs font-medium transition-colors ${
                    filters.timeframe === t.value
                      ? "bg-primary text-primary-foreground"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {t.label}
                </button>
              ))}
            </div>
          </div>
          <Button
            variant="ghost"
            size="icon"
            className="size-9"
            aria-label="Reset filters"
            onClick={() => onChange(defaultFilters)}
          >
            <RotateCcw className="size-4" />
          </Button>
        </div>
      </div>
    </GlassCard>
  );
}
