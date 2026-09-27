import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { Empty, PageHeader } from "../components/ui.jsx";
import { fmtClock, fmtDateTime, KIND_LABEL, SPEAKER_LABEL } from "../format.js";

export default function Buscar() {
  const [q, setQ] = useState("");
  const [kind, setKind] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (q.trim().length < 2) { setResult(null); return undefined; }
    const timer = setTimeout(() => {
      api.search({ q: q.trim(), kind, from, to }).then((r) => { setResult(r); setError(null); }).catch((e) => setError(e.message));
    }, 250);
    return () => clearTimeout(timer);
  }, [q, kind, from, to]);

  return (
    <div>
      <PageHeader title="Buscar" description="Busca una palabra en todas las transcripciones. Cada resultado lleva al momento exacto de la sesión." />
      <div className="mb-4 grid gap-2 md:grid-cols-[minmax(0,1fr)_150px_140px_140px]">
        <input className="field" autoFocus placeholder="salario, presupuesto, entrega…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Palabras" />
        <select className="field" value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Tipo">
          <option value="">Todos los tipos</option>
          {Object.entries(KIND_LABEL).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
        <input className="field" type="date" value={from} onChange={(e) => setFrom(e.target.value)} aria-label="Desde" />
        <input className="field" type="date" value={to} onChange={(e) => setTo(e.target.value)} aria-label="Hasta" />
      </div>
      {error && <p className="help" role="alert">{error}</p>}
      {!result ? (
        <p className="help">Escribe al menos dos letras.</p>
      ) : result.sessions.length === 0 ? (
        <Empty title="Sin resultados">Prueba con otra palabra: la búsqueda ignora acentos y completa la última palabra.</Empty>
      ) : (
        <div className="grid gap-4">
          <p className="help">{result.total} coincidencias en {result.sessions.length} sesiones</p>
          {result.sessions.map(({ session, hits }) => (
            <section key={session.id} className="panel-white">
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <a href={`#/sesiones/${session.id}`} className="min-w-0 flex-1 truncate text-[15px] font-semibold" style={{ color: "var(--accent)" }}>{session.title}</a>
                <span className="chip">{KIND_LABEL[session.kind]}</span>
                <span className="help">{fmtDateTime(session.started_at)}</span>
              </div>
              {hits.map((h) => (
                <a key={h.segment_id} href={`#/sesiones/${session.id}?t=${h.start_s}`} className={`seg seg-${h.speaker}`}>
                  <span className="t num">{fmtClock(h.start_s)}</span>
                  <span className="who">{SPEAKER_LABEL[h.speaker] || h.speaker}</span>
                  <span className="text min-w-0 break-words" dangerouslySetInnerHTML={{ __html: escapeButMark(h.snippet) }} />
                </a>
              ))}
            </section>
          ))}
        </div>
      )}
    </div>
  );
}

// The snippet comes from SQLite with <mark> markers around matches; everything else is escaped.
function escapeButMark(snippet) {
  const escaped = snippet.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return escaped.replace(/&lt;mark&gt;/g, "<mark>").replace(/&lt;\/mark&gt;/g, "</mark>");
}
