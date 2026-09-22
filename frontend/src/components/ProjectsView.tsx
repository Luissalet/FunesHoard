import { useEffect, useState } from "react";
import { api, formatDuration, type ProjectItem } from "../api";
import { STRINGS, type Lang } from "../i18n";
import { EmptyState } from "./Common";

export function ProjectsView({ lang }: { lang: Lang }) {
  const t = STRINGS[lang];
  const [items, setItems] = useState<ProjectItem[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.projects({ limit: 30 }).then((r) => {
      setItems(r.items);
      setLoading(false);
    });
  }, []);

  if (loading) return null;
  if (items.length === 0) return <EmptyState title={t.no_data_title} body={t.no_data_body} />;

  return (
    <div className="card">
      <table>
        <thead>
          <tr>
            <th>{t.project}</th>
            <th>{t.time_spent}</th>
            <th>{t.last_touched}</th>
            <th>{t.commits}</th>
          </tr>
        </thead>
        <tbody>
          {items.map((p) => (
            <tr key={p.project}>
              <td>{p.project}</td>
              <td>{formatDuration(p.time_s)}</td>
              <td>{new Date(p.last_touched * 1000).toLocaleString(lang)}</td>
              <td>{p.commits}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
