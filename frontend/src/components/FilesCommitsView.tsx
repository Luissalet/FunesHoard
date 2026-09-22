import { useEffect, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { api, localeFor, errorMessage, type CommitItem, type CommitRepo, type FileEventItem } from "../api";
import { STRINGS, fmt, type Lang } from "../i18n";
import { EmptyState } from "./Common";

export function FilesCommitsView({ lang }: { lang: Lang }) {
  const t = STRINGS[lang];
  const [files, setFiles] = useState<FileEventItem[]>([]);
  const [commits, setCommits] = useState<CommitItem[]>([]);
  const [repos, setRepos] = useState<CommitRepo[]>([]);
  const [authors, setAuthors] = useState("");
  const [authorsMsg, setAuthorsMsg] = useState<string | null>(null);
  const [newPath, setNewPath] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  function refresh() {
    Promise.all([api.recentFiles({ limit: 50 }), api.commitRepos(), api.commits({ limit: 30 }), api.commitAuthors()])
      .then(([f, r, c, a]) => {
        setFiles(f.items);
        setRepos(r.items);
        setCommits(c.items);
        setAuthors(a.authors.join(", "));
      })
      .catch((err) => setError(errorMessage(err)))
      .finally(() => setLoading(false));
  }

  useEffect(refresh, []);

  if (loading) return null;
  const when = (ts: number) =>
    new Date(ts * 1000).toLocaleString(localeFor(lang), { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

  return (
    <div>
      {error && <p className="form-error">{error}</p>}
      <div className="grid grid-2">
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
                try {
                  await api.addCommitRepo(newPath.trim());
                  setNewPath("");
                  setError(null);
                  refresh();
                  // The scan runs in the background; one follow-up refresh
                  // is enough to pick up "N repos found" for a typical folder.
                  setTimeout(refresh, 1500);
                } catch (err) {
                  setError(errorMessage(err));
                }
              }}
            >
              <Plus size={14} /> {t.add}
            </button>
          </div>
          {repos.length === 0 ? (
            <p className="muted" style={{ fontSize: 13 }}>—</p>
          ) : (
            <table>
              <tbody>
                {repos.map((r) => (
                  <tr key={r.id}>
                    <td>
                      <code>{r.path}</code>
                      <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>
                        {r.last_scan_ts == null
                          ? t.repo_scan_pending
                          : fmt(t.repo_scan_summary, { n: r.last_repo_count ?? 0, when: when(r.last_scan_ts) })}
                      </div>
                    </td>
                    <td style={{ width: 40 }}>
                      <button
                        className="icon-button"
                        aria-label={t.delete}
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

        <div className="card">
          <div className="section-title">{t.commit_authors}</div>
          <div className="row">
            <input type="text" value={authors} onChange={(e) => setAuthors(e.target.value)} style={{ flex: 1 }} />
            <button
              className="btn btn-primary"
              onClick={async () => {
                try {
                  const res = await api.setCommitAuthors(authors.split(",").map((a) => a.trim()).filter(Boolean));
                  setAuthors(res.authors.join(", "));
                  setAuthorsMsg(t.saved);
                } catch (err) {
                  setAuthorsMsg(errorMessage(err));
                }
              }}
            >
              {t.save}
            </button>
          </div>
          <p className="muted" style={{ fontSize: 12, margin: "8px 0 0" }}>
            {authorsMsg ? `${authorsMsg}. ` : ""}
            {t.commit_authors_hint}
          </p>
        </div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <div className="section-title">{t.recent_commits}</div>
        {commits.length === 0 ? (
          <p className="muted" style={{ fontSize: 13 }}>—</p>
        ) : (
          <table>
            <tbody>
              {commits.map((c) => (
                <tr key={c.id}>
                  <td style={{ width: 130 }}>
                    <span className="pill">{c.repo}</span>
                  </td>
                  <td>{c.subject}</td>
                  <td style={{ width: 100 }}>
                    <code>{c.sha.slice(0, 7)}</code>
                  </td>
                  <td className="muted" style={{ width: 150, textAlign: "right" }}>{when(c.ts)}</td>
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
              {files.map((f) => (
                <tr key={f.id}>
                  <td style={{ width: 60 }}>
                    <span className="pill">{f.app_hint || "?"}</span>
                  </td>
                  <td>
                    <code>{f.path}</code>
                  </td>
                  <td className="muted" style={{ width: 150, textAlign: "right" }}>{when(f.ts)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
