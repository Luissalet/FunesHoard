import {
  BookOpen,
  CalendarDays,
  FolderKanban,
  ListChecks,
  Search,
  Settings as SettingsIcon,
  ShieldCheck,
  Sparkles,
  Clock,
} from "lucide-react";
import type { Lang } from "../i18n";
import { STRINGS } from "../i18n";
import { formatClock, type StatusInfo } from "../api";

export type ViewId = "today" | "week" | "search" | "projects" | "files" | "rules" | "privacy" | "assistant" | "settings";

const ITEMS: { id: ViewId; icon: typeof Clock; key: keyof typeof STRINGS["en"] }[] = [
  { id: "today", icon: Clock, key: "nav_today" },
  { id: "week", icon: CalendarDays, key: "nav_week" },
  { id: "search", icon: Search, key: "nav_search" },
  { id: "projects", icon: FolderKanban, key: "nav_projects" },
  { id: "files", icon: BookOpen, key: "nav_files" },
  { id: "rules", icon: ListChecks, key: "nav_rules" },
  { id: "privacy", icon: ShieldCheck, key: "nav_privacy" },
  { id: "assistant", icon: Sparkles, key: "nav_assistant" },
  { id: "settings", icon: SettingsIcon, key: "nav_settings" },
];

export function Sidebar({
  view,
  onNavigate,
  lang,
  status,
}: {
  view: ViewId;
  onNavigate: (v: ViewId) => void;
  lang: Lang;
  status: StatusInfo | null;
}) {
  const t = STRINGS[lang];
  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-mark">
          <Clock size={16} />
        </div>
        <div className="brand-text">
          <h1>{t.appName}</h1>
          <p>{t.tagline}</p>
        </div>
      </div>
      <nav className="nav">
        {ITEMS.map((item) => (
          <button
            key={item.id}
            className={`nav-item ${view === item.id ? "active" : ""}`}
            onClick={() => onNavigate(item.id)}
          >
            <item.icon size={16} />
            {t[item.key]}
          </button>
        ))}
      </nav>
      <div className="sidebar-footer">
        {status && (
          <span className={`pill ${status.paused ? "paused" : "recording"}`}>
            <span className="dot" />
            {status.paused
              ? `${t.paused}${status.paused_until ? ` ${t.until} ${formatClock(status.paused_until, lang)}` : ""}`
              : t.recording}
          </span>
        )}
      </div>
    </aside>
  );
}
