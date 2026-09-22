"""Exclusion and redaction rules, applied *before* anything is stored.

Pure functions over plain data so they can be unit-tested without a
database: `apply_privacy(sample, exclude_rules, redact_rules)` returns
either None (drop entirely) or a possibly-redacted sample.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Tuple

from funes_hoard.core.spans import Sample


@dataclass
class PrivacyRule:
    id: int
    kind: str  # "exclude" | "redact"
    match_type: str  # "app" | "title_regex"
    pattern: str
    enabled: bool = True


# Every default title regex is `(?i)` (case-insensitive) and, where the word
# has one, accent-tolerant (`[oó]`, `[nñ]`): Chrome's own Spanish incognito
# title is "Nueva pestaña de incógnito", which a case-sensitive `Incognito`
# never matches. See `PRIVACY_DEFAULTS_VERSION` below for how an existing
# database picks up fixes to these patterns.
DEFAULT_EXCLUDE_RULES: List[PrivacyRule] = [
    PrivacyRule(id=-1, kind="exclude", match_type="app", pattern="KeePass"),
    PrivacyRule(id=-2, kind="exclude", match_type="app", pattern="KeePassXC"),
    PrivacyRule(id=-3, kind="exclude", match_type="app", pattern="1Password"),
    PrivacyRule(id=-4, kind="exclude", match_type="app", pattern="Bitwarden"),
    PrivacyRule(id=-5, kind="exclude", match_type="title_regex", pattern=r"(?i)InPrivate"),
    PrivacyRule(id=-6, kind="exclude", match_type="title_regex", pattern=r"(?i)Incognito"),
    PrivacyRule(id=-7, kind="exclude", match_type="title_regex", pattern=r"(?i)inc[oó]gnito"),
    PrivacyRule(id=-8, kind="exclude", match_type="title_regex", pattern=r"(?i)Private Browsing"),
    PrivacyRule(id=-9, kind="exclude", match_type="title_regex", pattern=r"(?i)Navegaci[oó]n privada"),
]

DEFAULT_REDACT_RULES: List[PrivacyRule] = [
    PrivacyRule(id=-101, kind="redact", match_type="title_regex", pattern=r"(?i)\bbank\b"),
    PrivacyRule(id=-102, kind="redact", match_type="title_regex", pattern=r"(?i)\bbanco\b"),
    PrivacyRule(id=-103, kind="redact", match_type="title_regex", pattern=r"(?i)\blogin\b"),
    PrivacyRule(id=-104, kind="redact", match_type="title_regex", pattern=r"(?i)contrase[nñ]a"),
    PrivacyRule(id=-105, kind="redact", match_type="title_regex", pattern=r"(?i)\bpassword\b"),
    PrivacyRule(id=-106, kind="redact", match_type="title_regex", pattern=r"(?i)iniciar sesi[oó]n"),
]

# Bump this whenever a DEFAULT_*_RULES pattern is fixed or added: `db.py`
# uses it to bring an existing database's still-unmodified defaults up to
# date (see `migrate_default_rules`), without touching anything the user
# edited or removed themselves.
PRIVACY_DEFAULTS_VERSION = 2

# (kind, match_type, old pattern) -> new pattern, for a default whose text
# changed in a later version (case/accent fixes). Only a row whose pattern
# still matches the OLD text verbatim is touched, so a rule the user edited
# to something else is left alone.
_DEFAULT_RULE_RENAMES: Dict[Tuple[str, str, str], str] = {
    ("exclude", "title_regex", "InPrivate"): r"(?i)InPrivate",
    ("exclude", "title_regex", "Incognito"): r"(?i)Incognito",
    ("exclude", "title_regex", "Private Browsing"): r"(?i)Private Browsing",
    ("exclude", "title_regex", r"Navegaci[oó]n privada"): r"(?i)Navegaci[oó]n privada",
}

# Defaults introduced outright in a later version (not a rename of an older
# one): added only when no rule already has that exact pattern.
_DEFAULT_RULES_ADDED_SINCE_V1: List[PrivacyRule] = [
    PrivacyRule(id=-7, kind="exclude", match_type="title_regex", pattern=r"(?i)inc[oó]gnito"),
    PrivacyRule(id=-106, kind="redact", match_type="title_regex", pattern=r"(?i)iniciar sesi[oó]n"),
]


def migrate_default_rules(existing: List[dict]) -> Dict[str, list]:
    """Bring a database's default privacy rules up to `PRIVACY_DEFAULTS_VERSION`.

    `existing` is every row currently in `privacy_rules` (dicts with at
    least id/kind/match_type/pattern). Returns
    `{"renames": [(id, new_pattern), ...], "adds": [PrivacyRule, ...]}` --
    pure data, so `db.py` just applies it and callers can unit-test the
    decision without a database.
    """
    renames = []
    for row in existing:
        key = (row["kind"], row["match_type"], row["pattern"])
        new_pattern = _DEFAULT_RULE_RENAMES.get(key)
        if new_pattern:
            renames.append((row["id"], new_pattern))
    existing_patterns = {(row["kind"], row["match_type"], row["pattern"]) for row in existing}
    adds = [r for r in _DEFAULT_RULES_ADDED_SINCE_V1 if (r.kind, r.match_type, r.pattern) not in existing_patterns]
    return {"renames": renames, "adds": adds}


def _rule_matches(rule: PrivacyRule, sample: Sample) -> bool:
    if not rule.enabled:
        return False
    if rule.match_type == "app":
        return rule.pattern.strip().lower() in (sample.app or "").lower()
    if rule.match_type == "title_regex":
        try:
            return re.search(rule.pattern, sample.title or "") is not None
        except re.error:
            return False
    return False


def apply_privacy(
    sample: Sample,
    exclude_rules: List[PrivacyRule],
    redact_rules: List[PrivacyRule],
) -> Optional[Sample]:
    """Return None to drop the sample entirely, or a (possibly redacted) copy."""
    for rule in exclude_rules:
        if _rule_matches(rule, sample):
            return None
    for rule in redact_rules:
        if _rule_matches(rule, sample):
            return replace(sample, title="[redacted]")
    return sample


@dataclass
class PauseState:
    paused_until: Optional[float] = None

    def is_paused(self, now: float) -> bool:
        return self.paused_until is not None and now < self.paused_until

    def pause_for(self, now: float, minutes: float) -> "PauseState":
        return PauseState(paused_until=now + minutes * 60.0)

    def resumed_if_expired(self, now: float) -> "PauseState":
        if self.paused_until is not None and now >= self.paused_until:
            return PauseState(paused_until=None)
        return self
