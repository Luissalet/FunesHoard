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


_VSCODE_NAMES = {"visual studio code", "vscode"}
_NOT_A_FOLDER = {"welcome", "settings", "keyboard shortcuts", "release notes", "get started"}
_JETBRAINS_APPS = ("pycharm", "idea", "webstorm", "rider", "clion", "goland", "phpstorm", "rustrover", "datagrip")
_BRACKET_SUFFIX_RE = re.compile(r"\s*(\[[^\]]*\]|\((workspace|running|debugging|administrator)\))\s*$", re.IGNORECASE)


def _clean_name(part: str) -> Optional[str]:
    name = part.strip().lstrip("●").strip()  # VS Code puts a dot before dirty files
    prev = None
    while prev != name:
        prev = name
        name = _BRACKET_SUFFIX_RE.sub("", name).strip()
    return name or None


def _vscode_project(title: str) -> Optional[str]:
    """`file - <folder> - Visual Studio Code[ - Insiders]` -> folder.

    Splits on the spaced separator only, so hyphenated folder names like
    `funes-hoard` survive; the folder is always the part right before the
    product name, however many dashes the file name contains.
    """
    parts = title.split(" - ")
    idx = next((i for i in range(len(parts) - 1, -1, -1) if parts[i].strip().lower() in _VSCODE_NAMES), None)
    if idx is None or idx == 0:
        return None
    if idx >= 2:
        return _clean_name(parts[idx - 1])
    # "<something> - Visual Studio Code": a folder with no file open, or a
    # lone file / editor tab with no folder. Only the former is a project.
    only = _clean_name(parts[0])
    if not only or "." in only or only.lower() in _NOT_A_FOLDER or only.lower().startswith("untitled"):
        return None
    return only


def _jetbrains_project(title: str) -> Optional[str]:
    """`<project> – file` or `<project> – [path] – file` (en dash; older builds use '-')."""
    sep = " – " if " – " in title else " - "
    first = title.split(sep, 1)[0]
    if first == title:
        return None
    return _clean_name(first)


def _visual_studio_project(title: str) -> Optional[str]:
    marker = " - Microsoft Visual Studio"
    if marker not in title:
        return None
    return _clean_name(title.split(marker, 1)[0])


def _repo_in_title(title: str, known_repo_names: List[str]) -> Optional[str]:
    # Longest names first so "funes-hoard" wins over a repo called "hoard";
    # word-ish boundaries so a repo called "api" does not match "rapid".
    for repo in sorted({r for r in known_repo_names if r}, key=len, reverse=True):
        if re.search(r"(?<![\w-])" + re.escape(repo) + r"(?![\w-])", title, re.IGNORECASE):
            return repo
    return None


def detect_project(title: str, known_repo_names: List[str], app: str = "") -> Optional[str]:
    """Best-effort project name extraction from an editor window title,
    falling back to any known git repo name that appears in the title."""
    if not title:
        return None
    app_l = (app or "").lower()
    project = _vscode_project(title)
    if project is None and any(name in app_l for name in _JETBRAINS_APPS):
        project = _jetbrains_project(title)
    if project is None and "devenv" in app_l:
        project = _visual_studio_project(title)
    if project:
        return project
    return _repo_in_title(title, known_repo_names)


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
        project = detect_project(title, known_repo_names, app=f"{app} {exe}")
    return category, project
