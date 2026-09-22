import { useEffect, useMemo, useRef, useState } from "react";
import { FileText, GitCommit, History, Sparkles } from "lucide-react";
import {
  api,
  errorMessage,
  formatClock,
  formatDuration,
  formatLongDate,
  isoDay,
  type DayNarrative,
  type SpanItem,
  type SummaryResponse,
  type TimelineResponse,
  type WhereContext,
} from "../api";
import { categoryColor } from "../categoryColors";
import { categoryLabel, STRINGS, type Lang } from "../i18n";
import { CategoryBarList, BarList, EmptyState } from "./Common";

type Zoom = "active" | "day";

export function TodayView({
  lang,
  day,
  focusTs,
  onNavigate,
}: {
  lang: Lang;
  day: string;
  /** A moment to pin in the timeline (from a search hit or "Where was I?"). */
  focusTs?: number;
  onNavigate: (day: string, at?: number) => void;
}) {
  const t = STRINGS[lang];
  const setDay = (d: string) => onNavigate(d);
  const timelineRef = useRef<HTMLDivElement>(null);
  const [timeline, setTimeline] = useState<TimelineResponse | null>(null);
  const [summary, setSummary] = useState<SummaryResponse | null>(null);
  const [selected, setSelected] = useState<SpanItem | null>(null);
  const [hovered, setHovered] = useState<SpanItem | null>(null);
  const [zoom, setZoom] = useState<Zoom>("active");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setSelected(null);
    Promise.all([api.timeline({ day, min_minutes: 0.5, limit: 100 }), api.summary({ day })])
      .then(([tl, sm]) => {
        if (cancelled) return;
        setTimeline(tl);
        setSummary(sm);
      })
      .catch((err) => !cancelled && setError(errorMessage(err)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [day]);

  const spans = useMemo(() => timeline?.items || [], [timeline]);
  const isToday = day === isoDay(new Date());

  // Pin the span at `focusTs` once the day has loaded (or the closest one).
  useEffect(() => {
    if (!focusTs || !spans.length) return;
    const actives = spans.filter((s) => s.kind === "active");
    const inside = actives.find((s) => s.start <= focusTs && focusTs < s.end);
    const nearest = inside || [...actives].sort((a, b) => Math.abs(a.start - focusTs) - Math.abs(b.start - focusTs))[0];
    if (nearest) {
      setSelected(nearest);
      timelineRef.current?.scrollIntoView?.({ block: "center" });
    }
  }, [focusTs, spans]);

  // Visible window: the whole day, or the active hours rounded out to full hours.
  const [viewStart, viewEnd] = useMemo(() => {
    const dayStart = timeline?.start ?? 0;
    const dayEnd = timeline?.end ?? dayStart + 86400;
    if (zoom === "day" || !summary?.first_activity || !summary.last_activity) return [dayStart, dayEnd];
    const hour = 3600;
    const last = isToday ? Math.max(summary.last_activity, Date.now() / 1000) : summary.last_activity;
    const s = Math.max(dayStart, Math.floor((summary.first_activity - dayStart) / hour) * hour + dayStart);
    const e = Math.min(dayEnd, Math.ceil((last - dayStart) / hour) * hour + dayStart);
    return e - s >= 2 * hour ? [s, e] : [dayStart, dayEnd];
  }, [timeline, summary, zoom, isToday]);
  const viewLen = Math.max(1, viewEnd - viewStart);
  const pct = (ts: number) => ((ts - viewStart) / viewLen) * 100;

  const ticks = useMemo(() => {
    const hours = viewLen / 3600;
    const step = hours <= 8 ? 1 : hours <= 14 ? 2 : 3;
    const out: number[] = [];
    for (let ts = viewStart; ts <= viewEnd + 1; ts += step * 3600) out.push(ts);
    return out;
  }, [viewStart, viewEnd, viewLen]);

  const nowTs = Date.now() / 1000;
  const showNow = isToday && nowTs >= viewStart && nowTs <= viewEnd;
  const legend = summary ? Object.keys(summary.by_category || {}) : [];
  const hasAway = spans.some((s) => s.kind === "away");
  const hasLocked = spans.some((s) => s.kind === "locked");
  const detail = hovered || selected;

  const picker = <DayPicker day={day} onChange={setDay} lang={lang} />;
  const whereCard = <WhereWasICard day={day} isToday={isToday} lang={lang} onShow={onNavigate} />;
  if (loading && !summary) return picker;
  if (error) {
    return (
      <div>
        {picker}
        <EmptyState title={t.load_error} body={error} />
      </div>
    );
  }
  if (!summary || (summary.active_s === 0 && spans.length === 0)) {
    return (
      <div>
        {picker}
        {whereCard}
        <EmptyState title={t.no_data_title} body={t.no_data_body} />
      </div>
    );
  }

  return (
    <div>
      {picker}

      <div className="card" style={{ marginTop: 16 }} ref={timelineRef}>
        <div className="row" style={{ justifyContent: "space-between", marginBottom: 12 }}>
          <div>
            <h2 className="day-heading">{formatLongDate(timeline?.start ?? nowTs, lang)}</h2>
            {summary.first_activity && summary.last_activity && (
              <div className="day-subheading">
                {t.first_activity} {formatClock(summary.first_activity, lang)} · {t.last_activity}{" "}
                {formatClock(summary.last_activity, lang)}
              </div>
            )}
          </div>
          <div className="toggle-tabs">
            <button className={zoom === "active" ? "active" : ""} onClick={() => setZoom("active")}>
              {t.active_hours}
            </button>
            <button className={zoom === "day" ? "active" : ""} onClick={() => setZoom("day")}>
              {t.whole_day}
            </button>
          </div>
        </div>

        <div className="timeline-track" onMouseLeave={() => setHovered(null)}>
          {ticks.slice(1, -1).map((ts) => (
            <div key={`g${ts}`} className="timeline-grid" style={{ left: `${pct(ts)}%` }} />
          ))}
          {spans
            .filter((s) => s.end > viewStart && s.start < viewEnd)
            .map((s) => {
              const left = Math.max(0, pct(s.start));
              const width = Math.max(0.2, Math.min(100, pct(s.end)) - left);
              const active = s.kind === "active";
              // C2: away and locked time are drawn (hatched), so a gap only
              // ever means "not recording". C6: every segment is focusable.
              const who = active ? s.app : s.kind === "away" ? t.away : t.locked;
              const label = `${who}, ${formatClock(s.start, lang)}–${formatClock(s.end, lang)}`;
              return (
                <div
                  key={s.id}
                  className={`timeline-seg ${active ? "" : `seg-${s.kind}`} ${selected?.id === s.id ? "selected" : ""}`}
                  style={{ left: `${left}%`, width: `${width}%`, ...(active ? { background: categoryColor(s.category) } : {}) }}
                  role="button"
                  tabIndex={0}
                  aria-label={label}
                  onMouseEnter={() => setHovered(s)}
                  onFocus={() => setHovered(s)}
                  onBlur={() => setHovered(null)}
                  onClick={() => setSelected(s)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      setSelected(s);
                    }
                  }}
                />
              );
            })}
          {showNow && <div className="timeline-now-marker" style={{ left: `${pct(nowTs)}%` }} title={t.now} />}
        </div>
        <div className="timeline-ruler">
          {ticks.map((ts) => (
            <span key={`t${ts}`} style={{ left: `${pct(ts)}%` }}>
              {ts >= viewStart + 86400 - 1 ? "24:00" : formatClock(ts, lang)}
            </span>
          ))}
        </div>
        <div className="timeline-legend">
          {legend.map((cat) => (
            <span key={cat}>
              <i style={{ background: categoryColor(cat) }} />
              {categoryLabel(cat, lang)}
            </span>
          ))}
          {hasAway && (
            <span>
              <i className="seg-away" />
              {t.away}
            </span>
          )}
          {hasLocked && (
            <span>
              <i className="seg-locked" />
              {t.locked}
            </span>
          )}
        </div>
        <div className="timeline-detail">
          {detail && detail.kind !== "active" ? (
            <>
              <strong>{detail.kind === "away" ? t.away : t.locked}</strong>
              <div className="detail-title muted">{detail.kind === "away" ? t.away_detail : t.locked_detail}</div>
              <div className="muted">
                {formatClock(detail.start, lang)}–{formatClock(detail.end, lang)} · {formatDuration(detail.duration_s)}
              </div>
            </>
          ) : detail ? (
            <>
              <strong>{detail.app}</strong>
              <span className="muted"> · {categoryLabel(detail.category, lang)}{detail.project ? ` · ${detail.project}` : ""}</span>
              <div className="detail-title">{detail.title || "—"}</div>
              <div className="muted">
                {formatClock(detail.start, lang)}–{formatClock(detail.end, lang)} · {formatDuration(detail.duration_s)}
              </div>
            </>
          ) : (
            <span className="muted">{t.segment_hint}</span>
          )}
        </div>
      </div>

      {whereCard}

      <WriteMyDayCard day={day} lang={lang} hasActivity={summary.active_s > 0} />

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
          <CategoryBarList entries={Object.entries(summary.by_category || {})} formatValue={formatDuration} lang={lang} />
        </div>
        <div className="card">
          <div className="section-title">{t.by_app}</div>
          <BarList entries={Object.entries(summary.by_app || {})} formatValue={formatDuration} />
        </div>
        <div className="card">
          <div className="section-title">{t.by_project}</div>
          <BarList entries={Object.entries(summary.by_project || {})} formatValue={formatDuration} />
        </div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <div className="section-title">{t.focus_blocks}</div>
        {summary.focus_blocks.length === 0 ? (
          <p className="muted" style={{ fontSize: 13 }}>—</p>
        ) : (
          <table>
            <tbody>
              {[...summary.focus_blocks]
                .sort((a, b) => a.start - b.start)
                .map((b) => (
                  <tr key={b.start}>
                    <td style={{ width: 160 }}>{b.key}</td>
                    <td className="muted">
                      {formatClock(b.start, lang)}–{formatClock(b.end, lang)}
                    </td>
                    <td style={{ width: 90, textAlign: "right" }}>{formatDuration(b.duration_s)}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function WriteMyDayCard({ day, lang, hasActivity }: { day: string; lang: Lang; hasActivity: boolean }) {
  const t = STRINGS[lang];
  const [narrative, setNarrative] = useState<DayNarrative | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    api
      .dayNarrative(day)
      .then((n) => !cancelled && setNarrative(n))
      .catch((err) => !cancelled && setError(errorMessage(err)));
    return () => {
      cancelled = true;
    };
  }, [day]);

  async function write(force: boolean) {
    setBusy(true);
    setError(null);
    try {
      const res = await api.generateDayNarrative(day, force);
      setNarrative((prev) => (prev ? { ...prev, text: res.text, model: res.model, generated_at: res.generated_at } : prev));
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  if (!narrative || !narrative.enabled) return null;
  // Why the button is off, in the words shown under it and as its tooltip.
  const blocker = !narrative.available ? t.no_llm : !hasActivity ? t.no_activity_to_write : null;

  return (
    <div className="card" style={{ marginTop: 16 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div className="section-title">
          <Sparkles size={14} style={{ marginRight: 6, verticalAlign: -2 }} />
          {t.write_my_day}
        </div>
        <button
          className="btn"
          disabled={busy || blocker !== null}
          title={blocker ?? undefined}
          onClick={() => write(Boolean(narrative.text))}
        >
          {busy ? t.working : narrative.text ? t.regenerate : t.write_my_day_button}
        </button>
      </div>
      {narrative.text ? (
        <>
          <p style={{ margin: "10px 0 4px" }}>{narrative.text}</p>
          <p className="muted" style={{ fontSize: 12, margin: 0 }}>
            {t.written_with} {narrative.model || "?"}
          </p>
        </>
      ) : (
        <>
          <p className="muted" style={{ fontSize: 13, marginBottom: 0 }}>{blocker ?? t.write_my_day_hint}</p>
          {!narrative.available && narrative.reason && (
            // C4: the backend's reason is a technical English sentence; keep
            // it one click away instead of mixed into Spanish copy.
            <details className="muted tech-details">
              <summary>{t.technical_details}</summary>
              {narrative.reason}
            </details>
          )}
        </>
      )}
      {error && <p className="form-error">{error}</p>}
    </div>
  );
}

function WhereWasICard({
  day,
  isToday,
  lang,
  onShow,
}: {
  day: string;
  isToday: boolean;
  lang: Lang;
  onShow: (day: string, at?: number) => void;
}) {
  const t = STRINGS[lang];
  const [contexts, setContexts] = useState<WhereContext[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    // Today: before now (so on Monday morning it shows Friday's last work);
    // any other day: before that day ended.
    api
      .whereWasI({ before: isToday ? undefined : day, contexts: 3 })
      .then((r) => !cancelled && setContexts(r.contexts))
      .catch((err) => !cancelled && setError(errorMessage(err)));
    return () => {
      cancelled = true;
    };
  }, [day, isToday]);

  if (contexts === null && !error) return null;
  const fileName = (path: string) => path.split(/[\\/]/).pop() || path;
  return (
    <div className="card where-card" style={{ marginTop: 16 }}>
      <div className="section-title">
        <History size={14} style={{ marginRight: 6, verticalAlign: -2 }} />
        {t.where_title}
      </div>
      <p className="muted" style={{ fontSize: 12, margin: "0 0 10px" }}>{isToday ? t.where_hint_now : t.where_hint_day}</p>
      {error && <p className="form-error">{error}</p>}
      {contexts && contexts.length === 0 && <p className="muted" style={{ fontSize: 13, margin: 0 }}>{t.where_empty}</p>}
      <div className="where-list">
        {(contexts || []).map((c) => {
          const cDay = isoDay(new Date(c.end * 1000));
          return (
            <div className="where-item" key={`${c.start}-${c.project ?? c.app}`}>
              <div className="row" style={{ justifyContent: "space-between", gap: 8, flexWrap: "nowrap" }}>
                <div style={{ minWidth: 0 }}>
                  <strong>{c.project || c.app}</strong>
                  <span className="muted">
                    {" · "}
                    {cDay !== day ? `${formatLongDate(c.start, lang)}, ` : ""}
                    {formatClock(c.start, lang)}–{formatClock(c.end, lang)} · {formatDuration(c.duration_s)}
                  </span>
                  {(c.recent_titles?.length ? c.recent_titles.slice(0, 2) : [c.title || "—"]).map((title) => (
                    <div className="detail-title where-title" title={title} key={title}>
                      {title}
                    </div>
                  ))}
                </div>
                <button className="btn btn-small" onClick={() => onShow(cDay, c.end - 1)}>
                  {t.where_show}
                </button>
              </div>
              {(c.files.length > 0 || c.commits.length > 0) && (
                <ul className="where-refs">
                  {c.files.slice(0, 3).map((f) => (
                    <li key={f} title={f}>
                      <FileText size={12} /> {fileName(f)}
                    </li>
                  ))}
                  {c.commits.slice(0, 2).map((cm) => (
                    <li key={cm.sha} title={cm.sha}>
                      <GitCommit size={12} /> {cm.subject} <code>{cm.sha.slice(0, 7)}</code>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function DayPicker({ day, onChange, lang }: { day: string; onChange: (d: string) => void; lang: Lang }) {
  const t = STRINGS[lang];
  const today = isoDay(new Date());
  const yesterday = isoDay(new Date(Date.now() - 86400000));
  return (
    <div className="toggle-tabs" style={{ width: "fit-content" }}>
      <button className={day === yesterday ? "active" : ""} onClick={() => onChange(yesterday)}>
        {t.day_picker_yesterday}
      </button>
      <button className={day === today ? "active" : ""} onClick={() => onChange(today)}>
        {t.day_picker_today}
      </button>
      <input
        type="date"
        value={day}
        max={today}
        onChange={(e) => e.target.value && onChange(e.target.value)}
        aria-label={t.human_range}
      />
    </div>
  );
}
