import React, { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { fmtClock, fmtDateTime, SPEAKER_LABEL } from "../format.js";

const OWNER_LABEL = { yo: "Yo", otros: "Otros" };
const WHEN_FORMAT = { day: "numeric", month: "short", year: "numeric" };

function whenOf(item) {
  if (item.due_date) {
    const [y, m, d] = item.due_date.split("-").map(Number);
    const text = new Date(y, m - 1, d).toLocaleDateString("es-ES", { weekday: "short", ...WHEN_FORMAT });
    return item.due_text ? `${text} (${item.due_text})` : text;
  }
  return item.due_text || "";
}

/** Meeting minutes of a finished session: summary, decisions, action items with evidence, open questions. */
export default function Minutes({ sessionId, sessionDone, onSeek, notify }) {
  const [state, setState] = useState(null);
  const [busy, setBusy] = useState(false);
  const timer = useRef(null);

  const load = useCallback(() => api.minutes(sessionId).then(setState).catch(() => setState(null)), [sessionId]);

  useEffect(() => {
    if (!sessionDone) return undefined;
    load();
    // The minutes of a meeting that just finished are written in the background: look again shortly.
    const later = [1500, 5000].map((ms) => setTimeout(load, ms));
    return () => later.forEach(clearTimeout);
  }, [sessionDone, load]);

  useEffect(() => {
    clearInterval(timer.current);
    if (state && state.generating) timer.current = setInterval(load, 2000);
    return () => clearInterval(timer.current);
  }, [state && state.generating, load]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!sessionDone || !state) return null;
  const { minutes, generating, last, model } = state;

  const generate = async (regenerate) => {
    setBusy(true);
    try {
      setState(await api.makeMinutes(sessionId, { regenerate }));
    } catch (error) {
      notify(error.message);
    }
    setBusy(false);
  };
  const copy = async () => {
    try {
      const text = await api.minutesMarkdown(sessionId);
      await navigator.clipboard.writeText(text);
      notify("Acta copiada como Markdown.");
    } catch {
      notify("No se pudo copiar; usa el botón MD para descargarla.");
    }
  };

  const noModel = !minutes && model && !model.available;
  const failure = last && ["error", "no_model"].includes(last.status) ? last : null;

  return (
    <section className="panel-white" aria-labelledby="minutes-title">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 id="minutes-title" className="text-[17px] font-semibold">Acta</h2>
        <div className="flex flex-wrap gap-2">
          {minutes && <button type="button" className="btn btn-sm" onClick={copy}>Copiar Markdown</button>}
          <button type="button" className="btn btn-sm btn-primary" disabled={busy || generating || noModel} onClick={() => generate(!!minutes)}>
            {generating ? "Escribiendo…" : minutes ? "Regenerar acta" : "Generar acta"}
          </button>
        </div>
      </div>
      {generating && <div className="banner banner-processing mb-3" role="status">Escribiendo el acta con el modelo local… puede tardar unos minutos en reuniones largas.</div>}
      {failure && !generating && (
        <div className="mb-3 rounded-md border p-3 text-[13px]" style={{ background: "var(--danger-bg)", color: "var(--danger-ink)", borderColor: "var(--danger-line)" }} role="alert">
          {failure.status === "no_model" ? "No hay ningún modelo de lenguaje disponible. " : "No se pudo escribir el acta. "}{failure.detail}
        </div>
      )}
      {!minutes && !generating && (
        <p className="help">
          {noModel
            ? `Todavía no hay acta y no hay modelo de lenguaje local disponible (${model.detail || "carga uno en Faustus u Ollama"}). No se inventa nada: cuando haya modelo podrás generarla.`
            : "Todavía no hay acta. El modelo local la escribe a partir de la transcripción y comprueba que cada compromiso cite palabras que de verdad se dijeron."}
        </p>
      )}
      {minutes && (
        <div className="grid gap-4 text-[14px]">
          <div>
            <h3 className="label">Resumen</h3>
            <ul className="list-disc pl-5">{minutes.summary.split("\n").filter(Boolean).map((line, i) => <li key={i}>{line}</li>)}</ul>
          </div>
          {minutes.decisions.length > 0 && (
            <div>
              <h3 className="label">Decisiones</h3>
              <ul className="list-disc pl-5">{minutes.decisions.map((d, i) => <li key={i}>{d}</li>)}</ul>
            </div>
          )}
          <div>
            <h3 className="label">Acciones y compromisos ({minutes.action_items.length})</h3>
            {minutes.action_items.length === 0 ? (
              <p className="help">No se detectaron compromisos con cita en la transcripción.</p>
            ) : (
              <ul className="grid gap-2">
                {minutes.action_items.map((item, i) => (
                  <li key={i} className="rounded-md border p-3" style={{ borderColor: "var(--line)" }}>
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="chip chip-tag">{OWNER_LABEL[item.owner] || item.owner || "¿quién?"}</span>
                      {item.counterpart && <span className="help">para {OWNER_LABEL[item.counterpart] || item.counterpart}</span>}
                      {whenOf(item) && <span className="chip chip-warn">{whenOf(item)}</span>}
                    </div>
                    <div className="mt-1 font-semibold">{item.action}</div>
                    <button type="button" className="mt-1 block w-full text-left help" onClick={() => onSeek && onSeek(item.evidence.start_s)} aria-label={`Escuchar desde ${fmtClock(item.evidence.start_s)}`}>
                      <span className="num font-semibold" style={{ color: "var(--accent)" }}>{fmtClock(item.evidence.start_s)} ▸</span>{" "}
                      {SPEAKER_LABEL[item.evidence.speaker] || item.evidence.speaker}: «{item.evidence.quote}»
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
          {minutes.open_questions.length > 0 && (
            <div>
              <h3 className="label">Preguntas abiertas</h3>
              <ul className="list-disc pl-5">{minutes.open_questions.map((q, i) => <li key={i}>{q}</li>)}</ul>
            </div>
          )}
          {minutes.participants.length > 0 && (
            <div className="flex flex-wrap items-center gap-1">
              <span className="label mb-0 mr-1">Participantes</span>
              {minutes.participants.map((p) => <span key={p} className="chip chip-tag">{p}</span>)}
            </div>
          )}
          <p className="help">Escrita por {minutes.model || "el modelo local"} el {fmtDateTime(minutes.created_at)}. Cada compromiso lleva la cita literal de la transcripción: compruébala antes de fiarte.</p>
        </div>
      )}
    </section>
  );
}
