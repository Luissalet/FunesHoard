import { useEffect, useState } from "react";
import { Brain, RefreshCw } from "lucide-react";
import { api, errorMessage, type BackendConfigPatch, type BackendStatus } from "../api";
import { STRINGS, type Lang } from "../i18n";
import { Badge } from "./Common";

const STATE_COLOR: Record<string, string> = { resolved: "#1a7f37", unavailable: "#8a8a8a" };

export function SettingsView({ lang }: { lang: Lang }) {
  const t = STRINGS[lang];
  const [status, setStatus] = useState<BackendStatus | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [rechecking, setRechecking] = useState(false);

  const [faustusUrl, setFaustusUrl] = useState("");
  const [faustusToken, setFaustusToken] = useState("");
  const [llmUrl, setLlmUrl] = useState("");
  const [llmModel, setLlmModel] = useState("");
  const [saveMsg, setSaveMsg] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);

  const [writeMyDay, setWriteMyDay] = useState(true);
  const [featureMsg, setFeatureMsg] = useState<string | null>(null);
  const [meetingsAwayMin, setMeetingsAwayMin] = useState(30);

  function refresh() {
    api
      .backendStatus()
      .then((s) => {
        setStatus(s);
        setFaustusUrl(s.faustus_url || "");
        setLlmUrl(s.llm_url_override || "");
        setLlmModel(s.llm_model_override || "");
        setWriteMyDay(s.write_my_day_enabled);
        setLoadError(null);
      })
      .catch((err) => setLoadError(errorMessage(err)));
    api.meetingsAwaySetting().then((r) => setMeetingsAwayMin(r.minutes)).catch(() => {});
  }
  useEffect(refresh, []);

  const llm = status?.capabilities?.llm;

  async function recheck() {
    setRechecking(true);
    try {
      await api.recheckBackend();
      refresh();
    } finally {
      setRechecking(false);
    }
  }

  async function saveConfig() {
    setSaveError(null);
    setSaveMsg(null);
    try {
      // A blank field is sent as "" so emptying it removes a saved override;
      // the token is the exception -- blank means "keep the stored one".
      const patch: BackendConfigPatch = {
        faustus_url: faustusUrl.trim(),
        capabilities: { llm: { url: llmUrl.trim(), model: llmModel.trim() } },
      };
      if (faustusToken.trim()) patch.faustus_token = faustusToken.trim();
      const res = await api.saveBackendConfig(patch);
      setFaustusToken("");
      setSaveMsg(res.faustus_token_set ? t.settings_saved_with_token : t.settings_saved);
      refresh();
    } catch (err) {
      setSaveError(errorMessage(err));
    }
  }

  async function clearToken() {
    setSaveError(null);
    try {
      await api.saveBackendConfig({ faustus_token: "" });
      setSaveMsg(t.settings_saved);
      refresh();
    } catch (err) {
      setSaveError(errorMessage(err));
    }
  }

  async function toggleWriteMyDay(enabled: boolean) {
    setWriteMyDay(enabled);
    try {
      await api.setWriteMyDaySetting(enabled);
      setFeatureMsg(t.saved);
    } catch (err) {
      setFeatureMsg(errorMessage(err));
    }
  }

  async function saveMeetingsAway(minutes: number) {
    setMeetingsAwayMin(minutes);
    try {
      await api.setMeetingsAwaySetting(minutes);
      setFeatureMsg(t.saved);
    } catch (err) {
      setFeatureMsg(errorMessage(err));
    }
  }

  return (
    <div>
      <div className="card">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <div className="section-title">{t.models_panel}</div>
          <button className="btn" onClick={recheck} disabled={rechecking}>
            <RefreshCw size={14} className={rechecking ? "spin" : ""} /> {t.recheck}
          </button>
        </div>
        {loadError ? (
          <p className="form-error">{loadError}</p>
        ) : !llm ? (
          <p className="muted" style={{ fontSize: 13 }}>{t.working}</p>
        ) : (
          <table>
            <tbody>
              <tr>
                <td style={{ width: 32 }}>
                  <Brain size={16} />
                </td>
                <td style={{ width: 60 }}>llm</td>
                <td style={{ width: 110 }}>{llm.provider || "—"}</td>
                <td>{llm.model || "—"}</td>
                <td style={{ width: 110 }}>
                  <Badge text={llm.state === "resolved" ? t.model_resolved : t.model_unavailable} color={STATE_COLOR[llm.state]} />
                </td>
              </tr>
              <tr>
                <td colSpan={5} className="muted" style={{ fontSize: 12, paddingTop: 4 }}>
                  {llm.state !== "resolved" && <div style={{ fontSize: 13, marginBottom: 2 }}>{t.no_llm}</div>}
                  {llm.reason}
                </td>
              </tr>
            </tbody>
          </table>
        )}
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <div className="section-title">{t.model_overrides}</div>
        <p className="muted" style={{ fontSize: 12, marginTop: 0 }}>{t.model_overrides_hint}</p>
        <div className="grid grid-2 form-grid">
          <label>
            {t.faustus_url}
            <input type="text" placeholder="http://127.0.0.1:7000" value={faustusUrl} onChange={(e) => setFaustusUrl(e.target.value)} />
          </label>
          <label>
            {t.faustus_token}
            <input
              type="password"
              placeholder={status?.faustus_token_set ? t.token_already_set : ""}
              value={faustusToken}
              onChange={(e) => setFaustusToken(e.target.value)}
            />
          </label>
          <label>
            {t.llm_url_override}
            <input type="text" placeholder="http://127.0.0.1:8081" value={llmUrl} onChange={(e) => setLlmUrl(e.target.value)} />
          </label>
          <label>
            {t.llm_model_override}
            <input type="text" placeholder="qwen3.8-27b-q8-llamacpp" value={llmModel} onChange={(e) => setLlmModel(e.target.value)} />
          </label>
        </div>
        <div className="row" style={{ marginTop: 10 }}>
          <button className="btn btn-primary" onClick={saveConfig}>
            {t.save}
          </button>
          {status?.faustus_token_set && (
            <button className="btn btn-ghost" onClick={clearToken}>
              {t.clear_token}
            </button>
          )}
        </div>
        {saveMsg && <p className="muted" style={{ fontSize: 12, marginTop: 8 }}>{saveMsg}</p>}
        {saveError && <p className="form-error">{saveError}</p>}
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <div className="section-title">{t.features}</div>
        <div className="row" style={{ justifyContent: "space-between" }}>
          <div>
            <strong>{t.write_my_day}</strong>
            <p className="muted" style={{ fontSize: 12, margin: "2px 0 0" }}>{t.write_my_day_hint}</p>
          </div>
          <label className="switch">
            <input type="checkbox" checked={writeMyDay} onChange={(e) => toggleWriteMyDay(e.target.checked)} />
            <span />
          </label>
        </div>
        <div className="row" style={{ justifyContent: "space-between", marginTop: 12 }}>
          <div>
            <strong>{t.meetings_away}</strong>
            <p className="muted" style={{ fontSize: 12, margin: "2px 0 0" }}>{t.meetings_away_hint}</p>
          </div>
          <input
            type="number"
            min={5}
            max={180}
            step={5}
            value={meetingsAwayMin}
            onChange={(e) => saveMeetingsAway(Number(e.target.value))}
            style={{ width: 70 }}
          />
        </div>
        {featureMsg && <p className="muted" style={{ fontSize: 12, marginTop: 8 }}>{featureMsg}</p>}
      </div>
    </div>
  );
}
