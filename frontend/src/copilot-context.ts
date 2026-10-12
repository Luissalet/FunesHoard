// What the shared assistant launcher asks this app for: the real objects open in the current view, the unsaved form
// fields, and a way to reload the open view in place when the assistant finishes a turn. Nothing here renders anything
// and nothing here imports the app (so node --test can load it); the wiring to the API is in copilot.ts.
// Only ids, titles and short previews leave this module: no window-title history, no tokens, no whole documents.

export interface OpenObject {
  kind: string;
  object_id: string | number;
  title: string;
  [key: string]: unknown;
}

export interface EditorSnapshot {
  kind: string;
  object_id: string | number | null;
  dirty: boolean;
  fields: Record<string, unknown>;
}

export interface SpanLike {
  id: number;
  start: number;
  end: number;
  kind: string;
  app: string;
  title: string;
  category: string;
  project: string | null;
  duration_s: number;
}

export interface SummaryLike {
  active_s: number;
  away_s: number;
  by_project?: Record<string, number>;
  by_app?: Record<string, number>;
  first_activity: number | null;
  last_activity: number | null;
  context_switches: number;
  longest_focus_block_s: number;
}

export const VIEWS = ["today", "timeline", "week", "search", "projects", "files", "rules", "privacy", "assistant", "settings"];
const DAY_RE = /^\d{4}-\d{2}-\d{2}$/;
const MAX_TITLE = 120;

// Fields of `draft` whose value differs from `initial` (compared as JSON, so an emptied field counts).
export function changedFields(initial: Record<string, unknown> | null | undefined, draft: Record<string, unknown> | null | undefined) {
  const base = initial || {};
  return Object.fromEntries(Object.entries(draft || {}).filter(([key, value]) => JSON.stringify(value) !== JSON.stringify(base[key])));
}

// A refreshed baseline arrives while the user is editing: untouched fields follow the new baseline, touched ones stay.
export function rebaseForm<T extends Record<string, unknown>>(previousBase: T | null | undefined, nextBase: T, form: T): T {
  const out: Record<string, unknown> = { ...nextBase };
  for (const key of Object.keys(form)) {
    if (JSON.stringify(form[key]) !== JSON.stringify(previousBase?.[key])) out[key] = form[key];
  }
  return out as T;
}

export interface ConfigForm { faustus_url: string; llm_url: string; llm_model: string }

// The PUT /api/backend/config body for what the user changed since the form was loaded (the backend merges per key and
// "" removes a saved override). The token is sent only when something was typed: blank means "keep the stored one".
export function configPatch(loaded: ConfigForm, current: ConfigForm, token = "") {
  const patch: { faustus_url?: string; faustus_token?: string; capabilities?: { llm: { url?: string; model?: string } } } = {};
  if (current.faustus_url.trim() !== loaded.faustus_url.trim()) patch.faustus_url = current.faustus_url.trim();
  const llm: { url?: string; model?: string } = {};
  if (current.llm_url.trim() !== loaded.llm_url.trim()) llm.url = current.llm_url.trim();
  if (current.llm_model.trim() !== loaded.llm_model.trim()) llm.model = current.llm_model.trim();
  if (Object.keys(llm).length) patch.capabilities = { llm };
  if (token.trim()) patch.faustus_token = token.trim();
  return patch;
}

// "#/today/2026-09-21@1790000000" -> { page: "today", day, at }. Unknown pages fall back to today, like the app does.
export function parseRoute(hash: string | undefined | null): { page: string; day?: string; at?: number } {
  const [view, rest] = String(hash || "").replace(/^#\/?/, "").split("/");
  if (!VIEWS.includes(view)) return { page: "today" };
  if (view !== "today" || !rest) return { page: view };
  const [day, at] = rest.split("@");
  return { page: "today", day: DAY_RE.test(day) ? day : undefined, at: at ? Number(at) || undefined : undefined };
}

const clip = (text: unknown, max = MAX_TITLE) => {
  const s = String(text ?? "").replace(/\s+/g, " ").trim();
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
};
const minutes = (seconds: number) => Math.round(seconds / 60);
const top = (map: Record<string, number> | undefined, n: number) =>
  Object.entries(map || {}).sort((a, b) => b[1] - a[1]).slice(0, n).map(([name]) => name);

function clock(ts: number | null | undefined): string | undefined {
  if (!ts) return undefined;
  const d = new Date(ts * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function spanObject(span: SpanLike): OpenObject {
  const title = clip(span.title ? `${span.app} - ${span.title}` : span.app);
  const out: OpenObject = { kind: "span", object_id: span.id, title, span_kind: span.kind, from: clock(span.start), to: clock(span.end), duration_min: minutes(span.duration_s), category: span.category };
  if (span.project) out.project = span.project;
  return out;
}

export function dayObject(day: string, summary: SummaryLike | null): OpenObject {
  const out: OpenObject = { kind: "day", object_id: day, title: day };
  if (!summary) return out;
  out.active_min = minutes(summary.active_s);
  out.away_min = minutes(summary.away_s);
  out.context_switches = summary.context_switches;
  out.longest_focus_min = minutes(summary.longest_focus_block_s);
  const first = clock(summary.first_activity);
  const last = clock(summary.last_activity);
  if (first) out.first_activity = first;
  if (last) out.last_activity = last;
  const projects = top(summary.by_project, 3);
  const apps = top(summary.by_app, 3);
  if (projects.length) out.top_projects = projects;
  if (apps.length) out.top_apps = apps;
  return out;
}

// ---- forms answer this event with a snapshot of what the user has typed ----------------------------------------------------
export const CONTEXT_REQUEST = "funes:context-request";
const EDITOR_ORDER: Record<string, number> = { "classify-rule-form": 0, "privacy-rule-form": 1, "settings-form": 2, "search-form": 3 };

export function collectEditors(target: Window | EventTarget | undefined = globalThis.window): EditorSnapshot[] {
  const found: EditorSnapshot[] = [];
  if (!target?.dispatchEvent) return found;
  target.dispatchEvent(
    new CustomEvent(CONTEXT_REQUEST, { detail: { provide: (snapshot: EditorSnapshot | null | undefined) => { if (snapshot) found.push(snapshot); } } }),
  );
  return found.sort((a, b) => (EDITOR_ORDER[a.kind] ?? 9) - (EDITOR_ORDER[b.kind] ?? 9));
}

// Registers one form: `get()` returns its current snapshot. Returns the unsubscribe function.
export function provideEditor(get: () => EditorSnapshot | null, target: EventTarget | undefined = globalThis.window): () => void {
  if (!target?.addEventListener) return () => {};
  const handler = (event: Event) => {
    const detail = (event as CustomEvent).detail;
    try {
      const snapshot = get();
      if (snapshot && typeof detail?.provide === "function") detail.provide(snapshot);
    } catch {
      /* a form that cannot describe itself is left out; the context must never throw */
    }
  };
  target.addEventListener(CONTEXT_REQUEST, handler);
  return () => target.removeEventListener(CONTEXT_REQUEST, handler);
}

// ---- the span the user has pinned in the timeline (reported by the Today view) ---------------------------------------------
let selectedSpan: SpanLike | null = null;
export const setSelectedSpan = (span: SpanLike | null) => { selectedSpan = span; };
export const getSelectedSpan = () => selectedSpan;

interface Deps {
  hash?: string;
  today: string;
  editors?: EditorSnapshot[];
  selectedSpan?: SpanLike | null;
  getSummary: (day: string) => Promise<SummaryLike | null | undefined>;
}

async function safe<T>(fn: () => Promise<T>): Promise<T | null> {
  try { return await fn(); } catch { return null; }
}

// Pure core (dependencies injected so it can be tested): never throws; an unreadable day yields the day without numbers.
export async function buildContext({ hash, today, editors = [], selectedSpan: span = null, getSummary }: Deps) {
  const route = parseRoute(hash);
  const active: OpenObject[] = [];
  const view: { page: string; object_id?: string; editor?: EditorSnapshot; editors?: EditorSnapshot[] } = { page: route.page };
  if (route.page === "today") {
    const day = route.day || today;
    const summary = await safe(async () => (await getSummary(day)) ?? null);
    active.push(dayObject(day, summary));
    view.object_id = day;
    if (span && span.id) active.push(spanObject(span));
  }
  const editor = editors.find((e) => e.dirty) || editors[0];
  if (editor) view.editor = editor;
  if (editors.length > 1) view.editors = editors;
  return { active_objects: active, view };
}

// ---- reload what is on screen when the assistant finishes a turn --------------------------------------------------------------
const handlers = new Set<() => void>();
let timer: ReturnType<typeof setTimeout> | null = null;

function run() {
  timer = null;
  for (const fn of [...handlers]) {
    try { fn(); } catch { /* a refresh must never break the page */ }
  }
}

export function scheduleRefresh() {
  if (timer) clearTimeout(timer);
  timer = setTimeout(run, 250);
}

// Every view registers what "reload my data in place" means for it (no remount; the forms keep what the user typed).
export function onRefresh(fn: () => void): () => void {
  handlers.add(fn);
  return () => { handlers.delete(fn); };
}

export const onTurnEnd = () => scheduleRefresh();

// Once at start: the object the launcher looks for, and the window event it dispatches on turn end (the debounce merges the two).
export function installCopilot(context: () => unknown, target: any = globalThis.window): boolean {
  if (!target || target.hoardCopilot) return false;
  target.hoardCopilot = { context, onTurnEnd };
  target.addEventListener("hoard:copilot-turn-end", scheduleRefresh);
  return true;
}
