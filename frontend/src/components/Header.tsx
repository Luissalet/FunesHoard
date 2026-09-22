import { Globe, Moon, Sun, SunMoon } from "lucide-react";
import type { Lang } from "../i18n";

export type ThemeMode = "system" | "light" | "dark";

export function Header({
  title,
  lang,
  onLangChange,
  theme,
  onThemeChange,
}: {
  title: string;
  lang: Lang;
  onLangChange: (l: Lang) => void;
  theme: ThemeMode;
  onThemeChange: (t: ThemeMode) => void;
}) {
  const nextTheme: Record<ThemeMode, ThemeMode> = { system: "light", light: "dark", dark: "system" };
  const ThemeIcon = theme === "light" ? Sun : theme === "dark" ? Moon : SunMoon;
  return (
    <header className="header">
      <div className="header-title">
        <img src="/favicon-192.png" width={28} height={28} alt="" className="header-icon" />
        <h2>{title}</h2>
      </div>
      <div className="header-right">
        <button className="icon-button" title="Language" onClick={() => onLangChange(lang === "en" ? "es" : "en")}>
          <Globe size={16} />
        </button>
        <button className="icon-button" title="Theme" onClick={() => onThemeChange(nextTheme[theme])}>
          <ThemeIcon size={16} />
        </button>
      </div>
    </header>
  );
}
