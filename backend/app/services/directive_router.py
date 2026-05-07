"""DirectiveRouter — turn a user's free-text instruction into a TargetSelector.

Recognised patterns (case-insensitive):
* `@handle`                          →  by_handle
* `all <plugin>` / `every <plugin>`  →  all_of_platforms
* `to <plugin>` / `<plugin> only`    →  all_of_platforms
* `tag:<name>` / `to <name> accounts`→  tagged
* `not <plugin>` / `except <plugin>` →  exclude_plugins

Plugin synonyms are mapped to canonical names (e.g. "ig" → "instagram",
"x" / "twitter" → "twitter", "fb" → "facebook"). The router never *invents*
targeting — if nothing matches, it returns an empty selector so the
TargetResolver falls back to the workflow defaults.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.value_objects.targeting import TargetSelector


PLUGIN_SYNONYMS: dict[str, str] = {
    # canonical names accepted directly + common aliases
    "linkedin": "linkedin", "li": "linkedin", "linked-in": "linkedin",
    "twitter": "twitter", "x": "twitter", "x.com": "twitter", "tweet": "twitter",
    "facebook": "facebook", "fb": "facebook", "meta": "facebook",
    "instagram": "instagram", "ig": "instagram", "insta": "instagram", "gram": "instagram",
    "youtube": "youtube", "yt": "youtube",
    "tiktok": "tiktok", "tt": "tiktok",
    "threads": "threads",
    "pinterest": "pinterest", "pin": "pinterest",
    "reddit": "reddit", "rd": "reddit",
    "mastodon": "mastodon",
    "bluesky": "bluesky", "bsky": "bluesky",
    "medium": "medium",
    "discord": "discord",
    "slack": "slack",
    "telegram": "telegram", "tg": "telegram",
    "tumblr": "tumblr",
}

# Phrases that signal "fan out across every connected account for this plugin"
ALL_PHRASES = (
    r"all\s+(?:my\s+|of\s+my\s+)?",
    r"every\s+(?:of\s+my\s+|my\s+)?",
    r"all\s+",
    r"every\s+",
)
ALL_RE = re.compile(
    rf"\b(?:{'|'.join(ALL_PHRASES)})({'|'.join(re.escape(k) for k in PLUGIN_SYNONYMS)})"
    r"(?:\s+(?:accounts|pages|profiles|channels|account|page|profile|channel))?\b",
    re.IGNORECASE,
)
# A plugin name followed by an account/page/profile/channel context word —
# catches conjunctions like "every IG and FB account".
CONTEXTUAL_RE = re.compile(
    rf"\b({'|'.join(re.escape(k) for k in PLUGIN_SYNONYMS)})\s+"
    r"(?:accounts|pages|profiles|channels|account|page|profile|channel)\b",
    re.IGNORECASE,
)
ONLY_RE = re.compile(
    rf"\b(?:to|on)\s+({'|'.join(re.escape(k) for k in PLUGIN_SYNONYMS)})\b",
    re.IGNORECASE,
)
ONLY_TRAIL_RE = re.compile(
    rf"\b({'|'.join(re.escape(k) for k in PLUGIN_SYNONYMS)})\s+only\b",
    re.IGNORECASE,
)
EXCEPT_RE = re.compile(
    rf"\b(?:not|except)\s+({'|'.join(re.escape(k) for k in PLUGIN_SYNONYMS)})\b",
    re.IGNORECASE,
)
# Conjunction: "X, Y and Z" — only applied once a targeting intent has been
# detected, to avoid spurious matches in casual conversation.
CONJUNCTION_RE = re.compile(
    rf"(?:,|\band|\bor)\s+({'|'.join(re.escape(k) for k in PLUGIN_SYNONYMS)})\b",
    re.IGNORECASE,
)
HANDLE_RE = re.compile(r"(?<![\w/])@([A-Za-z0-9_.\-]{2,40})")
EXPLICIT_TAG_RE = re.compile(r"\btag:([\w\-]+)\b", re.IGNORECASE)
# "to my marketing accounts" / "to all my marketing accounts" / "all my brand pages"
TO_TAGGED_RE = re.compile(
    r"\b(?:to|on|all|every)\s+(?:all\s+)?(?:my\s+|of\s+my\s+|the\s+)?"
    r"([\w\-]+)\s+(?:accounts|pages|profiles|channels)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DirectiveRouter:
    """Stateless parser. Subclass / replace if you want LLM-assisted routing."""

    def parse(self, directive: str) -> TargetSelector:
        if not directive:
            return TargetSelector()

        plugins: set[str] = set()
        for m in ALL_RE.finditer(directive):
            plugins.add(PLUGIN_SYNONYMS[m.group(1).lower()])
        for m in ONLY_RE.finditer(directive):
            plugins.add(PLUGIN_SYNONYMS[m.group(1).lower()])
        for m in ONLY_TRAIL_RE.finditer(directive):
            plugins.add(PLUGIN_SYNONYMS[m.group(1).lower()])
        # Catches "IG and FB account", "FB pages", "linkedin profiles", ...
        for m in CONTEXTUAL_RE.finditer(directive):
            plugins.add(PLUGIN_SYNONYMS[m.group(1).lower()])
        # If the directive already expresses targeting intent (we've matched
        # at least one plugin), sweep for conjunction-joined plugin names.
        if plugins:
            for m in CONJUNCTION_RE.finditer(directive):
                plugins.add(PLUGIN_SYNONYMS[m.group(1).lower()])

        excluded: set[str] = set()
        for m in EXCEPT_RE.finditer(directive):
            excluded.add(PLUGIN_SYNONYMS[m.group(1).lower()])
        # If a plugin is both included and excluded, exclude wins.
        plugins -= excluded

        handles = {f"@{m.group(1)}" for m in HANDLE_RE.finditer(directive)}

        tagged: set[str] = set()
        for m in EXPLICIT_TAG_RE.finditer(directive):
            tagged.add(m.group(1).lower())
        for m in TO_TAGGED_RE.finditer(directive):
            # Don't double-count — only treat as tag if it's not a known plugin
            t = m.group(1).lower()
            if t not in PLUGIN_SYNONYMS:
                tagged.add(t)

        return TargetSelector(
            all_of_platforms=tuple(sorted(plugins)),
            by_handle=tuple(sorted(handles)),
            tagged=tuple(sorted(tagged)),
            exclude_plugins=tuple(sorted(excluded)),
        )
