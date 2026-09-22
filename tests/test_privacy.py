from funes_hoard.core.privacy import (
    DEFAULT_EXCLUDE_RULES,
    DEFAULT_REDACT_RULES,
    PauseState,
    apply_privacy,
    migrate_default_rules,
)
from funes_hoard.core.spans import Sample


def sample(app="chrome.exe", title="something"):
    return Sample(ts=0.0, app=app, exe="", title=title, pid=1, idle_s=0.0, locked=False)


def test_excluded_app_is_dropped_entirely():
    s = sample(app="KeePassXC")
    result = apply_privacy(s, DEFAULT_EXCLUDE_RULES, DEFAULT_REDACT_RULES)
    assert result is None


def test_incognito_title_is_dropped_entirely():
    s = sample(app="chrome.exe", title="my search - Incognito")
    result = apply_privacy(s, DEFAULT_EXCLUDE_RULES, DEFAULT_REDACT_RULES)
    assert result is None


def test_spanish_private_browsing_is_dropped():
    s = sample(app="chrome.exe", title="Navegación privada - búsqueda")
    result = apply_privacy(s, DEFAULT_EXCLUDE_RULES, DEFAULT_REDACT_RULES)
    assert result is None


def test_banking_title_is_redacted_not_dropped():
    s = sample(app="chrome.exe", title="My Bank - Online Banking")
    result = apply_privacy(s, DEFAULT_EXCLUDE_RULES, DEFAULT_REDACT_RULES)
    assert result is not None
    assert result.app == "chrome.exe"
    assert result.title == "[redacted]"


def test_normal_title_passes_through_unchanged():
    s = sample(app="Code.exe", title="main.py - Faustus - Visual Studio Code")
    result = apply_privacy(s, DEFAULT_EXCLUDE_RULES, DEFAULT_REDACT_RULES)
    assert result == s


def test_pause_state_is_paused_until_expiry():
    p = PauseState().pause_for(now=1000.0, minutes=15)
    assert p.is_paused(1000.0)
    assert p.is_paused(1000.0 + 14 * 60)
    assert not p.is_paused(1000.0 + 15 * 60)


def test_pause_resumes_automatically_after_expiry():
    p = PauseState().pause_for(now=1000.0, minutes=1)
    resumed = p.resumed_if_expired(now=1000.0 + 61)
    assert resumed.paused_until is None
    assert not resumed.is_paused(1000.0 + 61)


def test_pause_does_not_resume_early():
    p = PauseState().pause_for(now=1000.0, minutes=15)
    still_paused = p.resumed_if_expired(now=1000.0 + 60)
    assert still_paused.is_paused(1000.0 + 60)


# --- B1 regression: the Spanish incognito new-tab title, and any other
# capitalisation of a default rule, must be excluded/redacted -------------
def test_spanish_incognito_new_tab_is_dropped_entirely():
    s = sample(app="chrome.exe", title="Nueva pestaña de incógnito - Google Chrome")
    result = apply_privacy(s, DEFAULT_EXCLUDE_RULES, DEFAULT_REDACT_RULES)
    assert result is None


def test_incognito_title_is_dropped_regardless_of_case():
    for title in ("my search - INCOGNITO", "my search - incognito", "Incógnito - vault"):
        assert apply_privacy(sample(title=title), DEFAULT_EXCLUDE_RULES, DEFAULT_REDACT_RULES) is None


def test_login_title_in_spanish_is_redacted():
    s = sample(app="chrome.exe", title="Iniciar sesión - Mi Banco")
    result = apply_privacy(s, DEFAULT_EXCLUDE_RULES, DEFAULT_REDACT_RULES)
    assert result is not None
    assert result.title == "[redacted]"


def test_migrate_default_rules_upgrades_only_unmodified_defaults():
    existing = [
        {"id": 1, "kind": "exclude", "match_type": "title_regex", "pattern": "Incognito"},
        {"id": 2, "kind": "exclude", "match_type": "title_regex", "pattern": "my custom rule"},
    ]
    plan = migrate_default_rules(existing)
    assert (1, r"(?i)Incognito") in plan["renames"]
    assert all(rid != 2 for rid, _ in plan["renames"])
    added_patterns = {r.pattern for r in plan["adds"]}
    assert r"(?i)inc[oó]gnito" in added_patterns
    assert r"(?i)iniciar sesi[oó]n" in added_patterns


def test_migrate_default_rules_is_a_noop_on_an_already_current_database():
    existing = [
        {"id": r.id, "kind": r.kind, "match_type": r.match_type, "pattern": r.pattern}
        for r in [*DEFAULT_EXCLUDE_RULES, *DEFAULT_REDACT_RULES]
    ]
    plan = migrate_default_rules(existing)
    assert plan == {"renames": [], "adds": []}
