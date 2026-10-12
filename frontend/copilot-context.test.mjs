// node --test frontend/copilot-context.test.mjs  (Node >= 22.18 strips the types of the .ts import)
import test from "node:test";
import assert from "node:assert/strict";
import {
  buildContext,
  changedFields,
  collectEditors,
  configPatch,
  dayObject,
  installCopilot,
  onRefresh,
  parseRoute,
  provideEditor,
  rebaseForm,
  scheduleRefresh,
  spanObject,
} from "./src/copilot-context.ts";

const summary = {
  active_s: 7260,
  away_s: 600,
  by_project: { Funes: 5000, Faustus: 1800, Docs: 400, Misc: 60 },
  by_app: { Code: 6000, Chrome: 1000, Terminal: 200, Notes: 60 },
  first_activity: 1790000000,
  last_activity: 1790007000,
  context_switches: 14,
  longest_focus_block_s: 2700,
};
const span = { id: 77, start: 1790000100, end: 1790000700, kind: "active", app: "Code", title: "api.py - Funes's Hoard", category: "Coding", project: "Funes", duration_s: 600 };

test("changedFields keeps only what differs and counts cleared fields", () => {
  assert.deepEqual(changedFields({ a: "x", b: 1, c: [1] }, { a: "x", b: 2, c: [] }), { b: 2, c: [] });
  assert.deepEqual(changedFields(null, { a: "" }), { a: "" });
  assert.deepEqual(changedFields({ a: 1 }, { a: 1 }), {});
});

test("rebaseForm lets untouched fields follow the reload and keeps typed ones", () => {
  const out = rebaseForm({ url: "a", model: "m" }, { url: "b", model: "n" }, { url: "a", model: "typed" });
  assert.deepEqual(out, { url: "b", model: "typed" });
});

test("configPatch sends only changed fields; the token only when typed", () => {
  const loaded = { faustus_url: "http://a", llm_url: "", llm_model: "q" };
  assert.deepEqual(configPatch(loaded, { ...loaded }, ""), {});
  assert.deepEqual(configPatch(loaded, { ...loaded, llm_model: " r " }, ""), { capabilities: { llm: { model: "r" } } });
  assert.deepEqual(configPatch(loaded, { ...loaded, llm_model: "" }, ""), { capabilities: { llm: { model: "" } } });
  assert.deepEqual(configPatch(loaded, { ...loaded, faustus_url: "" }, " tok "), { faustus_url: "", faustus_token: "tok" });
});

test("parseRoute reads the hash like the app and falls back to today", () => {
  assert.deepEqual(parseRoute(""), { page: "today" });
  assert.deepEqual(parseRoute("#/rules"), { page: "rules" });
  assert.deepEqual(parseRoute("#/today/2026-09-21@1790000000"), { page: "today", day: "2026-09-21", at: 1790000000 });
  assert.deepEqual(parseRoute("#/today/garbage"), { page: "today", day: undefined, at: undefined });
  assert.deepEqual(parseRoute("#/nope"), { page: "today" });
});

test("dayObject and spanObject carry short previews only", () => {
  const day = dayObject("2026-09-21", summary);
  assert.equal(day.kind, "day");
  assert.equal(day.object_id, "2026-09-21");
  assert.equal(day.active_min, 121);
  assert.deepEqual(day.top_projects, ["Funes", "Faustus", "Docs"]);
  assert.deepEqual(day.top_apps, ["Code", "Chrome", "Terminal"]);
  assert.deepEqual(dayObject("2026-09-21", null), { kind: "day", object_id: "2026-09-21", title: "2026-09-21" });
  const s = spanObject(span);
  assert.equal(s.object_id, 77);
  assert.equal(s.duration_min, 10);
  assert.equal(s.project, "Funes");
  assert.ok(spanObject({ ...span, title: "x".repeat(500) }).title.length <= 120);
});

test("buildContext: today view lists the day and the pinned span, with the dirty form as editor", async () => {
  const asked = [];
  const editors = [
    { kind: "search-form", object_id: null, dirty: false, fields: {} },
    { kind: "classify-rule-form", object_id: null, dirty: true, fields: { pattern: "foo" } },
  ];
  const ctx = await buildContext({ hash: "#/today", today: "2026-09-22", editors, selectedSpan: span, getSummary: async (d) => (asked.push(d), summary) });
  assert.deepEqual(asked, ["2026-09-22"]);
  assert.deepEqual(ctx.active_objects.map((o) => `${o.kind}:${o.object_id}`), ["day:2026-09-22", "span:77"]);
  assert.equal(ctx.view.page, "today");
  assert.equal(ctx.view.object_id, "2026-09-22");
  assert.equal(ctx.view.editor.kind, "classify-rule-form");
  assert.equal(ctx.view.editors.length, 2);
});

test("buildContext never throws: a failing day still yields the day, other pages have no object", async () => {
  const ctx = await buildContext({ hash: "#/today/2026-09-21", today: "2026-09-22", getSummary: async () => { throw new Error("boom"); } });
  assert.deepEqual(ctx.active_objects, [{ kind: "day", object_id: "2026-09-21", title: "2026-09-21" }]);
  const rules = await buildContext({ hash: "#/rules", today: "2026-09-22", selectedSpan: span, getSummary: async () => { throw new Error("unused"); } });
  assert.deepEqual(rules, { active_objects: [], view: { page: "rules" } });
});

test("forms answer the context request and unsubscribe", () => {
  const target = new EventTarget();
  const off = provideEditor(() => ({ kind: "settings-form", object_id: null, dirty: true, fields: { llm_model: "q" } }), target);
  const throwing = provideEditor(() => { throw new Error("broken form"); }, target);
  assert.deepEqual(collectEditors(target).map((e) => e.kind), ["settings-form"]);
  off();
  throwing();
  assert.deepEqual(collectEditors(target), []);
});

test("installCopilot registers once and the turn-end event reloads through one debounce", async () => {
  const target = new EventTarget();
  assert.equal(installCopilot(() => ({}), target), true);
  const first = target.hoardCopilot;
  assert.equal(typeof first.context, "function");
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
