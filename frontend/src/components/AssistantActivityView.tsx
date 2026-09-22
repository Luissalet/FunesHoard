import { useEffect, useState } from "react";
import { api, formatClock, type AgentCall } from "../api";
import { STRINGS, type Lang } from "../i18n";
import { EmptyState } from "./Common";

export function AssistantActivityView({ lang }: { lang: Lang }) {
  const t = STRINGS[lang];
  const [calls, setCalls] = useState<AgentCall[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .agentCalls(100)
      .then((r) => setCalls(r.items))
      .catch(() => setCalls([]))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return null;

  return (
    <div>
      <div className="card" style={{ marginBottom: 16 }}>
        <p style={{ fontSize: 13, color: "var(--text-muted)", margin: 0 }}>{t.agent_read_only}</p>
      </div>
      <div className="card">
        {calls.length === 0 ? (
          <EmptyState title={t.no_agent_calls} body={t.no_agent_calls_body} />
        ) : (
          <table>
            <thead>
              <tr>
                <th>{t.time}</th>
                <th>{t.tool}</th>
                <th>{t.args}</th>
                <th>{t.duration}</th>
                <th>{t.result}</th>
              </tr>
            </thead>
            <tbody>
              {calls.map((c) => (
                <tr key={c.id}>
                  <td style={{ width: 70, color: "var(--text-muted)" }}>{formatClock(c.ts, lang)}</td>
                  <td>
                    <code>{c.tool}</code>
                  </td>
                  <td style={{ color: "var(--text-muted)" }}>
                    {c.args_summary}
                    {!c.ok && c.error && <div className="call-error">{c.error}</div>}
                  </td>
                  <td style={{ width: 80, fontVariantNumeric: "tabular-nums" }}>
                    {c.duration_ms < 10 ? c.duration_ms.toFixed(1) : Math.round(c.duration_ms)} ms
                  </td>
                  <td style={{ width: 70 }}>
                    {c.ok ? (
                      <span className="badge" style={{ background: "var(--success)" }}>
                        {t.ok}
                      </span>
                    ) : (
                      <span className="badge" style={{ background: "var(--danger)" }} title={c.error || ""}>
                        {t.error}
                      </span>
                    )}
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
