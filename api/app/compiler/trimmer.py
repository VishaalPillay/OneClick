"""Component 10: enforce title 2-3 words and description 'It will' + 5-7 words total."""

import re

from app.config import settings

_PUNCT_EDGES = ".,;:!?\"'()[]"
# Dropped first when a title or description is too long; they carry no meaning of their own.
_FILLERS = {"the", "a", "an", "your", "my", "any", "all", "some", "this", "that", "its", "their", "our"}
# A trimmed description must not end on one of these ("It will clear the data of").
_DANGLING = _FILLERS | {"of", "to", "and", "or", "for", "with", "in", "on", "from", "by", "at", "into", "if"}
# Kept capitalised inside a sentence-case title or a description.
_PROPER = {
    "galaxy", "samsung", "wi-fi", "wifi", "bluetooth", "android", "gmail", "google", "smart", "switch",
    "members", "cloud", "one", "ui", "dex", "bixby", "sim", "usb", "nfc", "gps", "sd", "esim", "pin",
}  # fmt: skip
_TITLE_FALLBACK_WORD = "issue"
_DESC_PREFIX = ("It", "will")
_DESC_TAIL = ("on", "your", "device")
_DESC_DEFAULT = "It will help resolve this issue"


def _is_proper(word: str) -> bool:
    bare = word.strip(_PUNCT_EDGES)
    # An inner capital marks a name ("iPhone", "OneClick"); one only after a hyphen does not
    # ("Non-Responsive" is Title Case, not a name). Listed names ("Wi-Fi") stay either way.
    inner = re.sub(r"-([A-Z])", lambda m: "-" + m.group(1).lower(), bare)[1:]
    return bare.lower() in _PROPER or any(c.isdigit() for c in bare) or inner != inner.lower()


def _sentence_case(words: list[str]) -> list[str]:
    out = []
    for i, word in enumerate(words):
        if i == 0:
            out.append(word[:1].upper() + word[1:])
        elif _is_proper(word):
            out.append(word[:1].upper() + word[1:] if word.lower() in _PROPER else word)
        else:
            out.append(word.lower())
    return out


def _words(text: str) -> list[str]:
    return [w.strip(_PUNCT_EDGES) for w in (text or "").split() if w.strip(_PUNCT_EDGES)]


def trim_title(title: str) -> str:
    """2-3 words, sentence case, no trailing punctuation. Fillers go first when it is too long."""
    words = _words(title)
    low, high = settings.title_min_words, settings.title_max_words
    if len(words) > high:
        kept = [w for w in words if w.lower() not in _DANGLING]
        words = kept[:high] if len(kept) >= low else words[:high]
        while len(words) > low and words[-1].lower() in _DANGLING:
            words.pop()
    if not words:
        words = ["Troubleshooting"]
    while len(words) < low:
        words.append(_TITLE_FALLBACK_WORD)
    return " ".join(_sentence_case(words))


def _lower_common(words: list[str]) -> list[str]:
    """Lowercase ordinary words; keep proper nouns and a capitalised word that follows one
    ("Samsung Support", "Smart Switch")."""
    out: list[str] = []
    previous_proper = False
    for word in words:
        keep = _is_proper(word) or (previous_proper and word[:1].isupper())
        out.append(word if keep else word.lower())
        previous_proper = keep
    return out


def trim_description(text: str) -> str:
    """`It will ...` with 5-7 words in total, counting "It will"; no trailing period.

    A long draft loses fillers first, then words from the end (never ending on "of", "the"...);
    a short one gets a neutral tail so the count still holds.
    """
    words = _words(text)
    if len(words) >= 2 and words[0].lower() == "it" and words[1].lower() == "will":
        words = words[2:]
    elif words and words[0].lower() == "will":
        words = words[1:]
    body_max = settings.description_max_words - len(_DESC_PREFIX)
    body_min = settings.description_min_words - len(_DESC_PREFIX)
    if len(words) > body_max:
        words = [w for i, w in enumerate(words) if i == 0 or w.lower() not in _FILLERS]
    if len(words) > body_max:
        words = words[:body_max]
    while len(words) > 1 and words[-1].lower() in _DANGLING:
        words.pop()
    if not words:
        return _DESC_DEFAULT
    if len(words) < body_min:
        tail = [w for w in _DESC_TAIL if w not in (x.lower() for x in words)]
        words += tail[: body_max - len(words)]
    words = _lower_common(words)  # drafts from headings arrive in Title Case
    return " ".join([*_DESC_PREFIX, *words])
