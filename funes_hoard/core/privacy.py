"""Exclusion and redaction rules, applied *before* anything is stored.

Pure functions over plain data so they can be unit-tested without a
database: `apply_privacy(sample, exclude_rules, redact_rules)` returns
either None (drop entirely) or a possibly-redacted sample.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import List, Optional

from funes_hoard.core.spans import Sample


@dataclass
class PrivacyRule:
    id: int
    kind: str  # "exclude" | "redact"
    match_type: str  # "app" | "title_regex"
    pattern: str
    enabled: bool = True


DEFAULT_EXCLUDE_RULES: List[PrivacyRule] = [
    PrivacyRule(id=-1, kind="exclude", match_type="app", pattern="KeePass"),
    PrivacyRule(id=-2, kind="exclude", match_type="app", pattern="KeePassXC"),
    PrivacyRule(id=-3, kind="exclude", match_type="app", pattern="1Password"),
    PrivacyRule(id=-4, kind="exclude", match_type="app", pattern="Bitwarden"),
    PrivacyRule(id=-5, kind="exclude", match_type="title_regex", pattern=r"InPrivate"),
    PrivacyRule(id=-6, kind="exclude", match_type="title_regex", pattern=r"Incognito"),
    PrivacyRule(id=-7, kind="exclude", match_type="title_regex", pattern=r"Private Browsing"),
    PrivacyRule(id=-8, kind="exclude", match_type="title_regex", pattern=r"Navegaci[oó]n privada"),
]

DEFAULT_REDACT_RULES: List[PrivacyRule] = [
    PrivacyRule(id=-101, kind="redact", match_type="title_regex", pattern=r"(?i)\bbank\b"),
    PrivacyRule(id=-102, kind="redact", match_type="title_regex", pattern=r"(?i)\bbanco\b"),
    PrivacyRule(id=-103, kind="redact", match_type="title_regex", pattern=r"(?i)\blogin\b"),
    PrivacyRule(id=-104, kind="redact", match_type="title_regex", pattern=r"(?i)contrase[nñ]a"),
    PrivacyRule(id=-105, kind="redact", match_type="title_regex", pattern=r"(?i)\bpassword\b"),
]


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
