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
import { api, type StatusInfo } from "./api";
import { detectLang, STRINGS, type Lang } from "./i18n";

const TITLES: Record<ViewId, keyof typeof STRINGS["en"]> = {
  today: "nav_today",
  week: "nav_week",
  search: "nav_search",
  projects: "nav_projects",
  files: "nav_files",
  rules: "nav_rules",
  privacy: "nav_privacy",
  assistant: "nav_assistant",
  settings: "nav_settings",
};

function loadTheme(): ThemeMode {
  try {
    return (localStorage.getItem("funes-theme") as ThemeMode) || "system";
  } catch {
    return "system";
  }
}

export default function App() {
  const [view, setView] = useState<ViewId>("today");
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

  const t = STRINGS[lang];

  return (
    <div className="layout">
      <Sidebar view={view} onNavigate={setView} lang={lang} status={status} />
      <div className="main">
        <Header title={t[TITLES[view]]} lang={lang} onLangChange={setLang} theme={theme} onThemeChange={setTheme} />
        <div className="content">
          {view === "today" && <TodayView lang={lang} />}
          {view === "week" && <WeekView lang={lang} />}
          {view === "search" && <SearchView lang={lang} />}
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
