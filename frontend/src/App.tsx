import { useEffect, useState, useCallback } from "react";
import { Header, type ThemeMode } from "./components/Header";
import { Sidebar, type ViewId } from "./components/Sidebar";
import { TodayView } from "./components/TodayView";
import { WeekView } from "./components/WeekView";
import { SearchView } from "./components/SearchView";
import { ProjectsView } from "./components/ProjectsView";
import { FilesCommitsView } from "./components/FilesCommitsView";
import { RulesView } from "./components/RulesView";
import { PrivacyView } from "./components/PrivacyView";
import { AssistantActivityView } from "./components/AssistantActivityView";
import { SettingsView } from "./components/SettingsView";
import { RecallTimelineView } from "./components/RecallTimelineView";
import { api, isoDay, type StatusInfo } from "./api";
import { detectLang, STRINGS, type Lang } from "./i18n";

const TITLES: Record<ViewId, keyof typeof STRINGS["en"]> = {
  today: "nav_today",
  timeline: "nav_timeline",
  week: "nav_week",
  search: "nav_search",
  projects: "nav_projects",
  files: "nav_files",
  rules: "nav_rules",
  privacy: "nav_privacy",
  assistant: "nav_assistant",
  settings: "nav_settings",
};

const VIEWS = Object.keys(TITLES) as ViewId[];

/**
 * The address says where you are, so reload and Back work and a search hit
 * can open a moment: `#/today`, `#/today/2026-09-21`, `#/today/2026-09-21@1790000000`
 * (a moment to pin in that day's timeline), `#/search`, ...
 */
export interface Route {
  view: ViewId;
  day?: string;
  at?: number;
}

function parseHash(hash: string): Route {
  const [view, day] = hash.replace(/^#\/?/, "").split("/");
  if (!VIEWS.includes(view as ViewId)) return { view: "today" };
  if (view !== "today" || !day) return { view: view as ViewId };
  const [d, at] = day.split("@");
  return { view: "today", day: /^\d{4}-\d{2}-\d{2}$/.test(d) ? d : undefined, at: at ? Number(at) || undefined : undefined };
}

function routeHash(r: Route): string {
  if (r.view !== "today" || !r.day) return `#/${r.view}`;
  return `#/today/${r.day}${r.at ? `@${Math.round(r.at)}` : ""}`;
}

function loadTheme(): ThemeMode {
  try {
    return (localStorage.getItem("funes-theme") as ThemeMode) || "system";
  } catch {
    return "system";
  }
}

export default function App() {
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash));
  const view = route.view;
  const [lang, setLang] = useState<Lang>(detectLang());
  const [theme, setTheme] = useState<ThemeMode>(loadTheme());
  const [status, setStatus] = useState<StatusInfo | null>(null);

  const refreshStatus = useCallback(() => {
    api.status().then(setStatus).catch(() => {});
  }, []);

  useEffect(() => {
    refreshStatus();
    const id = setInterval(refreshStatus, 15000);
    return () => clearInterval(id);
  }, [refreshStatus]);

  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
    try {
      localStorage.setItem("funes-theme", theme);
    } catch {
      /* ignore */
    }
  }, [theme]);

  useEffect(() => {
    const onHash = () => setRoute(parseHash(window.location.hash));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const navigate = useCallback((next: Route) => {
    const hash = routeHash(next);
    if (window.location.hash !== hash) window.location.hash = hash;
    else setRoute(next);
  }, []);

  const t = STRINGS[lang];
  const today = isoDay(new Date());
  const yesterday = isoDay(new Date(Date.now() - 86400000));
  const day = route.day ?? today;
  // C3: the header said "Today" while showing any other day.
  const title =
    view !== "today" || day === today ? t[TITLES[view]] : day === yesterday ? t.day_picker_yesterday : t.nav_day;

  return (
    <div className="layout">
      <Sidebar view={view} onNavigate={(v) => navigate({ view: v })} lang={lang} status={status} />
      <div className="main">
        <Header title={title} lang={lang} onLangChange={setLang} theme={theme} onThemeChange={setTheme} />
        <div className="content">
          {view === "today" && (
            <TodayView
              lang={lang}
              day={day}
              focusTs={route.at}
              onNavigate={(d, at) => navigate({ view: "today", day: d === today && !at ? undefined : d, at })}
            />
          )}
          {view === "timeline" && <RecallTimelineView lang={lang} />}
          {view === "week" && <WeekView lang={lang} />}
          {view === "search" && (
            <SearchView lang={lang} onOpenMoment={(d, at) => navigate({ view: "today", day: d, at })} />
          )}
          {view === "projects" && <ProjectsView lang={lang} />}
          {view === "files" && <FilesCommitsView lang={lang} />}
          {view === "rules" && <RulesView lang={lang} />}
          {view === "privacy" && <PrivacyView lang={lang} status={status} onStatusChange={refreshStatus} />}
          {view === "assistant" && <AssistantActivityView lang={lang} />}
          {view === "settings" && <SettingsView lang={lang} />}
        </div>
      </div>
    </div>
  );
}
