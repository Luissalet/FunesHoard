import { useEffect, useState } from "react";
import { ArrowDown, ArrowUp, Plus, Trash2 } from "lucide-react";
import { api, type ClassifyRule } from "../api";
import { STRINGS, type Lang } from "../i18n";

const emptyDraft = { match_type: "app", pattern: "", category: "Coding", project: "" };

export function RulesView({ lang }: { lang: Lang }) {
  const t = STRINGS[lang];
  const [rules, setRules] = useState<ClassifyRule[]>([]);
  const [categories, setCategories] = useState<string[]>([]);
  const [draft, setDraft] = useState(emptyDraft);
  const [preview, setPreview] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);

  function refresh() {
    api.classifyRules().then((r) => {
      setRules(r.items);
      setCategories(r.categories);
    });
  }
  useEffect(refresh, []);

  async function runPreview() {
    if (!draft.pattern.trim()) return;
    const res = await api.previewClassifyRule({ ...draft, project: draft.project || null });
    setPreview(res.would_change);
  }

  async function addRule() {
    if (!draft.pattern.trim()) return;
    await api.addClassifyRule({ ...draft, project: draft.project || null });
    setDraft(emptyDraft);
    setPreview(null);
    refresh();
  }

  async function move(index: number, dir: -1 | 1) {
    const order = rules.map((r) => r.id);
    const target = index + dir;
    if (target < 0 || target >= order.length) return;
    [order[index], order[target]] = [order[target], order[index]];
    await api.reorderClassifyRules(order);
    refresh();
  }

  return (
    <div>
      <div className="card">
        <div className="section-title">{t.add_rule}</div>
        <div className="row">
          <select value={draft.match_type} onChange={(e) => setDraft({ ...draft, match_type: e.target.value })}>
            <option value="app">app</option>
            <option value="title_regex">title_regex</option>
            <option value="domain">domain</option>
          </select>
          <input
            type="text"
            placeholder={t.pattern}
            value={draft.pattern}
            onChange={(e) => setDraft({ ...draft, pattern: e.target.value })}
          />
          <select value={draft.category} onChange={(e) => setDraft({ ...draft, category: e.target.value })}>
            {categories.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
          <input
            type="text"
            placeholder={t.project}
            value={draft.project}
            onChange={(e) => setDraft({ ...draft, project: e.target.value })}
            style={{ width: 110 }}
          />
          <button className="btn" onClick={runPreview}>
            {t.preview}
          </button>
          <button className="btn btn-primary" onClick={addRule}>
            <Plus size={14} /> {t.add_rule}
          </button>
        </div>
        {preview !== null && (
          <p style={{ fontSize: 13, color: "var(--text-muted)", marginTop: 8 }}>
            {t.would_change}: <strong style={{ color: "var(--text)" }}>{preview}</strong> {t.spans}
          </p>
        )}
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <div className="row" style={{ justifyContent: "space-between" }}>
          <div className="section-title" style={{ marginBottom: 0 }}>
            {t.classify_rules}
          </div>
          <button
            className="btn"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              await api.reapplyClassifyRules();
              setBusy(false);
            }}
          >
            {t.reapply}
          </button>
        </div>
        <table style={{ marginTop: 10 }}>
          <thead>
            <tr>
              <th />
              <th>{t.match_type}</th>
              <th>{t.pattern}</th>
              <th>{t.category}</th>
              <th>{t.project}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rules.map((r, i) => (
              <tr key={r.id}>
                <td style={{ width: 50 }}>
                  <button className="icon-button" onClick={() => move(i, -1)}>
                    <ArrowUp size={12} />
                  </button>
                  <button className="icon-button" onClick={() => move(i, 1)}>
                    <ArrowDown size={12} />
                  </button>
                </td>
                <td>{r.match_type}</td>
                <td>
                  <code>{r.pattern}</code>
                </td>
                <td>{r.category}</td>
                <td>{r.project || "—"}</td>
                <td style={{ width: 40 }}>
                  <button
                    className="icon-button"
                    onClick={async () => {
                      await api.deleteClassifyRule(r.id);
                      refresh();
                    }}
                  >
                    <Trash2 size={14} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
