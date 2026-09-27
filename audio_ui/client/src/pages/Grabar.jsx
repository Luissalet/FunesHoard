import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import Transcript from "../components/Transcript.jsx";
import { Empty, PageHeader } from "../components/ui.jsx";
import { fmtClock, KIND_LABEL } from "../format.js";
import { useLive } from "../live.js";

const KINDS = Object.entries(KIND_LABEL);

function Meter({ label, value }) {
  return (
    <div className="flex items-center gap-3">
      <span className="w-20 text-[12px] font-semibold">{label}</span>
      <div className="meter flex-1"><span style={{ width: `${Math.round(Math.min(1, value || 0) * 100)}%` }} /></div>
    </div>
  );
}

function LiveView({ rec, onStop }) {
  const { segments } = useLive(rec.session_id, true);
  return (
    <div className="grid gap-5 lg:grid-cols-[320px_minmax(0,1fr)]">
      <section className="panel">
        <div className="help">Grabando</div>
        <div className="text-[34px] font-semibold num leading-tight">{fmtClock(rec.elapsed_s)}</div>
        <div className="mt-1 text-[15px] font-semibold">{rec.title}</div>
        <div className="help">{KIND_LABEL[rec.kind]}</div>
        <div className="mt-4 grid gap-2">
          {rec.tracks.includes("mic") && <Meter label="Yo (micro)" value={rec.levels.mic} />}
          {rec.tracks.includes("system") && <Meter label="Otros (sist.)" value={rec.levels.system} />}
        </div>
        <div className="help mt-3">{rec.chunks_sent} fragmentos enviados · {rec.live_segments} líneas en directo{rec.skipped_silent ? ` · ${rec.skipped_silent} en silencio` : ""}{rec.dropped ? ` · ${rec.dropped} descartadas` : ""}</div>
        <button type="button" className="btn-record stop mt-5 w-full" onClick={onStop}><span className="dot" aria-hidden="true" />Parar grabación</button>
      </section>
      <section className="panel-white">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-[17px] font-semibold">Transcripción en directo</h2>
          <span className="help">provisional: al parar se rehace entera</span>
        </div>
        <Transcript segments={segments} follow />
      </section>
    </div>
  );
}

export default function Grabar() {
  const { status, act, stopRecording } = useApp();
  const s = status.settings;
  const [title, setTitle] = useState("");
  const [kind, setKind] = useState(s.default_kind);
  const [mic, setMic] = useState(s.default_source_mic);
  const [system, setSystem] = useState(s.default_source_system && status.devices.system.length > 0);
  const [language, setLanguage] = useState(s.default_language);
  const [starting, setStarting] = useState(false);
  useEffect(() => { setSystem((v) => v && status.devices.system.length > 0); }, [status.devices.system.length]);

  const micName = (status.devices.mic.find((d) => d.default) || status.devices.mic[0] || {}).name;
  const sysName = (status.devices.system.find((d) => d.default) || status.devices.system[0] || {}).name;
  const canRecord = status.backend !== "none" && (mic || system);

  const start = async (e) => {
    e.preventDefault();
    setStarting(true);
    const session = await act(() => api.start({ title, kind, sources: { mic, system }, language }), "Grabando. Para cuando quieras desde el botón o el aviso superior.");
    setStarting(false);
    if (session) setTitle("");
  };

  if (status.recording) {
    return (
      <div>
        <PageHeader title="Grabar" description="La transcripción aparece a medida que hablas. Al parar, se transcribe de nuevo todo con el modelo completo y sustituye al texto provisional." />
        <LiveView rec={status.recording} onStop={stopRecording} />
      </div>
    );
  }

  return (
    <div className="max-w-[900px]">
      <PageHeader title="Grabar" description="Micrófono y sonido del sistema se guardan como dos pistas: así el texto sabe quién habló («yo» / «otros») sin reconocer voces." />
      {status.backend === "none" && (
        <div className="mb-5">
          <Empty title="No hay captura de audio disponible">{status.backend_notes.join(" · ") || "Revisa la instalación."}</Empty>
        </div>
      )}
      <form onSubmit={start} className="panel grid gap-5">
        <div className="grid gap-4 md:grid-cols-2">
          <div>
            <label className="label" htmlFor="title">Título</label>
            <input id="title" className="field" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Reunión con… (opcional)" maxLength={200} />
          </div>
          <div>
            <label className="label" htmlFor="kind">Tipo</label>
            <select id="kind" className="field" value={kind} onChange={(e) => setKind(e.target.value)}>
              {KINDS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </div>
        </div>
        <fieldset className="grid gap-2">
          <legend className="label">Fuentes</legend>
          <label className="check"><input type="checkbox" checked={mic} onChange={(e) => setMic(e.target.checked)} /> Micrófono <span className="help">— {micName || "no detectado"} · etiqueta «yo»</span></label>
          <label className="check"><input type="checkbox" checked={system} disabled={!status.devices.system.length} onChange={(e) => setSystem(e.target.checked)} /> Sonido del sistema <span className="help">— {sysName || "no disponible en este sistema"} · etiqueta «otros»</span></label>
        </fieldset>
        <div className="grid gap-4 md:grid-cols-2">
          <div>
            <label className="label" htmlFor="lang">Idioma</label>
            <select id="lang" className="field" value={language} onChange={(e) => setLanguage(e.target.value)}>
              <option value="auto">Detectar</option>
              <option value="es">Español</option>
              <option value="en">Inglés</option>
            </select>
          </div>
          <div className="help self-end">
            Modelo {status.transcriber.model} en {status.transcriber.device}
            {status.transcriber.download !== "ready" && <> · <a className="btn-link" href="#/ajustes">descargar en Ajustes</a></>}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-4">
          <button type="submit" className="btn-record" disabled={!canRecord || starting}><span className="dot" aria-hidden="true" />{starting ? "Iniciando…" : "Grabar"}</button>
          {!mic && !system && <span className="help">Elige al menos una fuente.</span>}
        </div>
      </form>
    </div>
  );
}
