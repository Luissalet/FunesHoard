from funes_hoard.core.classify import ClassifyRule, classify, default_rules, detect_project


def test_default_rules_classify_vscode_as_coding():
    cat, proj = classify("Code.exe", "C:/x/Code.exe", "main.py - Faustus - Visual Studio Code", default_rules())
    assert cat == "Coding"
    assert proj == "Faustus"


def test_default_rules_classify_spotify_as_media():
    cat, _ = classify("Spotify.exe", "", "Focus playlist", default_rules())
    assert cat == "Media"


def test_default_rules_classify_teams_as_meetings():
    cat, _ = classify("Teams.exe", "", "Weekly sync", default_rules())
    assert cat == "Meetings"


def test_unknown_app_falls_back_to_other():
    cat, proj = classify("SomeRandomApp.exe", "", "random title", default_rules())
    assert cat == "Other"
    assert proj is None


def test_project_detected_from_known_repo_name_in_title():
    cat, proj = classify("chrome.exe", "", "Issue #12 - laplaces-hoard - GitHub", default_rules(), known_repo_names=["laplaces-hoard"])
    assert proj == "laplaces-hoard"


def test_detect_project_from_vscode_title_pattern():
    assert detect_project("api.py - Atlas - Visual Studio Code", []) == "Atlas"


def test_detect_project_none_when_no_pattern_matches():
    assert detect_project("Untitled - Notepad", []) is None


def test_first_match_wins_ordering():
    rules = [
        ClassifyRule(id=1, order_idx=0, match_type="app", pattern="chrome", category="Browsing"),
        ClassifyRule(id=2, order_idx=1, match_type="title_regex", pattern="GitHub", category="Coding"),
    ]
    cat, _ = classify("chrome.exe", "", "some repo - GitHub", rules)
    assert cat == "Browsing"  # first rule (app match) wins even though title also matches rule 2


def test_disabled_rule_is_skipped():
    rules = [
        ClassifyRule(id=1, order_idx=0, match_type="app", pattern="chrome", category="Browsing", enabled=False),
    ]
    cat, _ = classify("chrome.exe", "", "anything", rules)
    assert cat == "Other"


# --- review regressions ----------------------------------------------------
def test_vscode_project_with_hyphen_in_name():
    # The user's repos are all slugs like funes-hoard / babels-hoard.
    assert detect_project("api.py - funes-hoard - Visual Studio Code", []) == "funes-hoard"


def test_vscode_dirty_marker_remote_suffix_and_insiders():
    assert detect_project("● main.py - Atlas - Visual Studio Code", []) == "Atlas"
    assert detect_project("main.py - Atlas [WSL: Ubuntu] - Visual Studio Code", []) == "Atlas"
    assert detect_project("main.py - Atlas (Workspace) - Visual Studio Code - Insiders", []) == "Atlas"


def test_vscode_folder_only_title():
    assert detect_project("Atlas - Visual Studio Code", []) == "Atlas"
    assert detect_project("Welcome - Visual Studio Code", []) is None
    assert detect_project("settings.json - Visual Studio Code", []) is None


def test_file_name_with_dashes_does_not_become_the_project():
    assert detect_project("my - notes.md - Atlas - Visual Studio Code", []) == "Atlas"


def test_jetbrains_titles():
    assert classify("pycharm64.exe", "", "Atlas – main.py", default_rules())[1] == "Atlas"
    assert classify("idea64.exe", "", "lumen-core – [C:\\src\\lumen-core] – Main.java", default_rules())[1] == "lumen-core"


def test_visual_studio_solution_title():
    assert classify("devenv.exe", "", "Atlas - Microsoft Visual Studio", default_rules())[1] == "Atlas"


def test_repo_name_matching_needs_word_boundaries():
    # A repo called "api" must not claim every title that contains "rapid".
    assert detect_project("Rapid prototyping - Google Chrome", ["api"]) is None
    assert detect_project("api - pull request #3", ["api"]) == "api"


def test_known_repo_names_come_from_discovered_repos_not_the_root_folder(tmp_path):
    from funes_hoard.collector import _known_repo_names
    from funes_hoard.db import Database

    import time

    db = Database(tmp_path)
    db.execute("INSERT INTO commit_repos(path, enabled) VALUES (?, 1)", ("C:\\Users\\me\\Desktop\\Side projects",))
    db.execute(
        "INSERT INTO commits(ts, repo, sha, subject, author) VALUES (?, 'funes-hoard', 'abc', 's', 'a')",
        (time.time(),),
    )
    db.set_meta("known_repos", '["babels-hoard"]')
    names = _known_repo_names(db)
    assert "Side projects" not in names
    assert set(names) == {"funes-hoard", "babels-hoard"}
