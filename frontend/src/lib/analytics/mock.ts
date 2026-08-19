import type {
  AnalyticsFilters,
  DailyActivityPoint,
  DashboardData,
  FunnelStage,
  JobRun,
  JobType,
  ProfileMeta,
  SegmentPerformance,
} from "./types";

export const JOB_TYPES: JobType[] = [
  "daily_linkedin_search",
  "daily_linkedin_acceptance_check",
  "daily_pitch_delivery",
  "daily_pitch_reply_check",
  "daily_followup_reply_check",
];

export const PROFILES: ProfileMeta[] = [
  { id: "p1", name: "Ankit Singh", headline: "Founder — GrowthLoop", status: "active" },
  { id: "p2", name: "Maya Fernandes", headline: "AE — Northbound", status: "active" },
  { id: "p3", name: "Dev Kapoor", headline: "SDR — Northbound", status: "cooldown" },
  { id: "p4", name: "Lena Ortiz", headline: "Recruiter — Talentwise", status: "inactive" },
];

export const POSITIONS = [
  "Head of Engineering",
  "VP Marketing",
  "Product Manager",
  "Talent Partner",
  "Founder / CEO",
];

export const LOCATIONS = ["Bengaluru", "London", "New York", "Berlin", "Singapore"];

// Deterministic pseudo-random so charts don't jump between renders.
function rng(seed: number) {
  let s = seed % 2147483647;
  if (s <= 0) s += 2147483646;
  return () => (s = (s * 16807) % 2147483647) / 2147483647;
}

function hash(str: string) {
  let h = 7;
  for (let i = 0; i < str.length; i++) h = (h * 31 + str.charCodeAt(i)) % 100000;
  return h;
}

function pct(a: number, b: number) {
  return b > 0 ? Math.round((a / b) * 1000) / 10 : 0;
}

function daysFor(timeframe: AnalyticsFilters["timeframe"]) {
  return timeframe === "7d" ? 7 : timeframe === "30d" ? 30 : 90;
}

function seriesForProfile(profileId: string, days: number, scale: number): DailyActivityPoint[] {
  const rand = rng(hash(profileId) + 13);
  const out: DailyActivityPoint[] = [];
  const today = new Date();
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(today);
    d.setDate(today.getDate() - i);
    const weekend = d.getDay() === 0 || d.getDay() === 6;
    const base = (weekend ? 0.35 : 1) * scale;
    const connectionsSent = Math.round((14 + rand() * 12) * base);
    const connectionsAccepted = Math.round(connectionsSent * (0.28 + rand() * 0.22));
    const dmsSent = Math.round(connectionsAccepted * (0.65 + rand() * 0.3));
    const repliesReceived = Math.round(dmsSent * (0.15 + rand() * 0.25));
    out.push({
      date: d.toISOString().slice(0, 10),
      connectionsSent,
      connectionsAccepted,
      dmsSent,
      repliesReceived,
    });
  }
  return out;
}

function segmentScale(filters: AnalyticsFilters) {
  let scale = 1;
  if (filters.position !== "all") scale *= 0.32;
  if (filters.location !== "all") scale *= 0.36;
  if (filters.profileStatus !== "all") scale *= 0.7;
  if (filters.jobType !== "all") scale *= 0.8;
  if (filters.jobStatus === "failed") scale *= 0.35;
  return scale;
}

function buildSegments(
  keys: string[],
  totals: { leads: number; connections: number; accepted: number; messages: number; replies: number },
  seed: number,
): SegmentPerformance[] {
  const rand = rng(seed);
  const weights = keys.map(() => 0.5 + rand());
  const sum = weights.reduce((a, b) => a + b, 0);
  return keys.map((key, i) => {
    const w = (weights[i] ?? 1) / sum;
    const leads = Math.round(totals.leads * w);
    const connections = Math.round(totals.connections * w);
    const accepted = Math.round(totals.accepted * w);
    const messages = Math.round(totals.messages * w);
    const replies = Math.round(totals.replies * w);
    return {
      key,
      label: key,
      leads,
      connections,
      acceptanceRate: pct(accepted, connections),
      messages,
      replies,
      replyRate: pct(replies, messages),
    };
  });
}

export function buildMockDashboard(filters: AnalyticsFilters): DashboardData {
  const days = daysFor(filters.timeframe);
  const scale = segmentScale(filters);

  const activeProfiles = PROFILES.filter(
    (p) =>
      (filters.profileId === "all" || p.id === filters.profileId) &&
      (filters.profileStatus === "all" || p.status === filters.profileStatus),
  );

  const perProfileSeries = activeProfiles.map((p) => ({
    profile: p,
    series: seriesForProfile(p.id, days, scale * (p.status === "active" ? 1 : p.status === "cooldown" ? 0.4 : 0.12)),
  }));

  const daily: DailyActivityPoint[] = Array.from({ length: days }, (_, i) => {
    const date = perProfileSeries[0]?.series[i]?.date ?? "";
    return perProfileSeries.reduce<DailyActivityPoint>(
      (acc, { series }) => {
        const pt = series[i];
        if (!pt) return acc;
        return {
          date: acc.date || pt.date,
          connectionsSent: acc.connectionsSent + pt.connectionsSent,
          connectionsAccepted: acc.connectionsAccepted + pt.connectionsAccepted,
          dmsSent: acc.dmsSent + pt.dmsSent,
          repliesReceived: acc.repliesReceived + pt.repliesReceived,
        };
      },
      { date, connectionsSent: 0, connectionsAccepted: 0, dmsSent: 0, repliesReceived: 0 },
    );
  }).filter((d) => d.date);

  const totals = daily.reduce(
    (a, d) => ({
      connectionsSent: a.connectionsSent + d.connectionsSent,
      connectionsAccepted: a.connectionsAccepted + d.connectionsAccepted,
      dmsSent: a.dmsSent + d.dmsSent,
      repliesReceived: a.repliesReceived + d.repliesReceived,
    }),
    { connectionsSent: 0, connectionsAccepted: 0, dmsSent: 0, repliesReceived: 0 },
  );

  const leads = Math.round(totals.connectionsSent * 1.62);

  const funnel: FunnelStage[] = [
    { key: "leads", label: "Leads Found", value: leads, conversionFromPrev: null },
    {
      key: "sent",
      label: "Connections Sent",
      value: totals.connectionsSent,
      conversionFromPrev: pct(totals.connectionsSent, leads),
    },
    {
      key: "accepted",
      label: "Connections Accepted",
      value: totals.connectionsAccepted,
      conversionFromPrev: pct(totals.connectionsAccepted, totals.connectionsSent),
    },
    {
      key: "dms",
      label: "DMs Sent",
      value: totals.dmsSent,
      conversionFromPrev: pct(totals.dmsSent, totals.connectionsAccepted),
    },
    {
      key: "replies",
      label: "Replies Received",
      value: totals.repliesReceived,
      conversionFromPrev: pct(totals.repliesReceived, totals.dmsSent),
    },
  ];

  const quota = activeProfiles.map((p) => {
    const rand = rng(hash(p.id) + 91);
    const connectionsLimit = 40;
    const messagesLimit = 60;
    const factor = p.status === "active" ? 0.6 + rand() * 0.4 : p.status === "cooldown" ? 0.15 : 0;
    const connectionsUsed = Math.round(connectionsLimit * factor);
    const messagesUsed = Math.round(messagesLimit * factor * 0.9);
    const reset = new Date();
    reset.setDate(reset.getDate() + ((7 - reset.getDay()) % 7 || 7));
    reset.setHours(0, 0, 0, 0);
    return {
      profileId: p.id,
      profileName: p.name,
      status: p.status,
      connectionsUsed,
      connectionsLimit,
      messagesUsed,
      messagesLimit,
      weeklyUsed: Math.round(connectionsUsed * 5.4),
      weeklyLimit: 200,
      weeklyResetAt: reset.toISOString(),
    };
  });

  const comparison = perProfileSeries.map(({ profile, series }) => {
    const t = series.reduce(
      (a, d) => ({
        sent: a.sent + d.connectionsSent,
        accepted: a.accepted + d.connectionsAccepted,
        dms: a.dms + d.dmsSent,
        replies: a.replies + d.repliesReceived,
      }),
      { sent: 0, accepted: 0, dms: 0, replies: 0 },
    );
    const q = quota.find((x) => x.profileId === profile.id);
    return {
      id: profile.id,
      name: profile.name,
      status: profile.status,
      connections: t.sent,
      acceptanceRate: pct(t.accepted, t.sent),
      messages: t.dms,
      replyRate: pct(t.replies, t.dms),
      quotaUtilization: q ? pct(q.connectionsUsed, q.connectionsLimit) : 0,
    };
  });

  const jobTypes = filters.jobType === "all" ? JOB_TYPES : (JOB_TYPES.filter((j) => j === filters.jobType) as JobType[]);
  const byJobType = jobTypes.map((jobType) => {
    const rand = rng(hash(jobType) + days);
    const success = Math.round((days * activeProfiles.length || 1) * (0.8 + rand() * 0.4));
    const failed = Math.round(success * (0.03 + rand() * 0.09));
    return {
      jobType,
      success: filters.jobStatus === "failed" ? 0 : success,
      failed: filters.jobStatus === "success" ? 0 : failed,
      avgDurationSec: Math.round((8 + rand() * 40) * 10) / 10,
      retries: Math.round(failed * (1 + rand())),
    };
  });

  const recentRuns: JobRun[] = Array.from({ length: 12 }, (_, i) => {
    const rand = rng(hash("run" + i) + days);
    const jobType = jobTypes[i % jobTypes.length] as JobType;
    const profile = activeProfiles[i % Math.max(activeProfiles.length, 1)];
    const failed = rand() < 0.18;
    const status: JobRun["status"] = filters.jobStatus === "failed" ? "failed" : filters.jobStatus === "success" ? "success" : failed ? "failed" : "success";
    const started = new Date(Date.now() - i * 41 * 60 * 1000);
    return {
      id: `job_${i + 1}`,
      jobType,
      profileName: profile?.name ?? "—",
      status,
      startedAt: started.toISOString(),
      durationSec: Math.round((5 + rand() * 55) * 10) / 10,
      retries: status === "failed" ? Math.ceil(rand() * 3) : 0,
      message: status === "failed" ? "LinkedIn rate limit encountered" : "Completed without errors",
    };
  }).filter((r) => (filters.jobStatus === "all" ? true : r.status === filters.jobStatus));

  const segTotals = {
    leads,
    connections: totals.connectionsSent,
    accepted: totals.connectionsAccepted,
    messages: totals.dmsSent,
    replies: totals.repliesReceived,
  };

  const positionKeys = filters.position === "all" ? POSITIONS : [filters.position];
  const locationKeys = filters.location === "all" ? LOCATIONS : [filters.location];

  return {
    profiles: PROFILES,
    positions: POSITIONS,
    locations: LOCATIONS,
    kpis: {
      connectionsSent: totals.connectionsSent,
      connectionsAccepted: totals.connectionsAccepted,
      acceptanceRate: pct(totals.connectionsAccepted, totals.connectionsSent),
      messagesSent: totals.dmsSent,
      repliesReceived: totals.repliesReceived,
      replyRate: pct(totals.repliesReceived, totals.dmsSent),
      dailyQuotaUsage: quota.length
        ? Math.round(
            (quota.reduce((a, q) => a + q.connectionsUsed / q.connectionsLimit, 0) / quota.length) * 1000,
          ) / 10
        : 0,
      profileHealth: quota.length
        ? Math.round(
            quota.reduce(
              (a, q) => a + (q.status === "active" ? 92 : q.status === "cooldown" ? 58 : 30),
              0,
            ) / quota.length,
          )
        : 0,
    },
    funnel,
    daily,
    comparison,
    quota,
    scheduler: {
      totalSuccess: byJobType.reduce((a, j) => a + j.success, 0),
      totalFailed: byJobType.reduce((a, j) => a + j.failed, 0),
      avgDurationSec:
        Math.round(
          (byJobType.reduce((a, j) => a + j.avgDurationSec, 0) / Math.max(byJobType.length, 1)) * 10,
        ) / 10,
      totalRetries: byJobType.reduce((a, j) => a + j.retries, 0),
      byJobType,
      recentRuns,
    },
    byPosition: buildSegments(positionKeys, segTotals, 41),
    byLocation: buildSegments(locationKeys, segTotals, 77),
    conversions: {
      leadToConnection: pct(totals.connectionsSent, leads),
      connectionToAcceptance: pct(totals.connectionsAccepted, totals.connectionsSent),
      acceptanceToDm: pct(totals.dmsSent, totals.connectionsAccepted),
      dmToReply: pct(totals.repliesReceived, totals.dmsSent),
      leadToReply: pct(totals.repliesReceived, leads),
    },
  };
}
