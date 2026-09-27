import React, { useEffect } from "react";
import { fmtClock } from "../format.js";

export function Toast({ message, onClose }) {
  useEffect(() => {
    if (!message) return undefined;
    const timer = setTimeout(onClose, 4000);
    return () => clearTimeout(timer);
  }, [message, onClose]);
  if (!message) return null;
  return (
    <div className="toast" role="status">
      {message} <button type="button" className="ml-3 underline" onClick={onClose}>Cerrar</button>
    </div>
  );
}

export function Banner({ status, onStop }) {
  if (!status) return null;
  const rec = status.recording;
  if (rec) {
    return (
      <div className="banner banner-recording" role="status" aria-live="polite">
        <span className="dot" aria-hidden="true" />
        <span className="min-w-0 flex-1 truncate">
          Grabando… <span className="num">{fmtClock(rec.elapsed_s)}</span> <span className="font-normal opacity-80">· {rec.title} · {rec.tracks.map((t) => (t === "mic" ? "micrófono" : "sistema")).join(" + ")}</span>
        </span>
        <a href={`#/sesiones/${rec.session_id}`} className="btn btn-sm">Ver</a>
        <button type="button" className="btn btn-sm btn-primary" onClick={onStop}>Parar</button>
      </div>
    );
  }
  if (status.queue_depth > 0) {
    return (
      <div className="banner banner-processing" role="status" aria-live="polite">
        <span className="min-w-0 flex-1">Transcribiendo… <span className="font-normal opacity-80">{status.worker_busy || `${status.queue_depth} en cola`}</span></span>
      </div>
    );
  }
  return null;
}

export function Switch({ checked, onChange, label }) {
  return <button type="button" role="switch" aria-checked={checked} aria-label={label} className="switch" onClick={() => onChange(!checked)} />;
}

export function Empty({ title, children, action }) {
  return (
    <div className="rounded-lg border border-dashed p-8 text-center" style={{ borderColor: "var(--field-line)" }}>
      <p className="font-semibold">{title}</p>
      <p className="help mt-1">{children}</p>
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function PageHeader({ title, description, children }) {
  return (
    <header className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-[26px] font-semibold leading-tight md:text-[30px]">{title}</h1>
        {description && <p className="help mt-1 max-w-[70ch]">{description}</p>}
      </div>
      {children}
    </header>
  );
}

export function StatusChip({ status }) {
  const cls = { recording: "chip-rec", processing: "chip-warn", done: "chip-ok", failed: "chip-danger" }[status] || "";
  const label = { recording: "Grabando", processing: "Transcribiendo", done: "Lista", failed: "Error" }[status] || status;
  return <span className={`chip ${cls}`}>{label}</span>;
}

export function Confirm({ open, title, children, confirmLabel = "Borrar", onConfirm, onCancel }) {
  const ref = React.useRef(null);
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);
  return (
    <dialog ref={ref} onClose={onCancel} aria-labelledby="confirm-title">
      <h2 id="confirm-title" className="text-[18px] font-semibold">{title}</h2>
      <p className="help mt-2">{children}</p>
      <div className="mt-5 flex justify-end gap-2">
        <button type="button" className="btn" onClick={onCancel}>Cancelar</button>
        <button type="button" className="btn btn-danger" onClick={onConfirm}>{confirmLabel}</button>
      </div>
    </dialog>
  );
}
