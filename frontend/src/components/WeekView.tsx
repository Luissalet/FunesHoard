import { useEffect, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { api, localeFor, errorMessage, formatDuration, isoDay, parseIsoDay } from "../api";
import { categoryColor } from "../categoryColors";
import { STRINGS, type Lang } from "../i18n";
import { EmptyState } from "./Common";

export function WeekView({ lang }: { lang: Lang }) {
  const t = STRINGS[lang];
  const [days, setDays] = useState<Awaited<ReturnType<typeof api.week>>["days"]>([]);
  const [loading, setLoading] = useState(true);
  const [anchor, setAnchor] = useState(() => isoDay(new Date()));
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setError(null);
    api
      .week(anchor)
      .then((r) => setDays(r.days))
      .catch((err) => setError(errorMessage(err)))
      .finally(() => setLoading(false));
  }, [anchor]);

  function shift(weeks: number) {
    const d = parseIsoDay(anchor);
    d.setDate(d.getDate() + weeks * 7);
    setAnchor(isoDay(d));
  }

  if (loading) return null;
  const totalActive = days.reduce((acc, d) => acc + d.active_s, 0);
  const first = days[0] ? parseIsoDay(days[0].date) : null;
  const last = days[6] ? parseIsoDay(days[6].date) : null;
  const isCurrentWeek = last !== null && last >= parseIsoDay(isoDay(new Date()));
  const nav = (
    <div className="row" style={{ marginBottom: 16 }}>
      <button className="icon-button" aria-label={t.previous_week} title={t.previous_week} onClick={() => shift(-1)}>
        <ChevronLeft size={16} />
      </button>
      <strong>
        {first?.toLocaleDateString(localeFor(lang), { day: "numeric", month: "long" })} –{" "}
        {last?.toLocaleDateString(localeFor(lang), { day: "numeric", month: "long", year: "numeric" })}
      </strong>
      <button
        className="icon-button"
        aria-label={t.next_week}
        title={t.next_week}
        disabled={isCurrentWeek}
        onClick={() => shift(1)}
      >
        <ChevronRight size={16} />
      </button>
    </div>
  );
  if (error) return <EmptyState title={t.load_error} body={error} />;
  if (totalActive === 0)
    return (
      <div>
        {nav}
        <EmptyState title={t.no_data_title} body={t.no_data_body} />
      </div>
    );

  const maxActive = Math.max(1, ...days.map((d) => d.active_s));
  const projectTotals: Record<string, number> = {};
  for (const d of days) {
    for (const [p, v] of Object.entries(d.by_project || {})) projectTotals[p] = (projectTotals[p] || 0) + v;
  }

  return (
    <div>
      {nav}
      <div className="card">
        <div className="section-title">{t.active_time}</div>
        <div className="row" style={{ alignItems: "flex-end", gap: 18, height: 200, paddingTop: 20 }}>
          {days.map((d) => {
            const cats = Object.entries(d.by_category || {});
            const total = Math.max(1, d.active_s);
            return (
              <div className="week-col" key={d.date}>
                <div className="week-bar" style={{ height: `${(d.active_s / maxActive) * 150 + 4}px` }}>
                  {cats.map(([cat, secs]) => (
                    <div key={cat} style={{ height: `${(secs / total) * 100}%`, background: categoryColor(cat) }} />
                  ))}
                </div>
                <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
                  {parseIsoDay(d.date).toLocaleDateString(localeFor(lang), { weekday: "short", day: "numeric" })}
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
