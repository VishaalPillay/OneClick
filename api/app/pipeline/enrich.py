"""Component 3: Stage 1 LLM call — intents, domain, title, 12 candidate variations; lexical-diversity filter.

The variations are what the semantic cache indexes, so a paraphrase of a solved question hits.
Filter (design doc C3): drop a candidate whose token Jaccard with the original or an already-kept
variation is >= 0.6 (a near-copy adds nothing), or whose embedding cosine with the original is < 0.6
(it drifted off meaning). Keep 8-10. When the LLM is unavailable, templates built from the query's
own words stand in, so results.jsonl always carries 8-10 variations.

Free tier (settings.enrich_llm_on_critical_path off): the intents come from call B, and only the
variations use an LLM, started in the background next to extraction (start_variations) so they never
delay an answer; the cache picks them up when they land (finish_variations).
"""

import re
from concurrent.futures import Future, ThreadPoolExecutor

import numpy as np

from app.cache.slot_guard import compatible
from app.compiler.scrub import scrub
from app.compiler.trimmer import trim_title
from app.config import settings
from app.models import Intent, Slots
from app.pipeline.normalize import normalize_query
from app.pipeline.slots import extract_slots
from app.pipeline.text import content_terms
from app.retrieval import dense

_WORD = re.compile(r"\w+")
_SYMPTOM_WORDS = {
    "black": "black",
    "cracked": "cracked",
    "flicker": "flickering",
    "drain": "draining",
    "overheating": "overheating",
    "slow": "slow",
    "unresponsive": "unresponsive",
    "distorted": "distorted",
    "crash": "crashing",
    "no_connection": "not connecting",
    "blurry": "blurry",
    "no_sound": "silent",
}
_DOMAINS = {
    "screen": "Display",
    "keyboard": "Display",
    "battery": "Battery",
    "camera": "Camera",
    "app": "Performance",
    "performance": "Performance",
    "storage": "Performance",
}
# The templates add the brand and device words themselves.
_DEVICE_WORDS = frozenset({"samsung", "galaxy", "phone", "tablet", "mobile", "device", "new"})
_TYPO_SWAPS = (("ea", "ae"), ("en", "ne"), ("in", "ni"), ("er", "re"), ("on", "no"), ("ck", "kc"))


def word_jaccard(a: str, b: str) -> float:
    left, right = set(_WORD.findall(a.lower())), set(_WORD.findall(b.lower()))
    return len(left & right) / len(left | right) if left | right else 1.0


def filter_variations(
    query: str, candidates: list[str], slots: Slots | None = None
) -> tuple[list[str], list[dict]]:
    """(kept, dropped[{text, reason, jaccard}]) — the rule the fixtures and test_fixtures.py encode.

    With `slots` (the query's), a candidate whose own slots contradict them is set aside: "screen
    flashes" reworded as "screen crashes" is a different problem, not a paraphrase. Measured on the
    kit: 33 of 200 model-written variations did that, and as cache keys they can never serve their own
    plan (the slot guard blocks them) while they can pull a no-article question toward another one.
    They come back only when fewer than variation_min would survive otherwise: a configure request
    ("I want to remove it") has fault-worded templates, and 8-10 variations is a hard rule.
    """
    norm = normalize_query(query)
    seen: set[str] = set()
    pool = []
    for text in candidates:
        text = " ".join(scrub(text or "").split())
        key = normalize_query(text)
        if text and key != norm and key not in seen:
            seen.add(key)
            pool.append(text)
    if not pool:
        return [], []
    vectors = np.asarray(dense.embed([query, *pool]), dtype=np.float32)
    cosines = vectors[1:] @ vectors[0]
    kept: list[str] = []
    dropped: list[dict] = []

    def consider(text: str, cosine: float, check_slots: bool) -> None:
        vs_original = word_jaccard(text, query)
        vs_kept = max((word_jaccard(text, k) for k in kept), default=0.0)
        if check_slots and slots is not None and not compatible(extract_slots(normalize_query(text)), slots):
            dropped.append({"text": text, "reason": "changes_the_problem", "jaccard": round(vs_original, 2)})
        elif vs_original >= settings.variation_jaccard_max:
            dropped.append(
                {"text": text, "reason": "token_jaccard_vs_original", "jaccard": round(vs_original, 2)}
            )
        elif vs_kept >= settings.variation_jaccard_max:
            dropped.append({"text": text, "reason": "token_jaccard_vs_kept", "jaccard": round(vs_kept, 2)})
        elif cosine < settings.variation_meaning_min_cos:
            dropped.append(
                {
                    "text": text,
                    "reason": "off_meaning",
                    "jaccard": round(vs_original, 2),
                    "cosine": round(cosine, 2),
                }
            )
        elif len(kept) >= settings.variation_max:
            dropped.append({"text": text, "reason": "over_limit", "jaccard": round(vs_original, 2)})
        else:
            kept.append(text)

    by_text = dict(zip(pool, (float(c) for c in cosines)))
    for text in pool:
        consider(text, by_text[text], check_slots=True)
    set_aside = [d["text"] for d in dropped if d["reason"] == "changes_the_problem"]
    for text in set_aside:
        if len(kept) >= settings.variation_min:
            break
        dropped = [d for d in dropped if d["text"] != text]
        consider(text, by_text[text], check_slots=False)
    return kept, dropped


# ---- rules path (no LLM) ----------------------------------------------------------------------------
def rules_title(slots: Slots, query: str) -> str:
    if slots.component and slots.symptom:
        return f"{slots.component.capitalize()} {_SYMPTOM_WORDS.get(slots.symptom, slots.symptom)}"
    if slots.component:
        return f"{slots.component.capitalize()} issue"
    terms = [t for t in content_terms(query) if not t.isdigit()][:2]
    return " ".join(terms).capitalize() if len(terms) == 2 else "Device issue"


def _typo(word: str) -> str:
    for a, b in _TYPO_SWAPS:
        if a in word:
            return word.replace(a, b, 1)
    return word[:-1] if len(word) > 4 else word


def template_variations(query: str, slots: Slots) -> list[str]:
    """Candidates in the five registers, built only from the query's own words and its slots."""
    ordered = []
    for word in re.findall(r"[a-z0-9][a-z0-9\-']*", query.lower()):
        if word in content_terms(word) and word not in _DEVICE_WORDS and word not in ordered:
            ordered.append(word)
    k = (ordered + ["phone", "issue", "problem", "device"])[:6]
    c = slots.component or k[0]
    s = _SYMPTOM_WORDS.get(slots.symptom or "", "") or k[1]
    return [
        " ".join(k[:5]),
        f"{c} {s} fix",
        f"Why is my {c} {s}?",
        f"How can I repair a {s} {c} on my Galaxy?",
        f"Help, my {c} keeps being {s}!",
        f"Samsung {c} problem: {k[0]}, {k[1]}",
        f"I am experiencing an issue where the device {c} is {s}.",
        f"ugh {c} {s} again, so annoying",
        f"{_typo(k[0])} {_typo(k[1])} {_typo(c)} not wroking",
        f"What causes {k[0]} and {k[2]} on a Galaxy?",
        f"Troubleshooting a {s} {c}",
        f"{c} {s} after update, what should I do",
    ]


def enrich_rules(query: str, slots: Slots, *, templates: bool = True) -> tuple[list[Intent], list[str], dict]:
    """The provisional intent, and template variations unless `templates` is off: with the background
    variations call running they are never used (finish_variations adds them itself if it needs them),
    and filtering them embeds a dozen texts on the answer's path."""
    text = " ".join(scrub(query).split()) or query
    intent = Intent(
        text=text, domain=_DOMAINS.get(slots.component or "", "Other"), title=rules_title(slots, text)
    )
    candidates = template_variations(text, slots) if templates else []
    kept, dropped = filter_variations(text, candidates) if candidates else ([], [])
    detail = {
        "canonical_query": text,
        "intents": [intent.model_dump(mode="json")],
        "variations": kept,
        "dropped_variations": dropped,
        "candidates": len(candidates),
        "model": None,
        "source": "rules",
        "tokens_in": 0,
        "tokens_out": 0,
    }
    return [intent], kept, detail


# ---- LLM path (call A) ------------------------------------------------------------------------------
DOMAIN_NAMES = ["Battery", "Display", "Camera", "Performance", "Other"]
SCHEMA = {
    "type": "object",
    "properties": {
        "canonical_query": {"type": "string"},
        "intents": {
            "type": "array",
            "minItems": 1,
            "maxItems": settings.max_intents,
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "domain": {"type": "string", "enum": DOMAIN_NAMES},
                    "title": {"type": "string"},
                },
                "required": ["text", "domain", "title"],
                "additionalProperties": False,
            },
        },
        "variations": {"type": "array", "items": {"type": "string"}, "minItems": 8, "maxItems": 14},
    },
    "required": ["canonical_query", "intents", "variations"],
    "additionalProperties": False,
}


def enrich_llm(query: str, slots: Slots) -> tuple[list[Intent], list[str], dict]:
    """Call A through the router. Raises LLMError when both providers fail (the caller degrades)."""
    from app.llm.router import complete_json

    info: dict = {}
    answer = complete_json("enrich", {"query": query}, SCHEMA, stage="enrich", info=info)
    intents = []
    for raw in (answer.get("intents") or [])[: settings.max_intents]:
        text = " ".join(scrub(str(raw.get("text") or "")).split())
        if not text:
            continue
        domain = raw.get("domain") if raw.get("domain") in DOMAIN_NAMES else "Other"
        intents.append(
            Intent(text=text, domain=domain, title=trim_title(scrub(str(raw.get("title") or text))))
        )
    if not intents:
        raise ValueError("the enrichment answer has no usable intent")
    llm_candidates = [str(v) for v in answer.get("variations") or [] if isinstance(v, str)]
    # Templates go after the model's candidates: they only fill the list up to 8 when too few survive.
    candidates = llm_candidates + template_variations(query, slots)
    kept, dropped = filter_variations(query, candidates, slots)
    detail = {
        "canonical_query": " ".join(scrub(str(answer.get("canonical_query") or query)).split()),
        "intents": [i.model_dump(mode="json") for i in intents],
        "variations": kept,
        "dropped_variations": dropped,
        "candidates": len(llm_candidates),
        "source": "llm",
        **{k: info.get(k) for k in ("model", "tokens_in", "tokens_out", "cost_usd", "attempts", "prompt")},
    }
    return intents, kept, detail


# ---- background variations (free tier) ---------------------------------------------------------------
VARIATIONS_SCHEMA = {
    "type": "object",
    "properties": {
        "variations": {"type": "array", "items": {"type": "string"}, "minItems": 8, "maxItems": 14}
    },
    "required": ["variations"],
    "additionalProperties": False,
}
_background = ThreadPoolExecutor(max_workers=settings.variations_workers, thread_name_prefix="variations")


def _variations_llm(query: str) -> tuple[list[str], dict]:
    from app.llm.router import complete_json

    info: dict = {}
    answer = complete_json("variations", {"query": query}, VARIATIONS_SCHEMA, stage="variations", info=info)
    return [str(v) for v in answer.get("variations") or [] if isinstance(v, str)], info


def start_variations(query: str) -> Future | None:
    """Start the variations call in the background; None when no LLM is configured."""
    from app.llm.router import available

    if not available() or settings.enrich_llm_on_critical_path:
        return None
    return _background.submit(_variations_llm, query)


def finish_variations(query: str, slots: Slots, future: Future) -> tuple[list[str], list[dict], dict]:
    """(kept, dropped, info) from a finished background call; templates fill in below 8 or on error."""
    try:
        llm_candidates, info = future.result(timeout=0)
    except Exception as exc:  # noqa: BLE001 - LLMError or a bad answer: the templates stand in
        llm_candidates, info = [], {"degraded": f"{type(exc).__name__}: {str(exc)[:200]}"}
    kept, dropped = filter_variations(query, llm_candidates + template_variations(query, slots), slots)
    keys = ("model", "tokens_in", "tokens_out", "cost_usd", "attempts", "prompt", "degraded")
    return kept, dropped, {"candidates": len(llm_candidates)} | {k: info.get(k) for k in keys if k in info}


# ---- entry points -----------------------------------------------------------------------------------
def enrich_with_report(
    query: str, slots: Slots, *, templates: bool = True
) -> tuple[list[Intent], list[str], dict]:
    """(intents, 8-10 variations, the `enrich` stream detail). LLM first, templates when it fails.

    With enrich_llm_on_critical_path off this is the no-LLM path: a provisional intent from the query
    (call B replaces it) and template variations (the background call replaces them).
    """
    from app.llm.router import available

    if available() and settings.enrich_llm_on_critical_path:
        try:
            return enrich_llm(query, slots)
        except Exception as exc:  # noqa: BLE001 - LLMError, a malformed answer: degrade, never fail
            intents, kept, detail = enrich_rules(query, slots)
            detail["degraded"] = f"{type(exc).__name__}: {str(exc)[:200]}"
            detail["attempts"] = getattr(exc, "attempts", [])
            return intents, kept, detail
    return enrich_rules(query, slots, templates=templates)


def enrich(norm_query: str) -> tuple[list[Intent], list[str]]:
    """Return (intents, 8-10 filtered variations)."""
    intents, variations, _ = enrich_with_report(norm_query, extract_slots(norm_query))
    return intents, variations
