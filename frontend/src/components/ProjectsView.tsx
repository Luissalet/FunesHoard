import { useEffect, useState } from "react";
import { api, errorMessage, formatDuration, localeFor, type ProjectItem } from "../api";
import { STRINGS, type Lang } from "../i18n";
import { EmptyState } from "./Common";

const RANGES = [
  { key: "last_7_days", since: "-7d" },
  { key: "last_30_days", since: "-30d" },
  { key: "this_week", since: "this week" },
] as const;

export function ProjectsView({ lang }: { lang: Lang }) {
  const t = STRINGS[lang];
  const [range, setRange] = useState(1);
  const [items, setItems] = useState<ProjectItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setError(null);
    api
      .projects({ since: RANGES[range].since, limit: 30 })
      .then((r) => setItems(r.items))
      .catch((err) => setError(errorMessage(err)))
      .finally(() => setLoading(false));
  }, [range]);

  if (loading) return null;
  const max = Math.max(1, ...items.map((p) => p.time_s));
  const picker = (
    <div className="toggle-tabs" style={{ width: "fit-content", marginBottom: 16 }}>
      {RANGES.map((r, i) => (
        <button key={r.key} className={range === i ? "active" : ""} onClick={() => setRange(i)}>
          {t[r.key]}
        </button>
      ))}
    </div>
  );
  if (error) return <EmptyState title={t.load_error} body={error} />;
  if (items.length === 0)
    return (
      <div>
        {picker}
        <EmptyState title={t.no_data_title} body={t.no_data_body} />
      </div>
    );

  return (
    <div>
      {picker}
      <div className="card">
        <table>
          <thead>
            <tr>
              <th>{t.project}</th>
              <th>{t.time_spent}</th>
              <th />
              <th>{t.last_touched}</th>
              <th style={{ textAlign: "right" }}>{t.commits}</th>
            </tr>
          </thead>
          <tbody>
            {items.map((p) => (
              <tr key={p.project}>
                <td>
                  <strong>{p.project}</strong>
                </td>
                <td style={{ width: 90 }}>{formatDuration(p.time_s)}</td>
                <td style={{ width: "35%" }}>
                  <div className="bar-track" style={{ marginTop: 6 }}>
                    <div className="bar-fill" style={{ width: `${(p.time_s / max) * 100}%`, background: "var(--cat-coding)" }} />
                  </div>
                </td>
                <td className="muted">
                  {new Date(p.last_touched * 1000).toLocaleString(localeFor(lang), {
                    weekday: "short",
                    day: "numeric",
                    month: "short",
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                </td>
                <td style={{ textAlign: "right" }}>{p.commits}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
