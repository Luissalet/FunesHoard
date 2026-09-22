import { useEffect, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { api, type CommitRepo, type FileEventItem } from "../api";
import { STRINGS, type Lang } from "../i18n";
import { EmptyState } from "./Common";

export function FilesCommitsView({ lang }: { lang: Lang }) {
  const t = STRINGS[lang];
  const [files, setFiles] = useState<FileEventItem[]>([]);
  const [repos, setRepos] = useState<CommitRepo[]>([]);
  const [newPath, setNewPath] = useState("");
  const [loading, setLoading] = useState(true);

  function refresh() {
    Promise.all([api.recentFiles({ limit: 50 }), api.commitRepos()]).then(([f, r]) => {
      setFiles(f.items);
      setRepos(r.items);
      setLoading(false);
    });
  }

  useEffect(refresh, []);

  if (loading) return null;

  return (
    <div>
      <div className="card">
        <div className="section-title">{t.commit_repos}</div>
        <div className="row" style={{ marginBottom: 12 }}>
          <input
            type="text"
            placeholder={t.add_repo_path}
            value={newPath}
            onChange={(e) => setNewPath(e.target.value)}
            style={{ flex: 1 }}
          />
          <button
            className="btn btn-primary"
            onClick={async () => {
              if (!newPath.trim()) return;
              await api.addCommitRepo(newPath.trim());
              setNewPath("");
              refresh();
            }}
          >
            <Plus size={14} /> {t.add}
          </button>
        </div>
        {repos.length === 0 ? (
          <p style={{ color: "var(--text-muted)", fontSize: 13 }}>—</p>
        ) : (
          <table>
            <tbody>
              {repos.map((r) => (
                <tr key={r.id}>
                  <td>
                    <code>{r.path}</code>
                  </td>
                  <td style={{ width: 40 }}>
                    <button
                      className="icon-button"
                      onClick={async () => {
                        await api.deleteCommitRepo(r.id);
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
        )}
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <div className="section-title">{t.recent_files}</div>
        {files.length === 0 ? (
          <EmptyState title={t.no_data_title} body={t.no_data_body} />
        ) : (
          <table>
            <tbody>
              {files.map((f, i) => (
                <tr key={i}>
                  <td style={{ width: 60 }}>
                    <span className="pill">{f.app_hint || "?"}</span>
                  </td>
                  <td>
                    <code>{f.path}</code>
                  </td>
                  <td style={{ width: 140, color: "var(--text-muted)", textAlign: "right" }}>
                    {new Date(f.ts * 1000).toLocaleString(lang)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
