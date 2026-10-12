// Wiring of copilot-context.ts to this app: the context() the shared launcher calls, plus the React hooks the views use
// to answer form snapshots and to reload their data in place after an assistant turn.
import { useEffect, useRef } from "react";
import { api, isoDay } from "./api";
import {
  buildContext,
  collectEditors,
  getSelectedSpan,
  installCopilot,
  onRefresh,
  provideEditor,
  type EditorSnapshot,
} from "./copilot-context";

export function context() {
  return buildContext({
    hash: window.location.hash,
    today: isoDay(new Date()),
    editors: collectEditors(),
    selectedSpan: getSelectedSpan(),
    getSummary: (day) => api.summary({ day }),
  });
}

export const install = () => installCopilot(context);

/** Reload the open view's data in place when the assistant finishes a turn (never remount, never touch typed fields). */
export function useTurnRefresh(reload: () => void) {
  const latest = useRef(reload);
  latest.current = reload;
  useEffect(() => onRefresh(() => latest.current()), []);
}

/** Answer the launcher's context request with this form's current snapshot (null = nothing to report). */
export function useEditorSnapshot(get: () => EditorSnapshot | null) {
  const latest = useRef(get);
  latest.current = get;
  useEffect(() => provideEditor(() => latest.current()), []);
}
