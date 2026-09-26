"""Component 7 (runtime): step -> node -> entry by polarity; tiered link decision (catalog / dummy / manual)."""

import re

from app import retrieval
from app.config import settings
from app.models import DraftAction, LinkDecision, LinkTier
from app.screengraph import get_node, node_of, sibling_for

DUMMY_ENTRY_ID = "DL-DUMMY"

# Steps that leave the Settings app entirely: no deeplink can ever open them.
# Verbs come from Vishaal's extractor; the phrases catch steps whose verb was guessed loosely.
_CRITICAL_VERBS = frozenset(["restart", "reboot", "reset"])
_MANUAL_VERBS = frozenset(["visit", "contact", "clean", "replace", "charge", "connect", "insert"])

_CRITICAL_PHRASES = (
    "safe mode",
    "force restart",
    "factory data reset",
    "factory reset",
    "software update",
    "power off",
    "reboot",
    "restart",
)
# Surfaces outside the Settings app: a deeplink opens Settings screens, nothing else.
_OFF_APP_SURFACES = ("quick settings", "quick panel", "notification panel", "power menu", "recents")

_MANUAL_PHRASES = (
    *_OFF_APP_SURFACES,
    "service cent",  # centre / center
    "samsung support",
    "authorized",
    "authorised",
    "charger",
    "charging cable",
    "power button",
    "side key",
    "volume down",
    "usb",
    "sim tray",
    "sim card",
    "soft cloth",
    "soft brush",
    "compressed air",
    "screen protector",
    "otg",
    "hdmi",
)

# Verbs that stay inside the Settings app; with one of these a path under Settings is a real
# screen, even when its name contains a word like "reset" ("Settings > General management > Reset").
_IN_APP_VERBS = frozenset(["open", "enable", "disable", "set", "adjust", "check"])
_SETTINGS_PATH_RE = re.compile(r"^\s*settings\b", re.IGNORECASE)
# Verbs that change a setting on a screen; with no toggle for them, the screen's own page will do.
_CHANGE_VERBS = frozenset(["enable", "disable", "set", "adjust"])


def _haystack(action: DraftAction) -> str:
    steps = " ".join(step.text for step in action.steps)
    return f"{action.screen_path or ''} {steps}".lower()


def offscreen_tier(action: DraftAction) -> LinkTier | None:
    """LinkTier.manual when the step cannot be a Settings screen, else None (go on and search).

    Critical operations (restart, safe mode, factory reset) and physical ones (visit a service
    centre, clean the port) both resolve to no deeplink; the categorizer separates them later.
    """
    verb = (action.intent_verb or "").lower()
    if verb in _MANUAL_VERBS:
        return LinkTier.manual
    # A Settings screen being opened or toggled is searchable whatever its wording mentions:
    # "Turn on Fast charging for the cable charger" is a settings toggle, not a physical step.
    # Critical verbs count too when the step names a screen ("Settings > ... > Factory data
    # reset"): resolver_cases.json expects a link there, and the compiler drops it if the
    # categorizer marks the action critical.
    if _SETTINGS_PATH_RE.match(action.screen_path or "") and verb in _IN_APP_VERBS | _CRITICAL_VERBS:
        return None
    if verb in _CRITICAL_VERBS:
        return LinkTier.manual
    text = _haystack(action)
    if any(phrase in text for phrase in _MANUAL_PHRASES + _CRITICAL_PHRASES):
        return LinkTier.manual
    return None


def resolve(action: DraftAction) -> LinkDecision:
    """Pick the catalog entry for one action, or decide there is none.

    Three outcomes (design doc, tiered link table):
      catalog - a screen matched above the confidence floor; entry chosen by the step verb
      dummy   - the step clearly opens a Settings screen the catalog does not cover
      manual  - physical, external or off-Settings steps: no deeplink at all
    """
    if offscreen_tier(action) is not None:
        return LinkDecision(tier=LinkTier.manual)
    if not action.screen_path:
        return LinkDecision(tier=LinkTier.manual)

    results = retrieval.search(action.screen_path, action.intent_verb, k=1)
    score = results[0][1] if results else 0.0
    if score < settings.link_catalog_min_score and (action.intent_verb or "").lower() in _CHANGE_VERBS:
        # No on/off entry for this change, but the screen that holds it may still have a page link
        # ("Select Buttons to turn off full screen gestures" -> View Navigation bar). Opening the
        # exact screen is the right one tap; the floor below still has to be cleared.
        page = retrieval.search(action.screen_path, "open", k=1)
        if page and page[0][1] > score:
            results, score = page, page[0][1]
    confidence = min(score / settings.link_max_score, 1.0)

    if results and score >= settings.link_catalog_min_score:
        entry_id = sibling_for(results[0][0], action.intent_verb)
        return LinkDecision(
            tier=LinkTier.catalog,
            node_id=node_of(entry_id),
            entry_id=entry_id,
            confidence=round(confidence, 2),
        )

    # No catalog screen is convincing. A step that still names a Settings screen gets the
    # placeholder; anything vaguer gets no link, which is safer than a wrong one.
    if _SETTINGS_PATH_RE.match(action.screen_path):
        return LinkDecision(tier=LinkTier.dummy, entry_id=DUMMY_ENTRY_ID, confidence=round(confidence, 2))
    return LinkDecision(tier=LinkTier.manual, confidence=round(confidence, 2))


def validation_for(entry_id: str) -> dict | None:
    """The entry's own validation object, copied verbatim (hard rule 8)."""
    node_id = node_of(entry_id)
    if not node_id:
        return None
    return get_node(node_id).validation_by_entry.get(entry_id)
