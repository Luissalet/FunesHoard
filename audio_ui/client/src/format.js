const pad = (n) => String(n).padStart(2, "0");

export const KIND_LABEL = { meeting: "Reunión", interview: "Entrevista", note: "Nota de voz", other: "Otro" };
export const STATUS_LABEL = { recording: "Grabando", processing: "Transcribiendo", done: "Lista", failed: "Error" };
export const SPEAKER_LABEL = { yo: "Yo", otros: "Otros", S1: "Voz" };

export function fmtClock(seconds) {
  seconds = Math.max(0, Math.floor(Number(seconds) || 0));
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}

export function fmtDuration(seconds) {
  seconds = Math.round(seconds || 0);
  if (seconds < 60) return `${seconds} s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min`;
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return minutes ? `${hours} h ${minutes} min` : `${hours} h`;
}

export function fmtDateTime(epoch) {
  if (!epoch) return "—";
  const date = new Date(epoch * 1000);
  return date.toLocaleString("es-ES", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function fmtDay(epoch) {
  if (!epoch) return "—";
  return new Date(epoch * 1000).toLocaleDateString("es-ES", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
}

export function fmtBytes(bytes) {
  if (bytes == null) return "—";
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(0)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(1)} GB`;
}

export function localDay(date = new Date()) {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}
