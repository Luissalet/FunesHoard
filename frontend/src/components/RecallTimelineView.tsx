import { RefreshCw, Search as SearchIcon } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api, errorMessage, type RecallItem, type RecallResponse, type SourceHealthItem, type SourceItem } from "../api";
import { STRINGS, type Lang } from "../i18n";
import { Badge, EmptyState } from "./Common";

type Strings = (typeof STRINGS)[Lang];

const SOURCE_COLOR: Record<string, string> = {
  funes: "var(--cat-coding)",
  argus: "var(--cat-design)",
  echo: "var(--cat-writing)",
  scribe: "var(--cat-communication)",
};

function sourceColor(source: string): string {
  return SOURCE_COLOR[source] || "var(--cat-other)";
}

function sourceLabel(t: Strings, source: string): string {
  const key = `recall_source_${source}` as keyof typeof STRINGS["en"];
  return (t[key] as string) || source;
}

function itemTime(item: RecallItem, lang: Lang): string {
  if (!item.time) return "";
  try {
    return new Date(item.time).toLocaleString(lang === "es" ? "es-ES" : "en-GB", {
      hour: "2-digit",
      minute: "2-digit",
      day: "2-digit",
      month: "2-digit",
    });
  } catch {
    return item.time;
  }
}

function RecallItemsList({ result, lang, t }: { result: RecallResponse | null; lang: Lang; t: Strings }) {
  if (!result) return null;
  if (result.items.length === 0) return <EmptyState title={t.recall_no_items} body="" />;
  return (
    <div className="card" style={{ padding: 0 }}>
      {result.items.map((item, i) => (
        <div
          key={`${item.source}-${item.citation}-${i}`}
          style={{
            display: "flex",
            gap: 12,
            alignItems: "flex-start",
            padding: "10px 16px",
            borderBottom: i < result.items.length - 1 ? "1px solid var(--border)" : "none",
          }}
        >
          <Badge text={sourceLabel(t, item.source)} color={sourceColor(item.source)} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 13 }}>{item.text}</div>
            <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
              {itemTime(item, lang)} · <code>{item.citation}</code>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

function SourcesCard({ lang, t, onChanged }: { lang: Lang; t: Strings; onChanged: () => void }) {
  const [items, setItems] = useState<SourceItem[]>([]);
  const [health, setHealth] = useState<Record<string, SourceHealthItem>>({});
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState<string | null>(null);

  const refresh = useCallback(() => {
    api.sources().then((r) => setItems(r.items));
    api.sourcesHealth().then((r) => {
      const byId: Record<string, SourceHealthItem> = {};
      for (const s of r.sources) byId[s.id] = s;
      setHealth(byId);
    });
  }, []);

  useEffect(refresh, [refresh]);

  const save = (id: string, patch: Partial<Pick<SourceItem, "base_url" | "enabled">>) => {
    setSaving(id);
    api
      .updateSource(id, patch)
      .then(() => {
        refresh();
        onChanged();
      })
      .finally(() => setSaving(null));
  };

  return (
    <div className="card">
      <h3 style={{ marginTop: 0 }}>{t.sources_title}</h3>
      <p style={{ fontSize: 13, color: "var(--text-muted)", marginTop: -4 }}>{t.sources_subtitle}</p>
      <table>
        <thead>
          <tr>
            <th />
            <th>{t.sources_base_url}</th>
            <th style={{ textAlign: "center" }}>{t.sources_enabled}</th>
            <th style={{ textAlign: "center" }}>{lang === "es" ? "Estado" : "Status"}</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {items.map((s) => {
            const h = health[s.id];
            const value = edits[s.id] ?? s.base_url;
            return (
              <tr key={s.id}>
                <td>
                  <Badge text={sourceLabel(t, s.id)} color={sourceColor(s.id)} />
                </td>
                <td>
                  <input
                    type="text"
                    value={value}
                    onChange={(e) => setEdits((prev) => ({ ...prev, [s.id]: e.target.value }))}
                    style={{ width: "100%", maxWidth: 260 }}
                  />
                </td>
                <td style={{ textAlign: "center" }}>
                  <input type="checkbox" checked={s.enabled} onChange={(e) => save(s.id, { enabled: e.target.checked })} />
                </td>
                <td style={{ textAlign: "center" }}>
                  {h ? (
                    <span className={`pill ${h.ok ? "recording" : "paused"}`}>
                      <span className="dot" />
                      {h.ok ? t.sources_status_ok : `${t.sources_status_down}${h.reason ? ` (${h.reason})` : ""}`}
                    </span>
                  ) : (
                    "…"
                  )}
                </td>
                <td>
                  <button
                    className="btn btn-primary"
                    disabled={saving === s.id || value === s.base_url}
                    onClick={() => save(s.id, { base_url: value })}
                  >
                    {t.sources_save}
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function RecallTimelineView({ lang }: { lang: Lang }) {
  const t = STRINGS[lang];
  const [at, setAt] = useState("");
  const [windowMinutes, setWindowMinutes] = useState(15);
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<RecallResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const runRecall = useCallback(() => {
    setLoading(true);
    setError(null);
    api
      .recall({ at: at || undefined, window: windowMinutes })
      .then(setResult)
      .catch((err) => setError(errorMessage(err)))
      .finally(() => setLoading(false));
  }, [at, windowMinutes]);

  useEffect(runRecall, [runRecall]);

  const runSearch = () => {
    if (!query.trim()) return runRecall();
    setLoading(true);
    setError(null);
    api
      .recallSearch(query.trim(), {})
      .then(setResult)
      .catch((err) => setError(errorMessage(err)))
      .finally(() => setLoading(false));
  };

  return (
    <div>
      <div className="card" style={{ marginBottom: 16 }}>
        <h3 style={{ marginTop: 0 }}>{t.recall_title}</h3>
        <p style={{ fontSize: 13, color: "var(--text-muted)", marginTop: -4 }}>{t.recall_subtitle}</p>
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center", marginBottom: 10 }}>
          <label style={{ fontSize: 13, color: "var(--text-muted)" }}>
            {t.recall_at}{" "}
            <input type="text" placeholder="16:00 / ayer / -2h" value={at} onChange={(e) => setAt(e.target.value)} style={{ width: 140 }} />
          </label>
          <button className="btn btn-ghost" onClick={() => setAt("")}>
            {t.recall_now}
          </button>
          <label style={{ fontSize: 13, color: "var(--text-muted)" }}>
            {t.recall_window}{" "}
            <select value={windowMinutes} onChange={(e) => setWindowMinutes(Number(e.target.value))}>
              {[15, 30, 60, 120].map((m) => (
                <option key={m} value={m}>
                  ±{m}m
                </option>
              ))}
            </select>
          </label>
          <button className="btn btn-ghost" onClick={runRecall} title={lang === "es" ? "Actualizar" : "Refresh"}>
            <RefreshCw size={14} />
          </button>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <input
            type="text"
            placeholder={t.recall_search_placeholder}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && runSearch()}
            style={{ flex: 1 }}
          />
          <button className="btn btn-primary" onClick={runSearch}>
            <SearchIcon size={14} /> {t.recall_search_button}
          </button>
        </div>
        {result?.summary?.unavailable && result.summary.unavailable.length > 0 && (
          <div style={{ marginTop: 10, display: "flex", gap: 6, flexWrap: "wrap" }}>
            {result.summary.unavailable.map((u) => (
              <span key={u.id} className="pill paused">
                <span className="dot" />
                {sourceLabel(t, u.id)}: {t.recall_unavailable} ({u.reason})
              </span>
            ))}
          </div>
        )}
      </div>

      {error && <EmptyState title={t.load_error} body={error} />}
      {!error && loading && !result && null}
      {!error && <RecallItemsList result={result} lang={lang} t={t} />}

      <div style={{ marginTop: 16 }}>
        <SourcesCard lang={lang} t={t} onChanged={runRecall} />
      </div>
    </div>
  );
}
