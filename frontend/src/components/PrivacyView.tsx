import { useEffect, useState } from "react";
import { Download, Plus, Trash2 } from "lucide-react";
import { api, errorMessage, formatClock, parseIsoDay, type PrivacyRule, type StatusInfo } from "../api";
import { fmt, STRINGS, type Lang } from "../i18n";
import { InlineConfirm } from "./Common";

export function PrivacyView({ lang, status, onStatusChange }: { lang: Lang; status: StatusInfo | null; onStatusChange: () => void }) {
  const t = STRINGS[lang];
  const [rules, setRules] = useState<PrivacyRule[]>([]);
  const [retentionDays, setRetentionDays] = useState(180);
  const [draftKind, setDraftKind] = useState<"exclude" | "redact">("exclude");
  const [draftMatch, setDraftMatch] = useState("app");
  const [draftPattern, setDraftPattern] = useState("");
  const [rangeStart, setRangeStart] = useState("");
  const [rangeEnd, setRangeEnd] = useState("");
  const [exportFrom, setExportFrom] = useState("");
  const [exportTo, setExportTo] = useState("");
  const [deleteMsg, setDeleteMsg] = useState<string | null>(null);
  const [ruleError, setRuleError] = useState<string | null>(null);
  const [retentionMsg, setRetentionMsg] = useState<string | null>(null);

  function refresh() {
    api.privacyRules().then((r) => setRules(r.items));
    api.getRetention().then((r) => setRetentionDays(r.days));
  }
  useEffect(refresh, []);

  // A9: "delete the half hour I forgot" -- fill both pickers in one click.
  function preset(minutes: number | "today") {
    const now = new Date();
    const start = minutes === "today" ? new Date(now.getFullYear(), now.getMonth(), now.getDate()) : new Date(now.getTime() - minutes * 60000);
    setRangeStart(toLocalInput(start));
    setRangeEnd(toLocalInput(now));
    setDeleteMsg(null);
  }
  // A10: optional export range, whole local days.
  const exportStart = exportFrom ? parseIsoDay(exportFrom).getTime() / 1000 : undefined;
  const exportEnd = exportTo ? parseIsoDay(exportTo).getTime() / 1000 + 86400 : undefined;

  const excludeRules = rules.filter((r) => r.kind === "exclude");
  const redactRules = rules.filter((r) => r.kind === "redact");

  async function addRule() {
    if (!draftPattern.trim()) return;
    setRuleError(null);
    try {
      await api.addPrivacyRule({ kind: draftKind, match_type: draftMatch, pattern: draftPattern.trim(), enabled: true });
      setDraftPattern("");
      refresh();
    } catch (err) {
      setRuleError(errorMessage(err));
    }
  }

  return (
    <div>
      <div className="card">
        <div className="section-title">{t.privacy_status}</div>
        <div className="row" style={{ justifyContent: "space-between" }}>
          <div>
            <span className={`pill ${status?.paused ? "paused" : "recording"}`} style={{ fontSize: 14 }}>
              <span className="dot" />
              {status?.paused
                ? `${t.paused} ${status.paused_until ? `${t.until} ${formatClock(status.paused_until, lang)}` : t.until_resumed}`
                : t.recording}
            </span>
            {status?.paused && status.paused_until && (
              <p className="muted" style={{ fontSize: 12, margin: "6px 0 0" }}>{t.resumes_by_itself}</p>
            )}
            {status?.probe_status && !status.paused && (
              <p className="muted" style={{ fontSize: 12, margin: "6px 0 0" }}>{status.probe_status}</p>
            )}
          </div>
          {status?.paused ? (
            <button
              className="btn btn-primary"
              onClick={async () => {
                await api.resume();
                onStatusChange();
              }}
            >
              {t.resume_now}
            </button>
          ) : (
            <div className="row">
              <button
                className="btn"
                onClick={async () => {
                  await api.pause(15);
                  onStatusChange();
                }}
              >
                {t.pause_15}
              </button>
              <button
                className="btn"
                onClick={async () => {
                  await api.pause(60);
                  onStatusChange();
                }}
              >
                {t.pause_60}
              </button>
              <button
                className="btn"
                onClick={async () => {
                  await api.pauseUntilResumed();
                  onStatusChange();
                }}
              >
                {t.pause_until_resumed}
              </button>
            </div>
          )}
        </div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <div className="section-title">{t.what_agent_can_do}</div>
        <p style={{ fontSize: 13, color: "var(--text-muted)", margin: 0 }}>{t.agent_read_only}</p>
      </div>

      <div className="grid grid-2" style={{ marginTop: 16 }}>
        <RuleList
          title={t.exclude_rules}
          rules={excludeRules}
          onDelete={async (id) => {
            await api.deletePrivacyRule(id);
            refresh();
          }}
        />
        <RuleList
          title={t.redact_rules}
          rules={redactRules}
          onDelete={async (id) => {
            await api.deletePrivacyRule(id);
            refresh();
          }}
        />
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <div className="section-title">{t.add_rule}</div>
        <div className="row">
          <select value={draftKind} onChange={(e) => setDraftKind(e.target.value as "exclude" | "redact")}>
            <option value="exclude">{t.exclude}</option>
            <option value="redact">{t.redact}</option>
          </select>
          <select value={draftMatch} onChange={(e) => setDraftMatch(e.target.value)}>
            <option value="app">app</option>
            <option value="title_regex">title_regex</option>
          </select>
          <input type="text" placeholder={t.pattern} value={draftPattern} onChange={(e) => setDraftPattern(e.target.value)} />
          <button className="btn btn-primary" onClick={addRule}>
            <Plus size={14} /> {t.add}
          </button>
        </div>
        {ruleError && <p className="form-error">{ruleError}</p>}
      </div>

      <div className="grid grid-2" style={{ marginTop: 16 }}>
        <div className="card">
          <div className="section-title">{t.retention}</div>
          <div className="row">
            <input
              type="number"
              value={retentionDays}
              min={1}
              onChange={(e) => setRetentionDays(Number(e.target.value))}
              style={{ width: 90 }}
            />
            <span>{t.retention_days}</span>
            <button
              className="btn btn-primary"
              onClick={async () => {
                try {
                  const r = await api.setRetention(retentionDays);
                  setRetentionMsg(`${t.saved}: ${r.days} ${t.retention_days}`);
                } catch (err) {
                  setRetentionMsg(errorMessage(err));
                }
              }}
            >
              {t.save}
            </button>
          </div>
          {retentionMsg && <p className="muted" style={{ fontSize: 12, margin: "8px 0 0" }}>{retentionMsg}</p>}
        </div>

        <div className="card">
          <div className="section-title">{t.export}</div>
          <div className="row">
            <label>
              {t.export_from} <input type="date" value={exportFrom} onChange={(e) => setExportFrom(e.target.value)} />
            </label>
            <label>
              {t.export_to} <input type="date" value={exportTo} onChange={(e) => setExportTo(e.target.value)} />
            </label>
          </div>
          <div className="row" style={{ marginTop: 10 }}>
            <a className="btn btn-primary" href={api.exportCsvUrl(exportStart, exportEnd)} download>
              <Download size={14} /> {t.export_csv}
            </a>
            <a className="btn" href={api.exportUrl(exportStart, exportEnd)} download>
              <Download size={14} /> {t.export_all_json}
            </a>
          </div>
          <p className="muted" style={{ fontSize: 12, margin: "8px 0 0" }}>{t.export_hint}</p>
        </div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <div className="section-title">{t.delete_range}</div>
        <div className="row" style={{ marginBottom: 10 }}>
          <button className="btn btn-small" onClick={() => preset(15)}>{t.preset_15m}</button>
          <button className="btn btn-small" onClick={() => preset(30)}>{t.preset_30m}</button>
          <button className="btn btn-small" onClick={() => preset(60)}>{t.preset_1h}</button>
          <button className="btn btn-small" onClick={() => preset("today")}>{t.day_picker_today}</button>
          <span className="muted" style={{ fontSize: 12 }}>{t.delete_presets_hint}</span>
        </div>
        <div className="row">
          <label>
            {t.delete_range_start}{" "}
            <input type="datetime-local" value={rangeStart} onChange={(e) => setRangeStart(e.target.value)} />
          </label>
          <label>
            {t.delete_range_end} <input type="datetime-local" value={rangeEnd} onChange={(e) => setRangeEnd(e.target.value)} />
          </label>
        </div>
        <div style={{ marginTop: 10 }}>
          <InlineConfirm
            label={t.delete}
            confirmLabel={t.confirm_button}
            cancelLabel={t.cancel_button}
            disabled={!rangeStart || !rangeEnd}
            onConfirm={async () => {
              const start = new Date(rangeStart).getTime() / 1000;
              const end = new Date(rangeEnd).getTime() / 1000;
              try {
                const res = await api.deleteRange(start, end);
                setDeleteMsg(
                  fmt(t.deleted_summary, {
                    spans: res.deleted.spans ?? 0,
                    files: res.deleted.file_events ?? 0,
                    commits: res.deleted.commits ?? 0,
                    narratives: res.deleted.day_narratives ?? 0,
                  }),
                );
              } catch (err) {
                setDeleteMsg(errorMessage(err));
              }
            }}
          />
        </div>
        {deleteMsg && <p style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 8 }}>{deleteMsg}</p>}
      </div>
    </div>
  );
}

function RuleList({ title, rules, onDelete }: { title: string; rules: PrivacyRule[]; onDelete: (id: number) => void }) {
  return (
    <div className="card">
      <div className="section-title">{title}</div>
      <table>
        <tbody>
          {rules.map((r) => (
            <tr key={r.id}>
              <td style={{ width: 90 }}>{r.match_type}</td>
              <td>
                <code>{r.pattern}</code>
              </td>
              <td style={{ width: 36 }}>
                <button className="icon-button" onClick={() => onDelete(r.id)}>
                  <Trash2 size={13} />
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** A Date as the value a datetime-local input expects (local time, minutes). */
function toLocalInput(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
