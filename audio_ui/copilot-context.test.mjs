// node --test audio_ui/copilot-context.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import {
  buildContext,
  changedFields,
  collectEditors,
  installCopilot,
  minutesObject,
  onRefresh,
  parseRoute,
  provideEditor,
  rebaseForm,
  scheduleRefresh,
  sessionObject,
} from "./client/src/copilot-context.js";

const session = {
  id: "s-1",
  title: "Reunión con Marta",
  status: "done",
  kind: "meeting",
  language: "es",
  duration_s: 1800,
  started_at: 1790000000,
  tags: ["cliente"],
  notes: "n".repeat(500),
  segments: [{ id: 1, text: "hola, esto es transcripción privada" }, { id: 2, text: "otra línea" }],
};
const minutesState = {
  generating: false,
  minutes: { summary: "Se acordó el presupuesto.\nSe fijó la entrega.", decisions: ["a"], action_items: [{}, {}], open_questions: [] },
};

test("changedFields / rebaseForm", () => {
  assert.deepEqual(changedFields({ title: "a", notes: "" }, { title: "a", notes: "x" }), { notes: "x" });
  assert.deepEqual(rebaseForm({ title: "a", notes: "n" }, { title: "b", notes: "m" }, { title: "a", notes: "mine" }), { title: "b", notes: "mine" });
});

test("parseRoute reads session ids and the seek time", () => {
  assert.deepEqual({ ...parseRoute("#/sesiones/s-1?t=12"), query: undefined }, { page: "sesiones", id: "s-1", query: undefined });
  assert.equal(parseRoute("#/sesiones/s-1?t=12").query.get("t"), "12");
  assert.equal(parseRoute("").page, "grabar");
});

test("sessionObject sends a preview of the notes and a segment count, never the transcript", () => {
  const o = sessionObject(session);
  assert.equal(o.kind, "audio_session");
  assert.equal(o.object_id, "s-1");
  assert.equal(o.segments, 2);
  assert.ok(o.notes_preview.length <= 200);
  assert.ok(!JSON.stringify(o).includes("transcripción privada"));
  assert.deepEqual(o.tags, ["cliente"]);
  assert.equal(o.started_at, new Date(1790000000 * 1000).toISOString());
});

test("minutesObject needs minutes and keeps counts and a short summary", () => {
  assert.equal(minutesObject("s-1", "x", { minutes: null }), null);
  const o = minutesObject("s-1", "Reunión", minutesState);
  assert.equal(o.kind, "minutes");
  assert.equal(o.object_id, "s-1");
  assert.equal(o.action_items, 2);
  assert.equal(o.decisions, 1);
});

test("buildContext: an open finished session yields the session and its minutes plus the dirty form", async () => {
  const editors = [{ kind: "session-form", object_id: "s-1", dirty: true, fields: { notes: "nuevo" } }];
  const ctx = await buildContext({ hash: "#/sesiones/s-1?t=42", editors, getSession: async () => session, getMinutes: async () => minutesState, getRecording: async () => null });
  assert.deepEqual(ctx.active_objects.map((o) => `${o.kind}:${o.object_id}`), ["audio_session:s-1", "minutes:s-1"]);
  assert.deepEqual({ page: ctx.view.page, object_id: ctx.view.object_id, at_s: ctx.view.at_s }, { page: "sessions", object_id: "s-1", at_s: 42 });
  assert.deepEqual(ctx.view.editor, editors[0]);
  assert.equal(ctx.view.editors, undefined);
});

test("buildContext: unfinished sessions skip minutes; unknown ids and errors give no object and never throw", async () => {
  const processing = { ...session, status: "processing" };
  const a = await buildContext({ hash: "#/sesiones/s-1", getSession: async () => processing, getMinutes: async () => { throw new Error("no"); }, getRecording: async () => null });
  assert.deepEqual(a.active_objects.map((o) => o.kind), ["audio_session"]);
  const missing = await buildContext({ hash: "#/sesiones/zzz", getSession: async () => { throw new Error("404"); }, getMinutes: async () => null, getRecording: async () => null });
  assert.deepEqual(missing, { active_objects: [], view: { page: "sessions" } });
  const broken = await buildContext({ hash: "#/sesiones/s-1", getSession: async () => session, getMinutes: async () => { throw new Error("500"); }, getRecording: async () => null });
  assert.deepEqual(broken.active_objects.map((o) => o.kind), ["audio_session"]);
});

test("buildContext: the record page reports the session being recorded", async () => {
  const rec = { session_id: "s-9", title: "Nota", kind: "voice_note", elapsed_s: 12.4 };
  const ctx = await buildContext({ hash: "#/grabar", getSession: async () => null, getMinutes: async () => null, getRecording: async () => rec });
  assert.deepEqual(ctx.active_objects, [{ kind: "audio_session", object_id: "s-9", title: "Nota", status: "recording", elapsed_s: 12, session_kind: "voice_note" }]);
  const idle = await buildContext({ hash: "#/buscar", getSession: async () => null, getMinutes: async () => null, getRecording: async () => rec });
  assert.deepEqual(idle, { active_objects: [], view: { page: "search" } });
});

test("forms answer the context request, sorted, and a broken form is skipped", () => {
  const target = new EventTarget();
  const offs = [
    provideEditor(() => ({ kind: "search-form", object_id: null, dirty: false, fields: {} }), target),
    provideEditor(() => ({ kind: "session-form", object_id: "s-1", dirty: true, fields: {} }), target),
    provideEditor(() => { throw new Error("broken"); }, target),
  ];
  assert.deepEqual(collectEditors(target).map((e) => e.kind), ["session-form", "search-form"]);
  offs.forEach((off) => off());
  assert.deepEqual(collectEditors(target), []);
});

test("installCopilot registers once; the event and onTurnEnd share one debounced reload", async () => {
  const target = new EventTarget();
  assert.equal(installCopilot(() => ({}), target), true);
  const first = target.hoardCopilot;
  assert.equal(installCopilot(() => ({}), target), false);
  assert.equal(target.hoardCopilot, first);
  let calls = 0;
  const off = onRefresh(() => { calls += 1; });
  target.dispatchEvent(new Event("hoard:copilot-turn-end"));
  first.onTurnEnd();
  scheduleRefresh();
  await new Promise((r) => setTimeout(r, 400));
  assert.equal(calls, 1);
  off();
});
