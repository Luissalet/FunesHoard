import React, { useCallback, useEffect, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import Player from "../components/Player.jsx";
import Transcript from "../components/Transcript.jsx";
import { Confirm, PageHeader, StatusChip } from "../components/ui.jsx";
import { fmtDateTime, fmtDuration, KIND_LABEL } from "../format.js";
import { useLive } from "../live.js";

function Editable({ value, onCommit, className = "field", as = "input", ...props }) {
  const [draft, setDraft] = useState(value || "");
  useEffect(() => setDraft(value || ""), [value]);
  const commit = () => { if (draft !== (value || "")) onCommit(draft); };
  const Tag = as;
  return <Tag className={className} value={draft} onChange={(e) => setDraft(e.target.value)} onBlur={commit} onKeyDown={(e) => as === "input" && e.key === "Enter" && commit()} {...props} />;
}

export default function Sesion() {
  const { route, act, status, stopRecording } = useApp();
  const id = route.id;
  const [session, setSession] = useState(null);
  const [missing, setMissing] = useState(false);
  const [seek, setSeek] = useState(null);
  const [time, setTime] = useState(0);
  const [confirm, setConfirm] = useState(false);
  const [tagDraft, setTagDraft] = useState("");

  const load = useCallback(() => api.session(id).then(setSession).catch(() => setMissing(true)), [id]);
  useEffect(() => { load(); }, [load]);
  const active = !!session && (session.status === "recording" || session.status === "processing");
  const { segments: liveSegments } = useLive(id, active, () => load());
  useEffect(() => {
    const t = Number(route.query.get("t"));
    if (session && session.status === "done" && Number.isFinite(t) && t > 0) setSeek({ t });
  }, [session && session.status, route.query]); // eslint-disable-line react-hooks/exhaustive-deps

  if (missing) return <p className="help">Esta sesión no existe (puede que se haya borrado). <a href="#/sesiones" className="btn-link">Volver</a></p>;
  if (!session) return <p className="help">Cargando…</p>;

  const save = async (patch, message) => {
    const updated = await act(() => api.patchSession(id, patch), message);
    if (updated) setSession((s) => ({ ...s, ...updated }));
  };
  const addTag = (e) => {
    e.preventDefault();
    if (!tagDraft.trim()) return;
    save({ tags: [...session.tags, tagDraft.trim()] }, "Etiqueta añadida.");
    setTagDraft("");
  };
  const remove = async () => {
    setConfirm(false);
    if (await act(() => api.deleteSession(id), "Sesión borrada.")) window.location.hash = "#/sesiones";
  };
  const segments = active ? [...session.segments.filter((s) => !liveSegments.some((l) => l.id === s.id)), ...liveSegments].sort((a, b) => a.start_s - b.start_s || a.id - b.id) : session.segments;
  const isRecordingThis = status.recording && status.recording.session_id === id;

  return (
    <div>
      <a href="#/sesiones" className="btn-link text-[13px]">← Sesiones</a>
      <PageHeader title={<Editable value={session.title} onCommit={(v) => save({ title: v }, "Título guardado.")} className="field text-[22px] font-semibold" aria-label="Título" maxLength={200} />}>
        <div className="flex flex-wrap gap-2">
          {isRecordingThis && <button type="button" className="btn btn-primary" onClick={stopRecording}>Parar grabación</button>}
          {session.status === "done" && ["txt", "srt", "md"].map((f) => <a key={f} className="btn" href={api.exportUrl(id, f)} download>{f.toUpperCase()}</a>)}
          {session.status === "failed" && <button type="button" className="btn" onClick={() => act(() => api.retranscribe(id), "Transcripción en cola.")}>Reintentar</button>}
          <button type="button" className="btn btn-danger" onClick={() => setConfirm(true)} disabled={isRecordingThis}>Borrar</button>
        </div>
      </PageHeader>
      <div className="mb-4 flex flex-wrap items-center gap-2 help">
        <StatusChip status={session.status} />
        <select className="field field-sm w-auto" value={session.kind} onChange={(e) => save({ kind: e.target.value }, "Tipo guardado.")} aria-label="Tipo">
          {Object.entries(KIND_LABEL).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
        <span>{fmtDateTime(session.started_at)}</span>
        <span>· {fmtDuration(session.duration_s)}</span>
        <span>· {session.sources.mic && session.sources.system ? "micrófono + sistema" : session.sources.mic ? "micrófono" : "sistema"}</span>
        <span>· {session.language === "auto" ? "idioma detectado" : session.language}</span>
        {session.error && <span className="chip chip-danger">{session.error}</span>}
        {session.stats && session.stats.no_speech && <span className="chip chip-warn">sin voz detectada</span>}
        {session.stats && session.stats.final && (session.stats.final.skipped_silent > 0 || session.stats.final.dropped > 0) && (
          <span className="help">· {session.stats.final.skipped_silent} fragmentos en silencio · {session.stats.final.dropped} líneas descartadas como ruido</span>
        )}
      </div>
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_300px]">
        <div className="grid gap-4">
          {session.status === "done" && <Player sessionId={id} duration={session.duration_s} seekTo={seek} onTime={setTime} />}
          {session.status === "processing" && <div className="banner banner-processing">Transcribiendo la sesión completa… el texto provisional se sustituirá al terminar.</div>}
          <section className="panel-white">
            <div className="mb-2 flex items-center justify-between">
              <h2 className="text-[17px] font-semibold">Transcripción</h2>
              <span className="help">{segments.length} líneas · pulsa una para saltar</span>
            </div>
            {session.status === "done" && !segments.length ? (
              <p className="help px-2 py-6 text-center">Sin voz detectada: la grabación solo contiene silencio o ruido, así que no hay texto que mostrar.</p>
            ) : (
              <Transcript segments={segments} currentTime={session.status === "done" ? time : null} onSeek={session.status === "done" ? (t) => setSeek({ t }) : undefined} follow={active} />
            )}
          </section>
        </div>
        <aside className="grid content-start gap-4">
          <section className="panel">
            <label className="label" htmlFor="notes">Notas</label>
            <Editable as="textarea" id="notes" value={session.notes} onCommit={(v) => save({ notes: v }, "Notas guardadas.")} placeholder="Acuerdos, tareas, contexto… se guarda al salir del campo." maxLength={20000} />
          </section>
          <section className="panel">
            <div className="label">Etiquetas</div>
            <div className="flex flex-wrap gap-1">
              {session.tags.map((t) => (
                <span key={t} className="chip chip-tag">{t} <button type="button" className="ml-1" aria-label={`Quitar ${t}`} onClick={() => save({ tags: session.tags.filter((x) => x !== t) }, "Etiqueta quitada.")}>×</button></span>
              ))}
              {!session.tags.length && <span className="help">Sin etiquetas.</span>}
            </div>
            <form onSubmit={addTag} className="mt-2 flex gap-2">
              <input className="field field-sm" value={tagDraft} onChange={(e) => setTagDraft(e.target.value)} placeholder="nueva etiqueta" maxLength={40} aria-label="Nueva etiqueta" />
              <button type="submit" className="btn btn-sm">Añadir</button>
            </form>
          </section>
        </aside>
      </div>
      <Confirm open={confirm} title="¿Borrar esta sesión?" onConfirm={remove} onCancel={() => setConfirm(false)}>Se eliminan el audio, la transcripción y las notas. No se puede deshacer.</Confirm>
    </div>
  );
}
