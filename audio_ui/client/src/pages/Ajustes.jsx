import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import { PageHeader, Switch } from "../components/ui.jsx";
import { fmtBytes, KIND_LABEL } from "../format.js";

const MODELS = [
  { value: "tiny", label: "tiny — muy rápido, poca precisión" },
  { value: "base", label: "base — rápido" },
  { value: "small", label: "small — equilibrio (por defecto)" },
  { value: "medium", label: "medium — preciso, lento en CPU" },
  { value: "large-v3", label: "large-v3 — máxima precisión, GPU recomendada" },
  { value: "large-v3-turbo", label: "large-v3-turbo — preciso y rápido en GPU" },
  { value: "distil-large-v3", label: "distil-large-v3 — inglés, rápido" },
];
const COMPUTE = ["auto", "int8", "int8_float16", "float16", "float32"];
const VAD = ["Permisivo", "Normal", "Estricto", "Muy estricto"];

function Row({ label, help, children }) {
  return (
    <div className="grid gap-2 py-3 md:grid-cols-[minmax(0,1fr)_300px] md:items-center" style={{ borderTop: "1px solid var(--line)" }}>
      <div>
        <div className="text-[13px] font-semibold">{label}</div>
        {help && <div className="help">{help}</div>}
      </div>
      <div className="flex items-center justify-end gap-2">{children}</div>
    </div>
  );
}

export default function Ajustes() {
  const { status, act } = useApp();
  const [settings, setSettings] = useState(null);
  useEffect(() => { api.settings().then(setSettings).catch(() => {}); }, []);
  const save = async (patch) => {
    const updated = await act(() => api.updateSettings(patch));
    if (updated) setSettings(updated);
  };
  if (!settings) return <p className="help">Cargando…</p>;
  const t = status.transcriber;
  const download = { ready: ["chip-ok", "descargado"], downloading: ["chip-warn", "descargando…"], missing: ["chip-warn", "no descargado"] }[t.download] || ["", t.download];

  return (
    <div className="max-w-[900px]">
      <PageHeader title="Ajustes" description="Todo se guarda al instante en Funes; ni el audio ni el texto salen del ordenador." />

      <section className="panel mb-5">
        <h2 className="text-[17px] font-semibold">Transcripción</h2>
        <div className="help mb-2">Motor: {t.name}{status.whisper_installed ? "" : " (faster-whisper no instalado)"} · CUDA {t.cuda ? "disponible" : "no detectada"} · VAD {status.vad}</div>
        <Row label="Modelo" help="Los modelos se descargan una vez a la carpeta de datos. Cambiarlo obliga a recargar (la primera transcripción tarda más).">
          <select className="field field-sm" value={settings.model_size} onChange={(e) => save({ model_size: e.target.value })}>
            {MODELS.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
          </select>
        </Row>
        <Row label="Estado del modelo" help={`${t.model} · ${t.models_dir || status.models_dir}${t.error ? ` · error: ${t.error}` : ""}`}>
          <span className={`chip ${download[0]}`}>{download[1]}</span>
          {t.download !== "ready" && <button type="button" className="btn btn-sm" onClick={() => act(api.downloadModel, "Descarga en cola.")}>Descargar ahora</button>}
          {t.download === "ready" && !t.loaded && <button type="button" className="btn btn-sm" onClick={() => act(api.downloadModel, "Cargando modelo.")}>Precargar</button>}
          {t.loaded && <span className="help">cargado</span>}
        </Row>
        <Row label="Dispositivo" help="auto = CUDA si existe, si no CPU.">
          <select className="field field-sm w-32" value={settings.device} onChange={(e) => save({ device: e.target.value })}>
            {["auto", "cuda", "cpu"].map((v) => <option key={v} value={v}>{v}</option>)}
          </select>
        </Row>
        <Row label="Tipo de cómputo" help="auto = float16 en GPU, int8 en CPU.">
          <select className="field field-sm w-40" value={settings.compute_type} onChange={(e) => save({ compute_type: e.target.value })}>
            {COMPUTE.map((v) => <option key={v} value={v}>{v}</option>)}
          </select>
        </Row>
        <Row label="Transcripción en directo" help="Mientras grabas, cada frase se transcribe al vuelo. Al parar se rehace todo.">
          <Switch checked={settings.live_transcription} label="Transcripción en directo" onChange={(v) => save({ live_transcription: v })} />
        </Row>
        <Row label="Sensibilidad del detector de voz" help="Estricto corta antes las pausas; permisivo agrupa frases largas.">
          <select className="field field-sm w-40" value={settings.vad_sensitivity} onChange={(e) => save({ vad_sensitivity: Number(e.target.value) })}>
            {VAD.map((label, index) => <option key={index} value={index}>{label}</option>)}
          </select>
        </Row>
        <Row label="Fragmento máximo en directo" help="Segundos de habla continua antes de enviar al modelo.">
          <input type="number" className="field field-sm w-24 text-right num" min={5} max={60} value={settings.live_chunk_max_s} onChange={(e) => save({ live_chunk_max_s: Number(e.target.value) })} />
        </Row>
      </section>

      <section className="panel mb-5">
        <h2 className="text-[17px] font-semibold">Valores por defecto al grabar</h2>
        <Row label="Micrófono" help={`Etiqueta «yo». ${status.devices.mic.map((d) => d.name).join(", ") || "sin dispositivos"}`}>
          <Switch checked={settings.default_source_mic} label="Micrófono por defecto" onChange={(v) => save({ default_source_mic: v })} />
        </Row>
        <Row label="Sonido del sistema" help={`Etiqueta «otros». ${status.devices.system.map((d) => d.name).join(", ") || "no disponible en este sistema"}`}>
          <Switch checked={settings.default_source_system} label="Sistema por defecto" onChange={(v) => save({ default_source_system: v })} />
        </Row>
        <Row label="Tipo">
          <select className="field field-sm w-40" value={settings.default_kind} onChange={(e) => save({ default_kind: e.target.value })}>
            {Object.entries(KIND_LABEL).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </Row>
        <Row label="Idioma">
          <select className="field field-sm w-40" value={settings.default_language} onChange={(e) => save({ default_language: e.target.value })}>
            <option value="auto">Detectar</option><option value="es">Español</option><option value="en">Inglés</option>
          </select>
        </Row>
      </section>

      <section className="panel">
        <h2 className="text-[17px] font-semibold">Sistema</h2>
        <Row label="Captura de audio" help={status.backend_notes.join(" · ") || "sin avisos"}><span className="chip">{status.backend}</span></Row>
        <Row label="ffmpeg" help="Necesario para importar mp3/m4a/mp4 (si falta se usa el decodificador de faster-whisper).">
          <span className={`chip ${status.ffmpeg ? "chip-ok" : status.pyav ? "chip-warn" : "chip-danger"}`}>{status.ffmpeg ? "detectado" : status.pyav ? "no, PyAV disponible" : "no detectado"}</span>
        </Row>
        <Row label="Carpeta de datos" help={status.data_dir}><span className="help num">{fmtBytes(status.storage_bytes)} en sesiones</span></Row>
        <Row label="Espacio libre en disco"><span className="help num">{fmtBytes(status.disk_free_bytes)}</span></Row>
        {status.last_error && <Row label="Último error"><span className="chip chip-danger">{status.last_error}</span></Row>}
      </section>
    </div>
  );
}
