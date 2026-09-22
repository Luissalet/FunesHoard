export interface StatusInfo {
  recording: boolean;
  paused: boolean;
  idle_s: number;
  now: number;
  app: string | null;
  title: string | null;
  category: string | null;
  project: string | null;
  kind: string | null;
  since: number | null;
  fts_available: boolean;
  rules_version: number;
  probe_status: string;
  retention_days: number;
}

export interface SpanItem {
  id: number;
  start: number;
  end: number;
  kind: string;
  app: string;
  title: string;
  category: string;
  project: string | null;
  duration_s: number;
  human: string;
}

export interface TimelineResponse {
  start: number;
  end: number;
  items: SpanItem[];
  truncated: boolean;
  has_more: boolean;
}

export interface FocusBlock {
  key: string;
  start: number;
  end: number;
  duration_s: number;
  human: string;
}

export interface SummaryResponse {
  start: number;
  end: number;
  human_range: string;
  active_s: number;
  away_s: number;
  by_category: Record<string, number>;
  by_app: Record<string, number>;
  by_project: Record<string, number>;
  first_activity: number | null;
  last_activity: number | null;
  context_switches: number;
  focus_blocks: FocusBlock[];
  longest_focus_block_s: number;
}

export interface SearchItem {
  source: string;
  ref_id: number;
  ts: number;
  text: string;
}

export interface ProjectItem {
  project: string;
  time_s: number;
  last_touched: number;
  commits: number;
}

export interface FileEventItem {
  path: string;
  ts: number;
  app_hint: string | null;
}

export interface ClassifyRule {
  id: number;
  order_idx: number;
  match_type: string;
  pattern: string;
  category: string;
  project: string | null;
  enabled: number;
}

export interface PrivacyRule {
  id: number;
  kind: string;
  match_type: string;
  pattern: string;
  enabled: number;
}

export interface AgentCall {
  id: number;
  ts: number;
  tool: string;
  args_summary: string;
  duration_ms: number;
  ok: number;
  error: string | null;
}

export interface CommitRepo {
  id: number;
  path: string;
  enabled: number;
  last_scan_ts: number | null;
}

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let message = res.statusText;
    try {
      const data = await res.json();
      message = data?.detail?.message || data?.message || message;
    } catch {
      /* ignore */
    }
    throw new Error(message);
  }
  return res.json() as Promise<T>;
}

function qs(params: Record<string, string | number | undefined>): string {
  const parts: string[] = [];
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== "") parts.push(`${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`);
  }
  return parts.length ? `?${parts.join("&")}` : "";
}

export const api = {
  status: () => req<StatusInfo>("GET", "/api/status"),
  timeline: (params: { start?: string; end?: string; min_minutes?: number; limit?: number }) =>
    req<TimelineResponse>("GET", `/api/timeline${qs(params)}`),
  summary: (params: { day?: string; start?: string; end?: string }) =>
    req<SummaryResponse>("GET", `/api/summary${qs(params)}`),
  week: (start?: string) => req<{ days: (SummaryResponse & { date: string })[] }>("GET", `/api/week${qs({ start })}`),
  search: (query: string, params: { since?: string; until?: string; limit?: number }) =>
    req<{ query: string; items: SearchItem[]; truncated: boolean }>("GET", `/api/search${qs({ query, ...params })}`),
  recentFiles: (params: { since?: string; limit?: number }) =>
    req<{ items: FileEventItem[]; truncated: boolean }>("GET", `/api/recent-files${qs(params)}`),
  projects: (params: { since?: string; limit?: number }) =>
    req<{ items: ProjectItem[]; truncated: boolean }>("GET", `/api/projects${qs(params)}`),
  whereWasI: (params: { before?: string; contexts?: number }) =>
    req<{ before: number; contexts: unknown[] }>("GET", `/api/where-was-i${qs(params)}`),
  agentCalls: (limit = 50) => req<{ items: AgentCall[] }>("GET", `/api/agent-calls${qs({ limit })}`),

  classifyRules: () => req<{ items: ClassifyRule[]; categories: string[] }>("GET", "/api/classify/rules"),
  addClassifyRule: (rule: Partial<ClassifyRule>) => req("POST", "/api/classify/rules", rule),
  updateClassifyRule: (id: number, rule: Partial<ClassifyRule>) => req("PUT", `/api/classify/rules/${id}`, rule),
  deleteClassifyRule: (id: number) => req("DELETE", `/api/classify/rules/${id}`),
  reorderClassifyRules: (order: number[]) => req("POST", "/api/classify/reorder", { order }),
  previewClassifyRule: (rule: Partial<ClassifyRule>) =>
    req<{ would_change: number; sample_size: number }>("POST", "/api/classify/preview", rule),
  reapplyClassifyRules: () => req<{ job_id: string }>("POST", "/api/classify/reapply"),

  privacyRules: () => req<{ items: PrivacyRule[] }>("GET", "/api/privacy/rules"),
  addPrivacyRule: (rule: { kind: string; match_type: string; pattern: string; enabled?: boolean }) =>
    req("POST", "/api/privacy/rules", rule),
  deletePrivacyRule: (id: number) => req("DELETE", `/api/privacy/rules/${id}`),
  pause: (minutes: number) => req<{ paused: boolean; until: number }>("POST", "/api/privacy/pause", { minutes }),
  pauseUntilResumed: () => req<{ paused: boolean }>("POST", "/api/privacy/pause-until-resumed"),
  resume: () => req<{ paused: boolean }>("POST", "/api/privacy/resume"),
  getRetention: () => req<{ days: number }>("GET", "/api/privacy/retention"),
  setRetention: (days: number) => req<{ days: number }>("PUT", "/api/privacy/retention", { days }),
  deleteRange: (start: number, end: number) =>
    req<{ deleted: Record<string, number> }>("POST", "/api/privacy/delete-range", { start, end }),
  exportUrl: (start?: number, end?: number) => `/api/privacy/export${qs({ start, end })}`,

  commitRepos: () => req<{ items: CommitRepo[] }>("GET", "/api/commit-repos"),
  addCommitRepo: (path: string) => req("POST", "/api/commit-repos", { path, enabled: true }),
  deleteCommitRepo: (id: number) => req("DELETE", `/api/commit-repos/${id}`),
};

export function formatDuration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

export function formatClock(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}
