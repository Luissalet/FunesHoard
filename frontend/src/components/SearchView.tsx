import { FileText, GitCommit, Monitor, Search } from "lucide-react";
import { useState } from "react";
import { api, errorMessage, formatClock, formatDuration, formatLongDate, isoDay, type SearchItem } from "../api";
import { fmt, STRINGS, type Lang } from "../i18n";
import { EmptyState } from "./Common";

const RANGES = [
  { key: "any_time", since: undefined },
  { key: "day_picker_today", since: "today" },
  { key: "this_week", since: "this week" },
  { key: "last_30_days", since: "-30d" },
] as const;

const SOURCE_ICON = { span: Monitor, file: FileText, commit: GitCommit } as const;

export function SearchView({ lang, onOpenMoment }: { lang: Lang; onOpenMoment: (day: string, at: number) => void }) {
  const t = STRINGS[lang];
  const [query, setQuery] = useState("");
  const [range, setRange] = useState(0);
  const [items, setItems] = useState<SearchItem[] | null>(null);
  const [truncated, setTruncated] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function runSearch(rangeIndex = range) {
    if (!query.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const res = await api.search(query.trim(), { limit: 60, since: RANGES[rangeIndex].since });
      setItems(res.items);
      setTruncated(res.truncated);
    } catch (err) {
      setError(errorMessage(err));
      setItems(null);
    } finally {
      setLoading(false);
    }
  }

  const grouped = new Map<string, SearchItem[]>();
  for (const item of items || []) {
    const d = new Date(item.ts * 1000);
    const key = `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
    if (!grouped.has(key)) grouped.set(key, []);
    grouped.get(key)!.push(item);
  }

  return (
    <div>
      <div className="card search-bar">
        <Search size={16} color="var(--text-muted)" />
        <input
          type="text"
          placeholder={t.search_placeholder}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && runSearch()}
          style={{ flex: 1 }}
          autoFocus
        />
        <select
          value={range}
          onChange={(e) => {
            const next = Number(e.target.value);
            setRange(next);
            if (items !== null) runSearch(next);
          }}
        >
          {RANGES.map((r, i) => (
            <option key={r.key} value={i}>
              {t[r.key]}
            </option>
          ))}
        </select>
        <button className="btn btn-primary" onClick={() => runSearch()} disabled={loading}>
          {t.search_button}
        </button>
      </div>

      {error && <p className="form-error">{error}</p>}
      {items !== null && items.length === 0 && <EmptyState title={t.no_data_title} body={t.no_data_body} />}
      {items !== null && items.length > 0 && (
        <p className="muted" style={{ fontSize: 12, margin: "12px 2px 0" }}>
          {fmt(t.results_count, { n: truncated ? `${items.length}+` : items.length })} · {t.search_click_hint}
        </p>
      )}

      {[...grouped.entries()].map(([key, dayItems]) => (
        <div className="card" style={{ marginTop: 12 }} key={key}>
          <div className="section-title">{formatLongDate(dayItems[0].ts, lang)}</div>
          <table>
            <tbody>
              {dayItems.map((item) => {
                const Icon = SOURCE_ICON[item.source as keyof typeof SOURCE_ICON] || Monitor;
                // A8: a hit opens that moment in its day's timeline.
                const open = () => onOpenMoment(isoDay(new Date(item.ts * 1000)), item.ts);
                return (
                  <tr
                    key={`${item.source}-${item.ref_id}`}
                    className="clickable"
                    tabIndex={0}
                    title={t.open_in_day}
                    onClick={open}
                    onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), open())}
                  >
                    <td className="muted" style={{ width: 64 }}>{formatClock(item.ts, lang)}</td>
                    <td style={{ width: 28 }} title={item.source}>
                      <Icon size={14} color="var(--text-muted)" />
                    </td>
                    <td className="snippet">{renderSnippet(item.text)}</td>
                    <td className="muted" style={{ width: 70, textAlign: "right" }}>
                      {item.duration_s !== undefined ? formatDuration(item.duration_s) : ""}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  );
}

/** The API wraps matches in \u0002...\u0003; render them as <mark> without innerHTML. */
function renderSnippet(text: string) {
  return text.split(/(\u0002[^\u0003]*\u0003)/).map((part, i) =>
    part.startsWith("\u0002") ? <mark key={i}>{part.slice(1, -1)}</mark> : <span key={i}>{part}</span>,
  );
}
