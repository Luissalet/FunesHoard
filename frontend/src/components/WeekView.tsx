import { useEffect, useState } from "react";
import { api, formatDuration } from "../api";
import { categoryColor } from "../categoryColors";
import { STRINGS, type Lang } from "../i18n";
import { EmptyState } from "./Common";

export function WeekView({ lang }: { lang: Lang }) {
  const t = STRINGS[lang];
  const [days, setDays] = useState<Awaited<ReturnType<typeof api.week>>["days"]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.week().then((r) => {
      setDays(r.days);
      setLoading(false);
    });
  }, []);

  if (loading) return null;
  const totalActive = days.reduce((acc, d) => acc + d.active_s, 0);
  if (totalActive === 0) return <EmptyState title={t.no_data_title} body={t.no_data_body} />;

  const maxActive = Math.max(1, ...days.map((d) => d.active_s));
  const projectTotals: Record<string, number> = {};
  for (const d of days) {
    for (const [p, v] of Object.entries(d.by_project)) projectTotals[p] = (projectTotals[p] || 0) + v;
  }

  return (
    <div>
      <div className="card">
        <div className="section-title">{t.active_time}</div>
        <div className="row" style={{ alignItems: "flex-end", gap: 18, height: 200, paddingTop: 20 }}>
          {days.map((d) => {
            const cats = Object.entries(d.by_category);
            const total = Math.max(1, d.active_s);
            return (
              <div className="week-col" key={d.date}>
                <div className="week-bar" style={{ height: `${(d.active_s / maxActive) * 150 + 4}px` }}>
                  {cats.map(([cat, secs]) => (
                    <div key={cat} style={{ height: `${(secs / total) * 100}%`, background: categoryColor(cat) }} />
                  ))}
                </div>
                <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
                  {new Date(d.date).toLocaleDateString(lang, { weekday: "short" })}
                </span>
                <span style={{ fontSize: 11 }}>{formatDuration(d.active_s)}</span>
              </div>
            );
          })}
        </div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <div className="section-title">{t.by_project}</div>
        {Object.keys(projectTotals).length === 0 ? (
          <p style={{ color: "var(--text-muted)", fontSize: 13 }}>—</p>
        ) : (
          <table>
            <tbody>
              {Object.entries(projectTotals)
                .sort((a, b) => b[1] - a[1])
                .map(([p, v]) => (
                  <tr key={p}>
                    <td>{p}</td>
                    <td style={{ textAlign: "right" }}>{formatDuration(v)}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
