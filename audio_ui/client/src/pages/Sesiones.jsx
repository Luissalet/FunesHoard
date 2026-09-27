import React, { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import { Empty, PageHeader, StatusChip } from "../components/ui.jsx";
import { fmtDateTime, fmtDuration, KIND_LABEL } from "../format.js";

export default function Sesiones() {
  const { status, act } = useApp();
  const [rows, setRows] = useState(null);
  const [tags, setTags] = useState([]);
  const [q, setQ] = useState("");
  const [kind, setKind] = useState("");
  const [tag, setTag] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [importing, setImporting] = useState(false);
  const file = useRef(null);

  const load = () => api.sessions({ q, kind, tag, from, to }).then((r) => setRows(r.sessions)).catch(() => setRows([]));
  useEffect(() => { load(); }, [q, kind, tag, from, to, status.sessions_total, status.queue_depth]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { api.tags().then((r) => setTags(r.tags)).catch(() => {}); }, [status.sessions_total]);

  const onImport = async (e) => {
    const chosen = e.target.files && e.target.files[0];
    if (!chosen) return;
    setImporting(true);
    await act(() => api.importFile(chosen, { kind: "other" }), `«${chosen.name}» importado; se transcribe en segundo plano.`);
    setImporting(false);
    e.target.value = "";
    load();
  };

  return (
    <div>
      <PageHeader title="Sesiones" description="Reuniones, entrevistas y notas grabadas o importadas. Abre una para escucharla junto a su transcripción.">
        <div className="flex gap-2">
          <input ref={file} type="file" accept=".wav,.mp3,.m4a,.ogg,.opus,.flac,.mp4,.webm,.mkv,.aac,.mov" className="hidden" onChange={onImport} />
          <button type="button" className="btn" disabled={importing} onClick={() => file.current && file.current.click()}>{importing ? "Importando…" : "Importar archivo"}</button>
          <a href="#/grabar" className="btn btn-primary">Grabar</a>
        </div>
      </PageHeader>
      <div className="mb-4 grid gap-2 md:grid-cols-[minmax(0,1fr)_150px_150px_140px_140px]">
        <input className="field" placeholder="Buscar en títulos y texto…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Buscar" />
        <select className="field" value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Tipo">
          <option value="">Todos los tipos</option>
          {Object.entries(KIND_LABEL).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
        <select className="field" value={tag} onChange={(e) => setTag(e.target.value)} aria-label="Etiqueta">
          <option value="">Todas las etiquetas</option>
          {tags.map((t) => <option key={t.tag} value={t.tag}>{t.tag} ({t.count})</option>)}
        </select>
        <input className="field" type="date" value={from} onChange={(e) => setFrom(e.target.value)} aria-label="Desde" />
        <input className="field" type="date" value={to} onChange={(e) => setTo(e.target.value)} aria-label="Hasta" />
      </div>
      {rows === null ? (
        <p className="help">Cargando…</p>
      ) : rows.length === 0 ? (
        <Empty title={q || kind || tag || from || to ? "Nada coincide con estos filtros" : "Todavía no hay sesiones"} action={<a href="#/grabar" className="btn btn-primary">Grabar la primera</a>}>
          {q || kind || tag || from || to ? "Prueba con otras palabras o quita filtros." : "Graba una reunión o importa un archivo de audio para empezar."}
        </Empty>
      ) : (
        <div className="panel-white p-0 md:px-3">
          {rows.map((s) => (
            <a key={s.id} href={`#/sesiones/${s.id}`} className="row">
              <div className="flex flex-wrap items-center gap-2">
                <span className="min-w-0 flex-1 truncate text-[15px] font-semibold">{s.title}</span>
                <StatusChip status={s.status} />
                <span className="chip">{KIND_LABEL[s.kind]}</span>
                <span className="help num">{fmtDuration(s.duration_s)}</span>
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-2 help">
                <span>{fmtDateTime(s.started_at)}</span>
                <span>· {s.sources.mic && s.sources.system ? "yo + otros" : s.sources.mic ? "micrófono" : "sistema"}</span>
                {s.tags.map((t) => <span key={t} className="chip chip-tag">{t}</span>)}
              </div>
              {s.first_line && <div className="mt-1 truncate text-[13px]" style={{ color: "var(--muted)" }}>{s.first_line}</div>}
            </a>
          ))}
        </div>
      )}
    </div>
  );
}
