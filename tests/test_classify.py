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
