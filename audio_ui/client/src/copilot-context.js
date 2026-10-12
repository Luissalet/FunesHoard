// What the shared assistant launcher asks the audio pages for: the real session open in the current view, the unsaved form
// fields, and a way to reload the open page in place when the assistant finishes a turn. Nothing here renders anything and
// nothing here imports the app (so node --test can load it); the wiring to the API is in copilot.js.
// Only ids, titles and short previews leave this module: never the transcript, the audio or whole notes.

const PAGE_NAMES = { grabar: "record", sesiones: "sessions", buscar: "search", ajustes: "settings" };
const EDITOR_ORDER = { "session-form": 0, "recording-form": 1, "search-form": 2, "sessions-filter-form": 3 };
const MAX_PREVIEW = 200;

export const CONTEXT_REQUEST = "funes:context-request";

// Fields of `draft` whose value differs from `initial` (compared as JSON, so an emptied field counts).
export function changedFields(initial, draft) {
  const base = initial || {};
  return Object.fromEntries(Object.entries(draft || {}).filter(([key, value]) => JSON.stringify(value) !== JSON.stringify(base[key])));
}

// A refreshed baseline arrives while the user is editing: untouched fields follow the new baseline, touched ones stay.
export function rebaseForm(previousBase, nextBase, form) {
  const out = { ...nextBase };
  for (const key of Object.keys(form)) {
    if (JSON.stringify(form[key]) !== JSON.stringify(previousBase?.[key])) out[key] = form[key];
  }
  return out;
}

// "#/sesiones/abc?t=12" -> { page: "sesiones", id: "abc", query }. Same reading as the app's own router.
export function parseRoute(hash) {
  const [path, query = ""] = String(hash || "").replace(/^#\/?/, "").split("?");
  const parts = path.split("/").filter(Boolean).map((p) => { try { return decodeURIComponent(p); } catch { return p; } });
  return { page: parts[0] || "grabar", id: parts[1] || null, query: new URLSearchParams(query) };
}

const clip = (text, max = MAX_PREVIEW) => {
  const s = String(text ?? "").replace(/\s+/g, " ").trim();
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
};
const pick = (source, keys) => Object.fromEntries(keys.filter((k) => source?.[k] !== undefined && source[k] !== null && source[k] !== "").map((k) => [k, source[k]]));

export function sessionObject(session) {
  const out = {
    kind: "audio_session",
    object_id: session.id,
    title: clip(session.title || session.id, 120),
    ...pick(session, ["status", "language", "duration_s"]),
    ...(session.kind ? { session_kind: session.kind } : {}),
    ...(session.started_at ? { started_at: new Date(session.started_at * 1000).toISOString() } : {}),
  };
  if (Array.isArray(session.tags) && session.tags.length) out.tags = session.tags.slice(0, 10);
  if (session.notes) out.notes_preview = clip(session.notes);
  if (Array.isArray(session.segments)) out.segments = session.segments.length;
  return out;
}

export function minutesObject(sessionId, title, state) {
  const minutes = state?.minutes;
  if (!minutes) return null;
  return {
    kind: "minutes",
    object_id: sessionId,
    title: clip(`Acta: ${title || sessionId}`, 120),
    summary_preview: clip(minutes.summary),
    decisions: (minutes.decisions || []).length,
    action_items: (minutes.action_items || []).length,
    open_questions: (minutes.open_questions || []).length,
    ...(state.generating ? { generating: true } : {}),
  };
}

export function recordingObject(rec) {
  return { kind: "audio_session", object_id: rec.session_id, title: clip(rec.title || rec.session_id, 120), status: "recording", elapsed_s: Math.round(rec.elapsed_s || 0), ...(rec.kind ? { session_kind: rec.kind } : {}) };
}

// ---- forms answer this event with a snapshot of what the user has typed ----------------------------------------------------
export function collectEditors(target = globalThis.window) {
  const found = [];
  if (!target?.dispatchEvent) return found;
  target.dispatchEvent(new CustomEvent(CONTEXT_REQUEST, { detail: { provide: (snapshot) => { if (snapshot) found.push(snapshot); } } }));
  return found.sort((a, b) => (EDITOR_ORDER[a.kind] ?? 9) - (EDITOR_ORDER[b.kind] ?? 9));
}

// Registers one form: `get()` returns its current snapshot. Returns the unsubscribe function.
export function provideEditor(get, target = globalThis.window) {
  if (!target?.addEventListener) return () => {};
  const handler = (event) => {
    try {
      const snapshot = get();
      if (snapshot && typeof event.detail?.provide === "function") event.detail.provide(snapshot);
    } catch {
      /* a form that cannot describe itself is left out; the context must never throw */
    }
  };
  target.addEventListener(CONTEXT_REQUEST, handler);
  return () => target.removeEventListener(CONTEXT_REQUEST, handler);
}

async function safe(fn) {
  try { return await fn(); } catch { return null; }
}

// Pure core (dependencies injected so it can be tested): never throws; unknown ids yield no object.
export async function buildContext({ hash, editors = [], getSession, getMinutes, getRecording }) {
  const route = parseRoute(hash);
  const page = PAGE_NAMES[route.page] || route.page || "record";
  const active = [];
  const view = { page };

  if (route.page === "sesiones" && route.id) {
    const session = await safe(() => getSession(route.id));
    if (session?.id) {
      view.object_id = session.id;
      const at = Number(route.query.get("t"));
      if (Number.isFinite(at) && at > 0) view.at_s = at;
      active.push(sessionObject(session));
      if (session.status === "done") {
        const minutes = minutesObject(session.id, session.title, await safe(() => getMinutes(session.id)));
        if (minutes) active.push(minutes);
      }
    }
  } else if (route.page === "grabar") {
    const rec = await safe(() => getRecording());
    if (rec?.session_id) {
      view.object_id = rec.session_id;
      active.push(recordingObject(rec));
    }
  }

  const editor = editors.find((e) => e.dirty) || editors[0];
  if (editor) view.editor = editor;
  if (editors.length > 1) view.editors = editors;
  return { active_objects: active, view };
}

// ---- reload what is on screen when the assistant finishes a turn --------------------------------------------------------------
const handlers = new Set();
let timer = null;

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

// Every page registers what "reload my data in place" means for it (no remount; the forms keep what the user typed).
export function onRefresh(fn) {
  handlers.add(fn);
  return () => { handlers.delete(fn); };
}

export const onTurnEnd = () => scheduleRefresh();

// Once at start: the object the launcher looks for, and the window event it dispatches on turn end (the debounce merges the two).
export function installCopilot(context, target = globalThis.window) {
  if (!target || target.hoardCopilot) return false;
  target.hoardCopilot = { context, onTurnEnd };
  target.addEventListener("hoard:copilot-turn-end", scheduleRefresh);
  return true;
}
