"""Classification rules: (app, title) -> (category, project).

Ordered, first-match rules. Ships sensible defaults for common desktop
apps. Project auto-detection reads the workspace/solution name out of
editor window titles and matches known git repo names inside any title.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

CATEGORIES = [
    "Coding", "Writing", "Reading", "Communication", "Meetings",
    "Browsing", "Design", "Media", "Games", "System", "Other",
]


@dataclass
class ClassifyRule:
    id: int
    order_idx: int
    match_type: str  # "app" | "title_regex" | "domain"
    pattern: str
    category: str
    project: Optional[str] = None
    enabled: bool = True


def default_rules() -> List[ClassifyRule]:
    rows = [
        ("app", "Code.exe", "Coding", None),
        ("app", "devenv.exe", "Coding", None),
        ("app", "pycharm", "Coding", None),
        ("app", "idea64.exe", "Coding", None),
        ("app", "WindowsTerminal.exe", "Coding", None),
        ("app", "cmd.exe", "Coding", None),
        ("app", "powershell.exe", "Coding", None),
        ("app", "WINWORD.EXE", "Writing", None),
        ("app", "notepad", "Writing", None),
        ("app", "Obsidian.exe", "Writing", None),
        ("app", "EXCEL.EXE", "Reading", None),
        ("app", "AcroRd32.exe", "Reading", None),
        ("app", "SumatraPDF.exe", "Reading", None),
        ("app", "Discord.exe", "Communication", None),
        ("app", "WhatsApp.exe", "Communication", None),
        ("app", "Teams.exe", "Meetings", None),
        ("app", "ms-teams.exe", "Meetings", None),
        ("app", "Zoom.exe", "Meetings", None),
        ("app", "Slack.exe", "Communication", None),
        ("app", "Outlook.exe", "Communication", None),
        ("app", "Spotify.exe", "Media", None),
        ("app", "vlc.exe", "Media", None),
        ("app", "steam.exe", "Games", None),
        ("app", "steamwebhelper.exe", "Games", None),
        ("app", "chrome.exe", "Browsing", None),
        ("app", "msedge.exe", "Browsing", None),
        ("app", "brave.exe", "Browsing", None),
        ("app", "firefox.exe", "Browsing", None),
        ("app", "Figma.exe", "Design", None),
        ("app", "Photoshop.exe", "Design", None),
        ("app", "LockApp.exe", "System", None),
        ("app", "explorer.exe", "System", None),
    ]
    return [
        ClassifyRule(id=-(i + 1), order_idx=i, match_type=mt, pattern=p, category=c, project=proj)
        for i, (mt, p, c, proj) in enumerate(rows)
    ]


# "file.py - Faustus - Visual Studio Code" -> project "Faustus"
_VSCODE_TITLE_RE = re.compile(r"^.*\s-\s(?P<project>[^-]+?)\s-\s(Visual Studio Code|VSCode)\s*$")
# JetBrains: "main.py - Faustus" or "Faustus - [main.py]"
_JETBRAINS_TITLE_RE = re.compile(r"^(?P<a>.+?)\s-\s(?P<b>.+)$")


def detect_project(title: str, known_repo_names: List[str]) -> Optional[str]:
    """Best-effort project name extraction from an editor window title."""
    if not title:
        return None
    m = _VSCODE_TITLE_RE.match(title)
    if m:
        return m.group("project").strip()
    for repo in known_repo_names:
        if repo and re.search(re.escape(repo), title, re.IGNORECASE):
            return repo
    return None


def classify(
    app: str,
    exe: str,
    title: str,
    rules: List[ClassifyRule],
    known_repo_names: Optional[List[str]] = None,
) -> Tuple[str, Optional[str]]:
    """Apply ordered rules; first enabled match wins. Falls back to Other."""
    known_repo_names = known_repo_names or []
    category = "Other"
    project: Optional[str] = None
    haystack_app = f"{app} {exe}".lower()
    for rule in sorted(rules, key=lambda r: r.order_idx):
        if not rule.enabled:
            continue
        matched = False
        if rule.match_type == "app":
            matched = rule.pattern.strip().lower() in haystack_app
        elif rule.match_type == "title_regex":
            try:
                matched = re.search(rule.pattern, title or "") is not None
            except re.error:
                matched = False
        elif rule.match_type == "domain":
            matched = rule.pattern.strip().lower() in (title or "").lower()
        if matched:
            category = rule.category
            project = rule.project
            break
    if project is None:
        project = detect_project(title, known_repo_names)
    return category, project
