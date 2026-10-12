// Wiring of copilot-context.js to the audio pages: the context() the shared launcher calls, plus the React hooks the pages
// use to answer form snapshots and to reload their data in place after an assistant turn.
import { useEffect, useRef } from "react";
import { api } from "./api.js";
import { buildContext, collectEditors, installCopilot, onRefresh, provideEditor } from "./copilot-context.js";

export function context() {
  return buildContext({
    hash: window.location.hash,
    editors: collectEditors(),
    getSession: (id) => api.session(id),
    getMinutes: (id) => api.minutes(id),
    getRecording: async () => (await api.status()).recording,
  });
}

export const install = () => installCopilot(context);

/** Reload the open page's data in place when the assistant finishes a turn (never remount, never touch typed fields). */
export function useTurnRefresh(reload) {
  const latest = useRef(reload);
  latest.current = reload;
  useEffect(() => onRefresh(() => latest.current()), []);
}

/** Answer the launcher's context request with this form's current snapshot (null = nothing to report). */
export function useEditorSnapshot(get) {
  const latest = useRef(get);
  latest.current = get;
  useEffect(() => provideEditor(() => latest.current()), []);
}
