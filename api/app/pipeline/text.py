"""Small text helpers shared by grounding, extraction, enrichment and multi-intent dedupe. No LLM."""

import re

_WORD = re.compile(r"[a-z0-9][a-z0-9\-']*")


def word_block(block: str) -> frozenset[str]:
    """A frozenset from a whitespace-separated block of words."""
    return frozenset(re.split(r"\s+", block.strip()))


STOPWORDS = word_block(
    """
    a an the and or but to of in on for with your you it is are be as at by if then this that these
    those from into up can may might will would could should when what there their its our we us has
    have not any all also just very more some such how does do did was were been being so than too
    out about over after before while until again once here where which who whom why my me i am let
    s t don't doesn't won't can't isn't aren't you're it's i'm
    """
)
# Words every UI step uses; sharing one of these with a sentence proves nothing about grounding.
UI_VERBS = word_block(
    """
    tap touch press hold select open go navigate swipe scroll turn make sure try use check find
    choose enter click follow then again see need want
    """
)


def tokens(text: str) -> list[str]:
    return _WORD.findall((text or "").lower())


def token_set(text: str) -> set[str]:
    return set(tokens(text))


def content_terms(text: str) -> set[str]:
    """Nouns-ish words: no stopwords, no generic UI verbs, at least 3 characters (keeps "wi-fi")."""
    return {w for w in tokens(text) if w not in STOPWORDS and w not in UI_VERBS and len(w) >= 3}


def recognisable(query: str, context: str) -> bool:
    """True when the query holds at least one word a reader would recognise: a common English word or
    one the article also uses. Typos and other languages still share some ("my", "screen", "black");
    "asdkjh qwe zzz 12345" shares none, and no answer to it would be anything but a guess."""
    words = [w for w in tokens(query) if any(c.isalpha() for c in w)]
    vocabulary = token_set(context)
    return any(w in STOPWORDS or w in UI_VERBS or w in vocabulary for w in words)


def jaccard(a: str, b: str) -> float:
    left, right = token_set(a), token_set(b)
    if not left and not right:
        return 1.0
    return len(left & right) / len(left | right)
