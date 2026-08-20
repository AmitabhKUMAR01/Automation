export type Timeframe = "7d" | "30d" | "all";

export interface AnalyticsFilters {
  profileId: string; // "all" | profile id
  timeframe: Timeframe;
  position: string;
  location: string;
  jobType: string;
  jobStatus: string;
  profileStatus: string;
}

export const defaultFilters: AnalyticsFilters = {
  profileId: "all",
  timeframe: "30d",
  position: "all",
  location: "all",
  jobType: "all",
  jobStatus: "all",
  profileStatus: "all",
};

export type ProfileStatus = "active" | "inactive" | "cooldown";

export interface ProfileMeta {
  id: string;
  name: string;
  headline: string;
  status: ProfileStatus;
}

export interface KpiSet {
  connectionsSent: number;
  connectionsAccepted: number;
  acceptanceRate: number;
  messagesSent: number;
  repliesReceived: number;
  replyRate: number;
  dailyQuotaUsage: number; // percentage
  profileHealth: number; // 0-100 score
}

export interface FunnelStage {
  key: string;
  label: string;
  value: number;
  conversionFromPrev: number | null;
}

export interface DailyActivityPoint {
  date: string;
  connectionsSent: number;
  connectionsAccepted: number;
  dmsSent: number;
  repliesReceived: number;
}

export interface ProfileComparisonRow {
  id: string;
  name: string;
  status: ProfileStatus;
  connections: number;
  acceptanceRate: number;
  messages: number;
  replyRate: number;
  quotaUtilization: number;
}

export interface QuotaHealth {
  profileId: string;
  profileName: string;
  status: ProfileStatus;
  connectionsUsed: number;
  connectionsLimit: number;
  messagesUsed: number;
  messagesLimit: number;
  weeklyUsed: number;
  weeklyLimit: number;
  weeklyResetAt: string; // ISO
}

export type JobType =
  | "daily_linkedin_search"
  | "daily_linkedin_acceptance_check"
  | "daily_pitch_delivery"
  | "daily_pitch_reply_check"
  | "daily_followup_reply_check";

export interface JobTypeStats {
  jobType: JobType;
  success: number;
  failed: number;
  avgDurationSec: number;
  retries: number;
}

export interface JobRun {
  id: string;
  jobType: JobType;
  profileName: string;
  status: "success" | "failed" | "running";
  startedAt: string;
  durationSec: number;
  retries: number;
  message: string;
}

export interface SchedulerHealth {
  totalSuccess: number;
  totalFailed: number;
  avgDurationSec: number;
  totalRetries: number;
  byJobType: JobTypeStats[];
  recentRuns: JobRun[];
}

export interface PaginatedRuns {
  items: JobRun[];
  page: number;
  pageSize: number;
  total: number;
  totalPages: number;
}

export interface SegmentPerformance {
  key: string;
  label: string;
  leads: number;
  connections: number;
  acceptanceRate: number;
  messages: number;
  replies: number;
  replyRate: number;
}

export interface ConversionSet {
  leadToConnection: number;
  connectionToAcceptance: number;
  acceptanceToDm: number;
  dmToReply: number;
  leadToReply: number;
}

export interface DashboardData {
  profiles: ProfileMeta[];
  positions: string[];
  locations: string[];
  kpis: KpiSet;
  funnel: FunnelStage[];
  daily: DailyActivityPoint[];
  comparison: ProfileComparisonRow[];
  quota: QuotaHealth[];
  scheduler: SchedulerHealth;
  byPosition: SegmentPerformance[];
  byLocation: SegmentPerformance[];
  conversions: ConversionSet;
}
