import { Search } from "lucide-react";
import { useState } from "react";
import { api, formatClock, type SearchItem } from "../api";
import { STRINGS, type Lang } from "../i18n";
import { EmptyState } from "./Common";

export function SearchView({ lang }: { lang: Lang }) {
  const t = STRINGS[lang];
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<SearchItem[] | null>(null);
  const [loading, setLoading] = useState(false);

  async function runSearch() {
    if (!query.trim()) return;
    setLoading(true);
    const res = await api.search(query.trim(), { limit: 40 });
    setItems(res.items);
    setLoading(false);
  }

  const grouped = new Map<string, SearchItem[]>();
  for (const item of items || []) {
    const day = new Date(item.ts * 1000).toLocaleDateString(lang);
    if (!grouped.has(day)) grouped.set(day, []);
    grouped.get(day)!.push(item);
  }

  return (
    <div>
      <div className="row card">
        <Search size={16} color="var(--text-muted)" />
        <input
          type="text"
          placeholder={t.search_placeholder}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && runSearch()}
          style={{ flex: 1 }}
        />
        <button className="btn btn-primary" onClick={runSearch} disabled={loading}>
          {t.search_button}
        </button>
      </div>

      {items !== null && items.length === 0 && <EmptyState title={t.no_data_title} body={t.no_data_body} />}

      {[...grouped.entries()].map(([day, dayItems]) => (
        <div className="card" style={{ marginTop: 16 }} key={day}>
          <div className="section-title">{day}</div>
          <table>
            <tbody>
              {dayItems.map((item, i) => (
                <tr key={i}>
                  <td style={{ width: 70, color: "var(--text-muted)" }}>{formatClock(item.ts)}</td>
                  <td style={{ width: 70 }}>
                    <span className="pill">{item.source}</span>
                  </td>
                  <td dangerouslySetInnerHTML={{ __html: escapeSnippet(item.text) }} />
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  );
}

function escapeSnippet(text: string): string {
  const escaped = text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return escaped.replace(/\[(.+?)\]/g, "<mark>$1</mark>");
}
