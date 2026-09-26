"""Central settings. Every threshold and budget lives here, never hard-coded in modules."""

import os
from pathlib import Path

from pydantic import BaseModel

# Repo root in a checkout. In the image api/ sits at /app, so this resolves to /data, where the
# Dockerfile copies data/; docker-compose also sets ONECLICK_DATA explicitly.
_DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"


class Settings(BaseModel):
    # ---- Engine (Vishaal) --------------------------------------------------------------------------
    # LLM (ADR-002), tuned for free tiers (no billing). Measured 2026-09-24: every Gemini model
    # answers 503 on the free tier under load, and Mistral's free plan rate-limits Small/Medium (429)
    # but serves the open-weight Ministral 3B/8B/14B at ~80-100 tokens/s, concurrent calls allowed.
    #
    # Call B (extract) "select" mode: the model lists the article sentence ids of each action instead
    # of rewriting steps (~5x fewer output tokens; steps are the article's own words). It races the
    # quality model against the fast one and keeps the quality answer if it lands by the deadline.
    extract_mode: str = "select"  # select (extract.v2) | rewrite (extract.v1: needs a fast paid model)
    # The prompt follows the mode (router.prompt_version): select -> prompt_versions["extract"],
    # rewrite -> prompt_versions["extract_rewrite"].
    extract_model: str = "ministral-14b-latest"
    extract_fast_model: str = "ministral-8b-latest"  # raced with extract_model; "" disables the race
    extract_thinking: str = "low"  # Gemini models only
    # The quality answer wins if it is back by then. 5.0 since extract v3: 14B answered every kit
    # article within 5.3 s (median 2.3 s), so waiting to 6.0 only held 8B's answer back.
    extract_prefer_deadline_s: float = 5.0
    # Call A (enrich). Free tier: intents come from call B and the 8-10 variations from a small model
    # that runs alongside extraction, so no LLM sits on the critical path before extraction.
    enrich_llm_on_critical_path: bool = False
    enrich_model: str = "gemini-3.1-flash-lite"  # used only when enrich_llm_on_critical_path is on
    enrich_thinking: str = "minimal"
    variations_model: str = "ministral-14b-latest"  # background, so quality over speed
    variations_budget_s: float = 10.0  # background: never delays a response
    # Last resort for every call. A model answering 429 or 5xx is skipped for llm_cooldown_s.
    fallback_model: str = "gemini-3-flash-preview"
    fallback_reasoning: str = "none"  # reasoning_effort on every Mistral call; "" leaves it out
    llm_cooldown_s: float = 60.0
    # Gemini 3 guidance: keep temperature at 1.0 (lower values can loop). Repeatability comes from
    # the cache and the compiler, not from temperature.
    llm_temperature_gemini: float = 1.0
    llm_temperature_mistral: float = 0.0
    # Cache-key tag (with prompt_versions below): change it whenever a prompt changes.
    prompt_version: str = "enrich-v1+extract-v3+variations-v1"
    prompt_versions: dict[str, str] = {
        "enrich": "v1",
        # select mode. v3 (2026-09-26) asks for compact JSON and the exact setting in screen_path. On
        # the 11 kit articles 14B's slowest answer fell from 8.5 s to 5.3 s (v2's indented JSON spent
        # a third of its tokens on whitespace, and pushed the 56-sentence article past the 6.5 s
        # timeout on every run), and 8B stopped answering with runs of blank space (bad_json).
        "extract": "v3",
        "extract_rewrite": "v1",  # rewrite mode
        "variations": "v1",
    }
    # USD per 1M tokens (input, output) for meta.cost_usd and /v1/metrics. Models not listed cost
    # $0 here: the Ministral models run on Mistral's free plan.
    llm_prices: dict[str, tuple[float, float]] = {
        "gemini-3.1-flash-lite": (0.25, 1.50),
        "gemini-3.5-flash-lite": (0.30, 2.50),
        "gemini-3.8-flash": (0.75, 3.75),
        "mistral-small-2603": (0.15, 0.60),
    }

    # Stage budgets in seconds (design doc: Reliability). The primary gets its own timeout inside
    # the budget so the fallback still has time to answer.
    enrich_primary_timeout_s: float = 1.5
    enrich_budget_s: float = 2.5
    # Cold p95 must stay under 8 s (spec) with every other stage on top (~0.5-1.2 s, more for a long
    # article). 7.0 put a fuller touch-lag plan at 8.26 s in the gate replica; past 6.0 s the fast
    # model's answer (typically ~3.5 s) is used instead.
    extract_primary_timeout_s: float = 6.0
    extract_budget_s: float = 6.3
    llm_timeout_default_s: float = 3.0  # a client called without a stage timeout
    llm_min_fallback_s: float = 0.5  # less than this left in a stage budget: skip the fallback model
    variations_workers: int = 4  # background variations calls running at once
    max_intents: int = 3  # intents (and so Goals) per query, from call A or call B
    enrich_max_tokens: int = 1024
    extract_max_tokens: int = 1500  # select mode needs ~100-400; rewrite mode wants ~4096
    # Actions per goal in select mode (schema maxItems + the prompt). Asked to cover every fix, the
    # touch-lag article drew 9-11 actions (~500 output tokens, 8B 4.4-6.2 s), and one cold run in four
    # missed the stage budget. Samsung's own sample plan has two.
    extract_max_actions: int = 8
    # The extract prompt says to return no goals when the article does not address the complaint. An
    # empty answer is trusted only when this many models gave one and none chose anything: 14B has
    # answered empty once for an article that did fit, while 8B answered it fully.
    extract_empty_votes: int = 2
    # Rules-only extraction (no model answered) takes the article's own instructions, so it must not
    # run on an article about something else. Best section relevance on the kit: 0.59-0.80; a washing
    # machine article sent with a phone complaint: 0.52.
    rules_min_relevance: float = 0.55
    variations_max_tokens: int = 600

    # Segmenting and grounding (components 4, 6)
    section_relevance_floor: float = 0.5  # below this a section is greyed out for that intent
    section_embed_chars: int = 400  # heading + this much body is embedded for section relevance
    grounding_min_shared_terms: int = 1
    rules_only_score_cap: float = 0.5  # extraction fell back to rules: score says so
    # A rules-only answer given while a model is configured (the free tier was busy) is cached like any
    # other, so repeats stay identical, but only this long: after it the question gets a cold run again.
    degraded_cache_ttl_s: float = 600.0

    # Compiler (component 10)
    description_min_words: int = 5  # counting "It will"
    description_max_words: int = 7
    title_min_words: int = 2
    title_max_words: int = 3
    # score = relevance + grounding coverage + link coverage, weighted; a catalog link counts in full,
    # a dummy link half, a manual action not at all (it is left out of link coverage).
    score_weights: tuple[float, float, float] = (0.4, 0.3, 0.3)
    link_value_catalog: float = 1.0
    link_value_dummy: float = 0.5
    validate_max_passes: int = 3  # repair passes before offending actions are dropped one by one
    action_dup_jaccard: float = 0.8  # steps this alike are the same action (multi-intent dedupe)

    # No article in the request (cache/no_siis.py). Never answered from nothing: a cached plan, or the
    # pipeline over a remembered article, else empty with fallback no_siis_context. Both gates are
    # stricter than the with-article cache because the article hash no longer guards the match.
    # Measured 2026-09-25 on the kit plans + real variations, paraphrases (200), unseen (15):
    no_siis_plan_threshold: float = 0.80  # 80% paraphrase hits, 3/200 other-article plans, 0/15 unseen
    article_match_threshold: float = 0.82  # query vs remembered article (title or best section)
    article_match_margin: float = 0.04  # ...and a clear winner: 7 right, 0 wrong, 0/15 unseen
    article_memory_max: int = 500  # remembered articles kept, oldest dropped first
    # The kit plans the no-article table is pre-warmed from; None = <data_dir>/results.jsonl, else the
    # repo-root results.jsonl. The SIIS cache itself still ships empty.
    no_siis_table_path: str | None = os.getenv("ONECLICK_RESULTS")
    # ------------------------------------------------------------------------------------------------

    # Cache (ADR-004)
    # 0.70, decided 2026-09-25 (sweep 0.55-0.85, real cache: results.jsonl variations, intent slot and
    # one-sided symptom rule). With the guards in place near-miss false hits stay at 1/60 and wrong-
    # article plans at 0 at every threshold, so the threshold only trades paraphrase hits: 83.5% at
    # 0.70 (a 3.5-point margin over A3's 80%) against 80.5% at 0.75. 0.70 is the lowest value where
    # every hit still serves its own row's plan; at 0.68 a hit starts serving a sibling row's plan.
    cache_sim_threshold: float = 0.70
    sqlite_path: str = os.getenv("ONECLICK_SQLITE", "cache.sqlite")

    # Where data/kit and data/build live. Set ONECLICK_DATA in the container.
    data_dir: str = os.getenv("ONECLICK_DATA", str(_DEFAULT_DATA_DIR))

    # Grounding (component 6)
    grounding_cos_threshold: float = 0.75

    # Query variations (component 3)
    variation_min: int = 8
    variation_max: int = 10
    variation_jaccard_max: float = 0.6
    variation_meaning_min_cos: float = 0.6

    # Response (ADR-006)
    include_meta: bool = True

    # Console stream (Vishaal): True replays data/fixtures instead of running the pipeline (demo only)
    stream_mock: bool = False
    stream_mock_time_scale: float = 1.0  # 1.0 = recorded stage timings; 0 = no delay (tests)
    stream_fixtures_dir: str | None = None  # None = <repo>/data/fixtures (not in the Docker image)
    # Retrieval (Karur, component 7): hybrid search over catalog entries / Screen Graph nodes
    embed_model: str = "BAAI/bge-small-en-v1.5"
    rerank_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    retrieval_top_k: int = 20  # candidates each searcher returns before fusion
    rerank_candidates: int = 10  # candidates sent to the cross-encoder (CPU cost is linear)
    rerank_top_k: int = 3  # candidates kept after the cross-encoder
    leaf_weight: float = 0.6  # weight of the leaf-screen name match ("Dark mode" in the path)
    polarity_bonus: float = 0.25  # entry type matches the step verb (enable -> onURL)
    polarity_penalty: float = 0.35  # entry is the opposite toggle (enable step -> offURL entry)
    offsurface_penalty: float = 0.3  # entry belongs to TV Settings / Members, not device Settings
    # Cross-encoder rerank. Off by default: on the Screen Graph it costs ~250 ms per step and
    # lowered recall@3 on article-worded steps (100% -> 88%), because grouping buttons into
    # screens already removed the near-ties it used to break. Kept for re-measuring later.
    use_rerank: bool = False
    # Adaptive rerank thresholds, used when use_rerank is on
    rerank_confident_score: float = 1.60  # top score at or above this: accept without reranking
    rerank_min_margin: float = 0.10  # top two closer than this: rerank to break the tie
    # Resolver tiers (component 7). Scores come from retrieval.search: fusion (<=1.0) plus the
    # leaf-name match and polarity bonus, so a confident screen sits near the top of the range.
    link_catalog_min_score: float = 1.60  # below this no catalog entry is trusted
    link_max_score: float = 1.85  # full marks, used to normalise confidence to 0-1
    rrf_k: int = 60  # reciprocal rank fusion constant
    # BM25 field weights: a field's tokens are repeated this many times in the indexed document
    bm25_field_weights: dict[str, int] = {
        "message": 2,
        "qna_description": 2,
        "clean_description": 1,
    }


settings = Settings()
