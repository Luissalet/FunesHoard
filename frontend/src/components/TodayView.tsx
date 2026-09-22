import { useEffect, useMemo, useState } from "react";
import { api, formatDuration, type SpanItem, type SummaryResponse, type TimelineResponse } from "../api";
import { categoryColor } from "../categoryColors";
import { STRINGS, type Lang } from "../i18n";
import { CategoryBarList, BarList, EmptyState } from "./Common";

export function TodayView({ lang }: { lang: Lang }) {
  const t = STRINGS[lang];
  const [day, setDay] = useState<string>("today");
  const [timeline, setTimeline] = useState<TimelineResponse | null>(null);
  const [summary, setSummary] = useState<SummaryResponse | null>(null);
  const [selected, setSelected] = useState<SpanItem | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([
      api.timeline({ start: day === "today" ? undefined : day, min_minutes: 0.5, limit: 100 }),
      api.summary({ day }),
    ]).then(([tl, sm]) => {
      if (cancelled) return;
      setTimeline(tl);
      setSummary(sm);
      setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [day]);

  const activeSpans = useMemo(() => timeline?.items || [], [timeline]);
  const dayStart = timeline?.start ?? 0;
  const dayEnd = timeline?.end ?? dayStart + 86400;
  const dayLen = Math.max(1, dayEnd - dayStart);
  const nowFrac = day === "today" ? Math.min(1, (Date.now() / 1000 - dayStart) / dayLen) : null;

  if (loading) return null;
  if (!summary || summary.active_s === 0 && activeSpans.length === 0) {
    return (
      <div>
        <DayPicker day={day} onChange={setDay} lang={lang} />
        <EmptyState title={t.no_data_title} body={t.no_data_body} />
      </div>
    );
  }

  return (
    <div>
      <DayPicker day={day} onChange={setDay} lang={lang} />

      <div className="card" style={{ marginTop: 16 }}>
        <div className="section-title">{t.human_range}: {summary.human_range}</div>
        <div className="timeline-track">
          {activeSpans.map((s) => {
            const left = ((s.start - dayStart) / dayLen) * 100;
            const width = Math.max(0.15, ((s.end - s.start) / dayLen) * 100);
            const color = s.kind === "active" ? categoryColor(s.category) : "transparent";
            return (
              <div
                key={s.id}
                className="timeline-seg"
                style={{ left: `${left}%`, width: `${width}%`, background: color }}
                title={`${s.app} — ${s.title}\n${s.human}`}
                onClick={() => setSelected(s)}
              />
            );
          })}
          {nowFrac !== null && <div className="timeline-now-marker" style={{ left: `${nowFrac * 100}%` }} />}
        </div>
        <div className="timeline-ruler">
          {[0, 6, 12, 18, 24].map((h) => (
            <span key={h}>{h}:00</span>
          ))}
        </div>
        {selected && (
          <p style={{ marginTop: 10, fontSize: 13, color: "var(--text-muted)" }}>
            <strong style={{ color: "var(--text)" }}>{selected.app}</strong> — {selected.title} · {selected.human}
          </p>
        )}
      </div>

      <div className="grid grid-4" style={{ marginTop: 16 }}>
        <div className="card">
          <div className="stat-label">{t.active_time}</div>
          <div className="stat-value">{formatDuration(summary.active_s)}</div>
        </div>
        <div className="card">
          <div className="stat-label">{t.away_time}</div>
          <div className="stat-value">{formatDuration(summary.away_s)}</div>
        </div>
        <div className="card">
          <div className="stat-label">{t.context_switches}</div>
          <div className="stat-value">{summary.context_switches}</div>
        </div>
        <div className="card">
          <div className="stat-label">{t.longest_focus}</div>
          <div className="stat-value">{formatDuration(summary.longest_focus_block_s)}</div>
        </div>
      </div>

      <div className="grid grid-3" style={{ marginTop: 16 }}>
        <div className="card">
          <div className="section-title">{t.by_category}</div>
          <CategoryBarList entries={Object.entries(summary.by_category)} formatValue={formatDuration} />
        </div>
        <div className="card">
          <div className="section-title">{t.by_app}</div>
          <BarList entries={Object.entries(summary.by_app)} formatValue={formatDuration} />
        </div>
        <div className="card">
          <div className="section-title">{t.by_project}</div>
          <BarList entries={Object.entries(summary.by_project)} formatValue={formatDuration} />
        </div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <div className="section-title">{t.focus_blocks}</div>
        {summary.focus_blocks.length === 0 ? (
          <p style={{ color: "var(--text-muted)", fontSize: 13 }}>—</p>
        ) : (
          <table>
            <tbody>
              {summary.focus_blocks.map((b, i) => (
                <tr key={i}>
                  <td style={{ width: 130 }}>{b.key}</td>
                  <td>{b.human}</td>
                  <td style={{ width: 80, textAlign: "right" }}>{formatDuration(b.duration_s)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function DayPicker({ day, onChange, lang }: { day: string; onChange: (d: string) => void; lang: Lang }) {
  const t = STRINGS[lang];
  return (
    <div className="toggle-tabs" style={{ width: "fit-content" }}>
      <button className={day === "yesterday" ? "active" : ""} onClick={() => onChange("yesterday")}>
        {t.day_picker_yesterday}
      </button>
      <button className={day === "today" ? "active" : ""} onClick={() => onChange("today")}>
        {t.day_picker_today}
      </button>
      <input
        type="date"
        onChange={(e) => e.target.value && onChange(e.target.value)}
        style={{ border: "none", background: "transparent" }}
      />
    </div>
  );
}
