import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api } from "./api.js";
import { Banner, Toast } from "./components/ui.jsx";
import Grabar from "./pages/Grabar.jsx";
import Sesiones from "./pages/Sesiones.jsx";
import Sesion from "./pages/Sesion.jsx";
import Buscar from "./pages/Buscar.jsx";
import Ajustes from "./pages/Ajustes.jsx";

const PAGES = [
  { path: "grabar", label: "Grabar", icon: "M12 3a3 3 0 00-3 3v6a3 3 0 006 0V6a3 3 0 00-3-3zM6 11a6 6 0 0012 0M12 17v4m-4 0h8", component: Grabar },
  { path: "sesiones", label: "Sesiones", icon: "M4 6h16M4 12h16M4 18h10", component: Sesiones },
  { path: "buscar", label: "Buscar", icon: "M11 4a7 7 0 100 14 7 7 0 000-14zM20 20l-4-4", component: Buscar },
  { path: "ajustes", label: "Ajustes", icon: "M12 8a4 4 0 100 8 4 4 0 000-8zM4 12h2m12 0h2M12 4v2m0 12v2", component: Ajustes },
];

const AppContext = createContext(null);
export const useApp = () => useContext(AppContext);

export function useHashRoute() {
  const read = () => {
    const [path, query] = window.location.hash.replace(/^#\/?/, "").split("?");
    const parts = path.split("/");
    return { page: parts[0] || "grabar", id: parts[1] || null, query: new URLSearchParams(query || "") };
  };
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const onChange = () => setRoute(read());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

function Icon({ d }) {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={d} />
    </svg>
  );
}

export default function App() {
  const route = useHashRoute();
  const [status, setStatus] = useState(null);
  const [error, setError] = useState(null);
  const [toast, setToast] = useState(null);

  const refresh = useCallback(async () => {
    try {
      const next = await api.status();
      setStatus(next);
      setError(null);
      return next;
    } catch (e) {
      setError(e.message);
      return null;
    }
  }, []);
  const busy = !!(status && (status.recording || status.queue_depth > 0));
  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, busy ? 1000 : 5000);
    return () => clearInterval(timer);
  }, [refresh, busy]);

  const notify = useCallback((message) => setToast(message), []);
  const act = useCallback(
    async (fn, okMessage) => {
      try {
        const result = await fn();
        if (okMessage) setToast(okMessage);
        await refresh();
        return result;
      } catch (e) {
        setToast(e.message);
        return null;
      }
    },
    [refresh],
  );
  const stopRecording = useCallback(() => {
    if (!status || !status.recording) return;
    return act(() => api.stop(status.recording.session_id), "Grabación detenida. Transcribiendo…");
  }, [status, act]);
  const value = useMemo(() => ({ status, refresh, notify, act, route, stopRecording }), [status, refresh, notify, act, route, stopRecording]);

  const page = PAGES.find((p) => p.path === route.page) || PAGES[0];
  const Component = page.path === "sesiones" && route.id ? Sesion : page.component;

  return (
    <AppContext.Provider value={value}>
      <div className="min-h-dvh md:grid md:grid-cols-[224px_minmax(0,1fr)]">
        <aside className="sticky top-0 z-10 border-b md:h-dvh md:border-b-0 md:border-r" style={{ background: "var(--sidebar)", borderColor: "var(--line)" }}>
          <div className="flex items-center gap-2 px-4 py-3 md:px-5 md:py-5">
            <span className="grid h-8 w-8 place-items-center rounded-md text-[15px] font-bold text-white" style={{ background: "var(--accent)", fontFamily: "Georgia, serif" }}>S</span>
            <div className="leading-tight">
              <div className="text-[15px] font-semibold">Funes · Audio</div>
              <div className="help text-[11px]">Grabadora y transcripción</div>
            </div>
            <a className="btn-link ml-auto text-[12px]" href="/">Volver a Funes</a>
          </div>
          <nav aria-label="Secciones" className="flex gap-1 overflow-x-auto px-3 pb-2 md:flex-col md:px-3">
            {PAGES.map((p) => (
              <a key={p.path} href={`#/${p.path}`} className="nav-link shrink-0 text-[13px]" aria-current={p.path === page.path ? "page" : undefined}>
                <Icon d={p.icon} />
                {p.label}
              </a>
            ))}
          </nav>
          {status && (
            <div className="hidden px-5 pt-4 md:block">
              <div className="help text-[11px]">Sesiones</div>
              <div className="text-[13px] num font-semibold">{status.sessions_total}</div>
              <div className="help text-[11px] mt-2">Modelo</div>
              <div className="text-[13px]">{status.transcriber.model} · {status.transcriber.device}</div>
            </div>
          )}
        </aside>
        <main className="min-w-0 px-4 py-4 md:px-10 md:py-8">
          {error && (
            <div className="mb-4 rounded-md border p-4 text-[13px]" style={{ background: "var(--danger-bg)", color: "var(--danger-ink)", borderColor: "var(--danger-line)" }} role="alert">
              No se pudo contactar con el módulo de audio: {error}. <button type="button" className="btn-link" onClick={refresh}>Reintentar</button>
            </div>
          )}
          {status && (status.recording || status.queue_depth > 0) && (
            <div className="mb-5">
              <Banner status={status} onStop={stopRecording} />
            </div>
          )}
          {status ? <Component key={`${page.path}/${route.id || ""}`} /> : !error && <p className="help p-8">Cargando…</p>}
        </main>
      </div>
      <Toast message={toast} onClose={() => setToast(null)} />
    </AppContext.Provider>
  );
}
