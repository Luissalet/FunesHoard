import { useEffect, useRef, useState } from "react";
import { api } from "./api.js";

/** Subscribe to a session's SSE stream. Returns { segments, status } and appends as they arrive; `onDone` fires once. */
export function useLive(sessionId, enabled, onDone) {
  const [segments, setSegments] = useState([]);
  const [status, setStatus] = useState(null);
  const done = useRef(onDone);
  done.current = onDone;

  useEffect(() => {
    if (!sessionId || !enabled) return undefined;
    setSegments([]);
    const source = new EventSource(api.liveUrl(sessionId));
    source.addEventListener("segment", (e) => {
      const seg = JSON.parse(e.data);
      setSegments((prev) => (prev.some((p) => p.id === seg.id) ? prev : [...prev, seg].sort((a, b) => a.start_s - b.start_s || a.id - b.id)));
    });
    source.addEventListener("status", (e) => setStatus(JSON.parse(e.data).status));
    source.addEventListener("done", (e) => {
      const data = JSON.parse(e.data);
      setStatus(data.status);
      source.close();
      if (done.current) done.current(data);
    });
    source.onerror = () => {
      /* the browser retries on its own; a finished session closes with `done` */
    };
    return () => source.close();
  }, [sessionId, enabled]);

  return { segments, status };
}
