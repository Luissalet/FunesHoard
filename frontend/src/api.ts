export interface StatusInfo {
  recording: boolean;
  paused: boolean;
  paused_until: number | null;
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
  human_range: string;
  total: number;
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

export interface CommitItem {
  id: number;
  ts: number;
  repo: string;
  sha: string;
  subject: string;
  author: string;
}

export interface Job {
  id: string;
  kind: string;
  status: "queued" | "running" | "done" | "error";
  progress: number;
  total: number;
  error: string | null;
  result: Record<string, unknown> | null;
}

export interface FileEventItem {
  id: number;
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

export interface Resolution {
  capability: string;
  provider: string | null;
  url: string | null;
  model: string | null;
  api: string | null;
  state: "resolved" | "unavailable";
  reason: string;
  details: Record<string, unknown>;
}

export interface BackendStatus {
  capabilities: Record<string, Resolution>;
  only_resident: boolean;
  faustus_url: string | null;
  faustus_token_set: boolean;
  write_my_day_enabled: boolean;
  llm_url_override: string | null;
  llm_model_override: string | null;
}

export interface BackendConfigPatch {
  only_resident?: boolean;
  faustus_url?: string;
  faustus_token?: string;
  capabilities?: Record<string, { url?: string; model?: string }>;
}

export interface DayNarrative {
  day: string;
  text: string | null;
  model: string | null;
  generated_at: number | null;
  enabled: boolean;
  available: boolean;
  reason: string | null;
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
      message = data?.message || data?.detail?.message || message;
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
  timeline: (params: { day?: string; start?: string; end?: string; min_minutes?: number; limit?: number; offset?: number }) =>
    req<TimelineResponse>("GET", `/api/timeline${qs(params)}`),
  summary: (params: { day?: string; start?: string; end?: string }) =>
    req<SummaryResponse>("GET", `/api/summary${qs(params)}`),
  week: (start?: string) => req<{ days: (SummaryResponse & { date: string })[] }>("GET", `/api/week${qs({ start })}`),
  search: (query: string, params: { since?: string; until?: string; limit?: number }) =>
    req<{ query: string; items: SearchItem[]; truncated: boolean }>("GET", `/api/search${qs({ query, ...params })}`),
  commits: (params: { since?: string; limit?: number }) =>
    req<{ items: CommitItem[]; truncated: boolean }>("GET", `/api/commits${qs(params)}`),
  job: (id: string) => req<Job>("GET", `/api/jobs/${id}`),
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
  commitAuthors: () => req<{ authors: string[] }>("GET", "/api/commit-authors"),
  setCommitAuthors: (authors: string[]) => req<{ authors: string[] }>("PUT", "/api/commit-authors", { authors }),

  backendStatus: () => req<BackendStatus>("GET", "/api/backend"),
  saveBackendConfig: (patch: BackendConfigPatch) =>
    req<{ saved: boolean; faustus_token_set: boolean }>("PUT", "/api/backend/config", patch),
  recheckBackend: () => req<{ ok: boolean }>("POST", "/api/backend/recheck"),

  dayNarrative: (day?: string) => req<DayNarrative>("GET", `/api/day-narrative${qs({ day })}`),
  generateDayNarrative: (day?: string, force = false) =>
    req<{ day: string; text: string; model: string | null; generated_at: number; cached: boolean }>(
      "POST",
      "/api/day-narrative",
      { day, force },
    ),
  writeMyDaySetting: () => req<{ enabled: boolean }>("GET", "/api/settings/write-my-day"),
  setWriteMyDaySetting: (enabled: boolean) => req<{ enabled: boolean }>("PUT", "/api/settings/write-my-day", { enabled }),
};

export function formatDuration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

/**
 * Locale for dates and times: the browser's own when it matches the
 * interface language (so an en-GB or es-MX user keeps their clock format),
 * otherwise the European variant of that language.
 */
export function localeFor(lang: string): string {
  try {
    const nav = navigator.language || "";
    if (nav.toLowerCase().startsWith(lang)) return nav;
  } catch {
    /* ignore */
  }
  return lang === "es" ? "es-ES" : "en-GB";
}

export function formatClock(ts: number, lang?: string): string {
  return new Date(ts * 1000).toLocaleTimeString(lang ? localeFor(lang) : [], { hour: "2-digit", minute: "2-digit" });
}

/** "Tuesday, 22 September" in the interface language (first letter capitalised). */
export function formatLongDate(ts: number, lang: string): string {
  const text = new Date(ts * 1000).toLocaleDateString(localeFor(lang), { weekday: "long", day: "numeric", month: "long" });
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** YYYY-MM-DD for a local calendar day (what the API and <input type=date> use). */
export function isoDay(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** Parse YYYY-MM-DD as a *local* date (new Date("2026-09-21") is UTC midnight). */
export function parseIsoDay(day: string): Date {
  const [y, m, d] = day.split("-").map(Number);
  return new Date(y, m - 1, d);
}

export function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}
