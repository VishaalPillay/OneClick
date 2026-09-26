"""Component 8: keyword overrides for critical/manual; auto only via the tiered link decision.

Also sets the two things the orderer needs: `disruption_rank` (0 quick checks ... 5 critical) and
`depends_on` (prerequisite kinds from data/dependencies.json, e.g. "backup" before a factory reset).
"""

import json
import re
from functools import lru_cache
from pathlib import Path

from app.config import settings
from app.models import DraftAction, LinkTier
from app.pipeline.text import token_set

# Critical actions, least to most disruptive (the design's restart < safe mode < update < reset).
CRITICAL_KINDS: tuple[tuple[str, re.Pattern], ...] = (
    (
        "restart",
        re.compile(
            r"\b(?:force )?restart(?:ing|s|ed)?\b(?! (?:the |your )?(?:\w+ )?app\b)|\breboot|"
            r"\bturn (?:it|the (?:device|phone)|your (?:device|phone)) off and (?:back )?on",
            re.IGNORECASE,
        ),
    ),
    ("safe mode", re.compile(r"\bsafe mode\b", re.IGNORECASE)),
    (
        "software update",
        re.compile(
            r"\bsoftware updates?\b|\bupdate (?:the |your )?(?:device |phone |tablet )?software\b|"
            r"\bsystem update|\bfirmware update",
            re.IGNORECASE,
        ),
    ),
    ("factory reset", re.compile(r"\bfactory (?:data )?reset\b|\breset\b.*\bfactory\b", re.IGNORECASE)),
)

# (rank, pattern) checked from most to least disruptive; first match wins. Rank 5 is critical.
_RANKS: tuple[tuple[int, re.Pattern], ...] = (
    (
        4,
        re.compile(
            r"service cent|\bcontact\b|samsung support|customer support|\bcarrier\b|"
            r"email (?:service )?provider|\brepair\b|authori[sz]ed",
            re.IGNORECASE,
        ),
    ),
    (3, re.compile(r"samsung members|diagnos", re.IGNORECASE)),
    (
        2,
        re.compile(
            r"reset network|network settings|\bsign (?:in|out)\b|re-?add|(?:remove|add) (?:the |your )?"
            r"(?:\w+ )?account|\bre-?log",
            re.IGNORECASE,
        ),
    ),
    (
        1,
        re.compile(
            r"clear (?:the )?(?:app'?s? )?(?:cache|data|storage)|\bcache\b|uninstall|reinstall|force stop|"
            r"update (?:the |your )?(?:\w+ )?apps?\b|app updates?",
            re.IGNORECASE,
        ),
    ),
)


@lru_cache(maxsize=1)
def dependency_edges() -> tuple[tuple[str, frozenset, frozenset], ...]:
    """(before_kind, before_words, after_words) from data/dependencies.json."""
    path = Path(settings.data_dir) / "dependencies.json"
    try:
        edges = json.loads(path.read_text(encoding="utf-8")).get("edges", [])
    except (OSError, ValueError):
        return ()
    stop = {"in", "the", "a", "an", "of", "to", "while", "on"}
    return tuple(
        (e["before"], frozenset(_words(e["before"]) - stop), frozenset(_words(e["after"]) - stop))
        for e in edges
    )


def _words(text: str) -> set[str]:
    """Token set with "back up" read as "backup", so "Back Up Your Data" provides the backup edge."""
    return token_set(re.sub(r"\bback(?:ing)? ?up\b", "backup", text or "", flags=re.IGNORECASE))


def _headline(action: DraftAction) -> str:
    """What the action *is*: its name and screen, or its first step when unnamed. Later steps only
    mention things ("Uninstall the app while in Safe mode" is not itself a safe mode restart)."""
    head = " ".join(x for x in (action.name, action.screen_path) if x)
    return head or (action.steps[0].text if action.steps else "")


def action_text(action: DraftAction) -> str:
    return " ".join([action.name or "", action.screen_path or "", *(s.text for s in action.steps)])


def critical_kind(action: DraftAction) -> str | None:
    """The most disruptive critical kind the action's headline names, or None."""
    head = _headline(action)
    found = None
    for kind, pattern in CRITICAL_KINDS:
        if pattern.search(head):
            found = kind
    if found is None and (action.intent_verb or "").lower() == "restart" and not action.name:
        found = "restart"
    return found


def critical_rank(action: DraftAction) -> int:
    kind = critical_kind(action)
    return [k for k, _ in CRITICAL_KINDS].index(kind) if kind else -1


def _rank(action: DraftAction) -> int:
    if critical_kind(action):
        return 5
    head = _headline(action)
    for rank, pattern in _RANKS:
        if pattern.search(head):
            return rank
    text = action_text(action)
    for rank, pattern in _RANKS:
        if rank >= 3 and pattern.search(text):  # "Contact Samsung Support" steps name the centre
            return rank
    return 0


def _depends_on(action: DraftAction) -> list[str]:
    words = _words(action_text(action))
    kinds = list(action.depends_on)
    for before, before_words, after_words in dependency_edges():
        provides_itself = before_words <= _words(_headline(action))
        if after_words and after_words <= words and not provides_itself and before not in kinds:
            kinds.append(before)
    return kinds


def categorize(actions: list[DraftAction]) -> list[DraftAction]:
    out = []
    for action in actions:
        rank = _rank(action)
        link = action.link
        if rank == 5:
            category = "critical"
        elif link is not None and link.tier in (LinkTier.catalog, LinkTier.dummy):
            category = "auto"
        else:
            category = "manual"
        out.append(
            action.model_copy(
                update={"category": category, "disruption_rank": rank, "depends_on": _depends_on(action)}
            )
        )
    return out
