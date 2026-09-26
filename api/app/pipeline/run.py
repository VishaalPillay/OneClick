"""Orchestrator: normalize -> cache -> enrich -> segment -> extract -> ground -> resolve -> order -> compile.

One code path: `run_stream()` yields a StageEvent as each stage finishes and always ends with `done`,
whose detail is the full response body; `run()` just drains it. So the scored endpoint and the console
stream can never disagree. Every stage degrades instead of raising (hard rule 4):

    enrich fails      -> the query as the only intent, template variations
    extract fails     -> rules-only steps from the relevant sections, score capped
    nothing grounded  -> empty contexts, fallback "no_match" (never a fabricated step)
    no SIIS           -> a cached plan, else the pipeline over a remembered article, else empty with
                         fallback "no_siis_context" (cache/no_siis.py; never an LLM filling the gap)
"""

import time
from collections.abc import Iterator
from concurrent.futures import wait

from app import cache
from app.cache import exact as exact_tier
from app.cache import no_siis, store
from app.compiler.compile import compile_with_report
from app.compiler.validate import validate_with_report
from app.config import settings
from app.models import (
    CacheEntry,
    DraftAction,
    Intent,
    LinkDecision,
    LinkTier,
    ResponseMeta,
    Slots,
    StageEvent,
)
from app.models import StageName as S
from app.obs import metrics, readiness
from app.obs import trace as traces
from app.obs.logging import log_request
from app.pipeline.categorize import categorize
from app.pipeline.enrich import enrich_rules, enrich_with_report, finish_variations, start_variations
from app.pipeline.extract import extract_rules, extract_with_topics
from app.pipeline.ground import ground_with_report
from app.pipeline.multi_intent import dedupe_with_report
from app.pipeline.normalize import clean_siis, display_query, normalize_query, siis_title
from app.pipeline.order import order
from app.pipeline.segment import segment_with_sections
from app.pipeline.slots import extract_slots
from app.pipeline.text import recognisable
from app.retrieval import search as retrieval_search
from app.screengraph.resolver import resolve

FALLBACK_NO_MATCH = "no_match"
FALLBACK_NO_SIIS = "no_siis_context"


class _Run:
    """Per-request state: timings, cost and the pieces the final meta block needs."""

    def __init__(self, query: str):
        self.started = time.perf_counter()
        self.trace = traces.new_trace()
        self.query = query
        self.models: list[str] = []
        self.cost = 0.0
        self.tokens_in = 0
        self.tokens_out = 0
        self.degraded: list[str] = []

    def event(self, stage: S, started: float, summary: str, detail: dict) -> StageEvent:
        timing = traces.stage(self.trace, stage.value, started, summary)
        return StageEvent(stage=stage, ms=timing.ms, summary=summary, detail=detail)

    def add_llm(self, info: dict, *, answer_model: bool = True) -> None:
        """Count an LLM call's tokens and cost; `answer_model` calls also name meta.model."""
        if answer_model and info.get("model"):
            self.models.append(info["model"])
        self.cost += float(info.get("cost_usd") or 0.0)
        self.tokens_in += int(info.get("tokens_in") or 0)
        self.tokens_out += int(info.get("tokens_out") or 0)

    def elapsed_ms(self) -> float:
        return round((time.perf_counter() - self.started) * 1000, 1)

    def done(
        self,
        contexts: list,
        *,
        tier: str | None = None,
        fallback: str | None = None,
        latency: float | None = None,
        source: str | None = None,
    ) -> StageEvent:
        """The final frame; also records the trace and the metrics window (once per request).

        `latency` is the time the answer was ready; results.jsonl waits for background variations after
        that, and the wait is not part of what a caller of the API would see.
        """
        latency = self.elapsed_ms() if latency is None else latency
        model = self.models[-1] if self.models else ("rules" if tier is None and contexts else None)
        meta = ResponseMeta(
            latency_ms=latency,
            cache_hit=tier is not None,
            cache_tier=tier,
            model=None if tier else model,
            cost_usd=round(self.cost, 6),
            trace_id=self.trace.trace_id,
            fallback=fallback,
            source=source,
        )
        self.trace.cache_tier = tier
        self.trace.model = meta.model
        self.trace.fallback = fallback
        self.trace.cost_usd = meta.cost_usd
        self.trace.tokens_in, self.trace.tokens_out = self.tokens_in, self.tokens_out
        try:
            extra = {"query": self.query, "degraded": self.degraded}
            traces.put(self.trace, extra)
            metrics.record(self.trace, latency, fallback)
            log_request({**self.trace.model_dump(mode="json"), **extra, "latency_ms": latency})
        except Exception as exc:  # noqa: BLE001 - observability must never cost the answer
            self.degraded.append(f"obs:{type(exc).__name__}")
        goals = len(contexts)
        actions = sum(len(g.get("actions", [])) for g in contexts)
        how = f"from the {tier} cache" if tier else "(cold)"
        summary = f"{goals} goal(s), {actions} action(s) in {latency} ms {how}"
        return StageEvent(
            stage=S.done,
            ms=latency,
            summary=summary,
            detail={"contexts": contexts, "meta": meta.model_dump(mode="json")},
        )


def _llm_configured() -> bool:
    """True once an LLM key is set; until then rules-only output is the real answer and is cached."""
    try:
        from app.llm.router import available
    except ImportError:
        return False
    try:
        return available()
    except Exception:  # noqa: BLE001
        return False


# ---- cache ------------------------------------------------------------------------------------------
def _cache_detail(norm_query: str, slots: Slots, key: str, siis_hash: str | None, title: str | None) -> dict:
    return {
        "hit": False,
        "tier": None,
        "similarity": None,
        "threshold": settings.cache_sim_threshold,
        "norm_query": norm_query,
        "slots": slots.model_dump(mode="json"),
        "key": key,
        "siis_hash": siis_hash,
        "siis_title": title,
    }


def _serve_hit(
    run: _Run, hit, detail: dict, started: float, *, no_article: bool = False
) -> Iterator[StageEvent]:
    """A cached plan, re-validated (hard rule 3). `no_article`: the request carried none, so the answer
    is tagged fallback no_siis_context, source cached_plan."""
    body, report = validate_with_report({"contexts": (hit.plan or {}).get("contexts", [])})
    entry = store.entries().get(hit.key)
    matched = entry.query_texts[0] if entry and entry.query_texts else None
    detail.update(
        hit.as_detail(),
        query=run.query,
        matched_query=matched,
        siis_hash_match=not no_article,
        slots_match=True,
        validate=report,
    )
    where = "no-article plan table" if getattr(hit, "source", None) == "kit_table" else "cache"
    yield run.event(S.cache, started, f"{hit.tier.capitalize()} hit from the {where}", detail)
    if no_article:
        yield run.done(body["contexts"], tier=hit.tier, fallback=FALLBACK_NO_SIIS, source="cached_plan")
        return
    fallback = None if body["contexts"] else FALLBACK_NO_MATCH
    yield run.done(body["contexts"], tier=hit.tier, fallback=fallback)


# ---- cold path --------------------------------------------------------------------------------------
def _resolve_all(actions: list[DraftAction]) -> tuple[list[DraftAction], list[dict]]:
    out, links = [], []
    for action in actions:
        try:
            link = resolve(action)
        except Exception:  # noqa: BLE001 - no link is always a safe answer
            link = LinkDecision(tier=LinkTier.manual)
        candidates = []
        if action.screen_path:
            try:
                candidates = [
                    {"entry_id": entry_id, "score": round(score, 2)}
                    for entry_id, score in retrieval_search(action.screen_path, action.intent_verb, k=3)
                ]
            except Exception:  # noqa: BLE001
                candidates = []
        out.append(action.model_copy(update={"link": link}))
        links.append(
            {
                "action": action.name,
                "screen_path": action.screen_path,
                "intent_verb": action.intent_verb,
                "tier": link.tier.value,
                "node_id": link.node_id,
                "entry_id": link.entry_id,
                "confidence": link.confidence,
                "candidates": candidates,
            }
        )
    return out, links


def _section_relevance(sections: list[dict], sentence_section: dict[str, str]):
    """relevance_of(action): mean relevance, for the action's own intent, of the sections it cites."""
    by_heading = {s["heading"]: s["relevance"] for s in sections}

    def relevance_of(action: DraftAction) -> float:
        headings = {
            sentence_section[i] for step in action.steps for i in step.src_ids if i in sentence_section
        }
        values = [
            by_heading[h][action.intent_index]
            for h in headings
            if h in by_heading and action.intent_index < len(by_heading[h])
        ]
        return sum(values) / len(values) if values else 0.0

    return relevance_of


def _intent_relevance(intents: list[Intent], actions: list[DraftAction], relevance_of, sections: list[dict]):
    out = []
    for index, intent in enumerate(intents):
        values = [relevance_of(a) for a in actions if a.intent_index == index]
        if values:
            rel = sum(values) / len(values)
        else:
            rel = max((s["relevance"][index] for s in sections if index < len(s["relevance"])), default=0.0)
        out.append(intent.model_copy(update={"relevance": round(rel, 2)}))
    return out


def _ordered(actions: list[DraftAction]) -> list[DraftAction]:
    """Order within each intent, keeping intents in first-appearance order."""
    out: list[DraftAction] = []
    for index in dict.fromkeys(a.intent_index for a in actions):
        out += order([a for a in actions if a.intent_index == index])
    return out


def _cold(
    run: _Run,
    query_text: str,
    slots: Slots,
    siis_clean: str,
    key: str,
    siis_hash: str | None,
    *,
    wait_variations: bool = False,
    sink: dict | None = None,
    retrieved: "no_siis.RetrievedArticle | None" = None,
):
    """The full pipeline. `retrieved`: the request had no article and this remembered one matched it;
    scores are scaled by the match confidence and the answer is tagged as such."""
    # Free tier: the variations call starts now and runs next to extraction (never on the answer's path).
    try:
        future = start_variations(query_text)
    except Exception:  # noqa: BLE001 - templates stand in
        future = None

    # enrich
    t = time.perf_counter()
    try:
        intents, variations, enrich_detail = enrich_with_report(query_text, slots, templates=future is None)
    except Exception as exc:  # noqa: BLE001
        run.degraded.append(f"enrich:{type(exc).__name__}")
        intents, variations, enrich_detail = enrich_rules(query_text, slots, templates=future is None)
    run.add_llm(enrich_detail)
    if enrich_detail.get("degraded"):
        run.degraded.append("enrich:llm_failed")
    enrich_detail = {**enrich_detail, "variations_pending": future is not None}
    yield run.event(
        S.enrich,
        t,
        f"{len(intents)} intent(s), variations in the background"
        if future is not None
        else f"{len(intents)} intent(s), {len(variations)} variations kept "
        f"({len(enrich_detail.get('dropped_variations', []))} dropped)",
        enrich_detail,
    )

    # segment
    t = time.perf_counter()
    try:
        sentences, sections = segment_with_sections(siis_clean, intents)
    except Exception as exc:  # noqa: BLE001
        run.degraded.append(f"segment:{type(exc).__name__}")
        sentences, sections = [], []
    relevant = sum(1 for s in sections if s["relevant"])
    yield run.event(
        S.segment,
        t,
        f"{len(sections)} sections, {len(sentences)} sentences, {relevant} relevant",
        {
            "sections": sections,
            "sentences": [s.model_dump(mode="json") for s in sentences],
            "relevance_floor": settings.section_relevance_floor,
            "intent_titles": [i.title for i in intents],
        },
    )

    # extract
    t = time.perf_counter()
    try:
        actions, topics, info = extract_with_topics(intents, sentences, sections=sections, query=query_text)
    except Exception as exc:  # noqa: BLE001
        run.degraded.append(f"extract:{type(exc).__name__}")
        actions, topics = extract_rules(intents, sentences, sections)
        info = {"source": "rules", "model": None, "tokens_in": 0, "tokens_out": 0}
    run.add_llm(info)
    if info.get("degraded"):
        run.degraded.append("extract:llm_failed")
    rules_only = info.get("source") == "rules"
    if info.get("intents"):
        # Select mode: call B also split the complaint into problems; those become the intents, and
        # section relevance is scored against them (sentence ids are unchanged).
        try:
            intents = [Intent.model_validate(i) for i in info["intents"]]
            _, sections = segment_with_sections(siis_clean, intents)
        except Exception as exc:  # noqa: BLE001
            run.degraded.append(f"rescore:{type(exc).__name__}")
    proposed = sum(len(a.steps) for a in actions)
    yield run.event(
        S.extract,
        t,
        "No action: the article does not address this complaint"
        if info.get("no_match")
        else f"{len(actions)} actions, {proposed} steps proposed ({info.get('source')})",
        {"topics": topics, "actions": [a.model_dump(mode="json") for a in actions], **info},
    )

    # ground
    t = time.perf_counter()
    try:
        grounded, ground_report = ground_with_report(actions, sentences)
        if not grounded and not rules_only and sentences and not info.get("no_match"):
            # The LLM's steps did not survive: fall back to the article's own instructions.
            run.degraded.append("ground:llm_steps_dropped")
            actions, topics = extract_rules(intents, sentences, sections)
            grounded, ground_report = ground_with_report(actions, sentences)
            rules_only = True
    except Exception as exc:  # noqa: BLE001 - an ungrounded step is never shipped
        run.degraded.append(f"ground:{type(exc).__name__}")
        grounded, ground_report = [], {"proposed_steps": proposed, "kept_steps": 0, "coverage": 0.0}
    yield run.event(
        S.ground,
        t,
        f"{ground_report.get('kept_steps', 0)} of {ground_report.get('proposed_steps', 0)} steps grounded, "
        f"{len(ground_report.get('dropped_steps', []))} dropped",
        {**ground_report, "actions": [a.model_dump(mode="json") for a in grounded]},
    )

    # resolve
    t = time.perf_counter()
    resolved, links = _resolve_all(grounded)
    counts = {tier.value: sum(1 for x in links if x["tier"] == tier.value) for tier in LinkTier}
    yield run.event(
        S.resolve,
        t,
        f"{counts['catalog']} catalog, {counts['dummy']} dummy, {counts['manual']} manual",
        {"links": links, "counts": counts},
    )

    # categorize, order, multi-intent, compile, validate
    t = time.perf_counter()
    try:
        sentence_section = {s.id: s.section for s in sentences}
        relevance_of = _section_relevance(sections, sentence_section)
        actions = _ordered(categorize(resolved))
        actions, deduped = dedupe_with_report(intents, actions, relevance_of=relevance_of)
        intents = _intent_relevance(intents, actions, relevance_of, sections)
        goals, compile_report = compile_with_report(
            intents,
            actions,
            topics=topics,
            coverage=ground_report.get("coverage_by_intent"),
            score_cap=settings.rules_only_score_cap if rules_only and _llm_configured() else None,
        )
        body, validate_report = validate_with_report({"contexts": goals})
    except Exception as exc:  # noqa: BLE001
        run.degraded.append(f"compile:{type(exc).__name__}")
        body, deduped = {"contexts": []}, []
        compile_report, validate_report = {"score_inputs": []}, {"schema_valid": True}
    contexts = body["contexts"]
    detail = {
        **compile_report,
        "goals": len(contexts),
        "actions": sum(len(g["actions"]) for g in contexts),
        "deduped": deduped,
        **validate_report,
    }
    yield run.event(
        S.compile,
        t,
        f"{detail['goals']} goal(s), {detail['actions']} actions, "
        f"{validate_report.get('url_leaks', 0)} URL leaks, schema {'valid' if validate_report.get('schema_valid') else 'repaired'}",
        detail,
    )

    fallback = None if contexts else FALLBACK_NO_MATCH
    if retrieved is not None:
        # The article was matched, not given: the score says how sure that match is.
        for goal in contexts:
            goal["score"] = round(min(1.0, max(0.0, goal["score"] * retrieved.similarity)), 2)
        fallback = FALLBACK_NO_SIIS
    answer_ready_ms = run.elapsed_ms()
    if future is not None:
        if wait_variations:
            wait([future], timeout=settings.variations_budget_s)  # errors surface in finish_variations
        if future.done():
            variations, variations_dropped, variations_info = finish_variations(query_text, slots, future)
            if sink is not None:
                sink["variations_dropped"], sink["variations_info"] = variations_dropped, variations_info
            run.add_llm(variations_info, answer_model=False)
    # Every valid answer is cached, a degraded one too: it is grounded and already score-capped, and the
    # scorer's repeat of a query must be fast and identical (A3, deterministic repeats). Measured: not
    # caching degraded answers cost 2/20 repeat hits and one changed plan on the free tier.
    degraded = rules_only and _llm_configured()
    if contexts:

        def entry(texts: list[str]) -> CacheEntry:
            return CacheEntry(
                key=key,
                siis_hash=siis_hash,
                slots=slots,
                # A rules-only answer while a model is configured is marked, so it can be retried later.
                plan={"contexts": contexts, **({"degraded": True} if degraded else {})},
                query_texts=texts,
            )

        try:
            if future is not None and not future.done():
                # The LLM variations land after the answer. Until then only the query itself is indexed:
                # the template stand-ins are generic ("Why is my screen black?") and would stay in the
                # index next to the real variations, widening what a near miss can match.
                cache.put(entry([query_text]))
                future.add_done_callback(lambda f: _late_variations(f, query_text, slots, entry))
            else:
                cache.put(entry([query_text, *variations]))
        except Exception:  # noqa: BLE001 - a failed cache write only costs the next request time
            run.degraded.append("cache_write")
    if sink is not None:
        sink["variations"] = variations
    source = "retrieved_article" if retrieved is not None else None
    yield run.done(contexts, fallback=fallback, latency=answer_ready_ms, source=source)


def _late_variations(future, query_text: str, slots: Slots, entry) -> None:
    try:
        variations, _, _ = finish_variations(query_text, slots, future)
        cache.put(entry([query_text, *variations]))
    except Exception:  # noqa: BLE001 - the plan is already cached; only paraphrase hits are lost
        return


def run_stream(
    query: str,
    siis: dict | str | None,
    *,
    wait_for_variations: bool = False,
    sink: dict | None = None,
) -> Iterator[StageEvent]:
    """Same pipeline as run(), yielding one StageEvent as each stage finishes.

    Order: cache -> enrich -> segment -> extract -> ground -> resolve -> compile -> done. On a cache hit
    yield cache then done. `done.detail` is the full response body (contexts + meta). Detail shapes per
    stage are in data/fixtures/README.md. `wait_for_variations` holds `done` until the background
    variations are in (results.jsonl needs them; the API never waits); `sink` receives them.
    """
    run = _Run(query)
    try:
        yield from _run_stream(run, query, siis, wait_for_variations, sink)
    except GeneratorExit:
        raise
    except Exception as exc:  # noqa: BLE001 - always end on a valid, empty answer
        run.degraded.append(f"pipeline:{type(exc).__name__}")
        yield run.done([], fallback=FALLBACK_NO_MATCH)


def _run_stream(
    run: _Run, query: str, siis: dict | str | None, wait_variations: bool, sink: dict | None
) -> Iterator[StageEvent]:
    t = time.perf_counter()
    readiness.ensure()  # scripts and tests may call the pipeline without the API's startup warm-up
    norm_query = normalize_query(query)
    query_text = display_query(query)  # scrubbed: links and addresses never reach an LLM
    siis_clean, siis_hash = clean_siis(siis)
    try:
        slots = extract_slots(norm_query)
    except Exception:  # noqa: BLE001 - missing slots act as wildcards in the cache guard
        slots = Slots()
    key = cache.make_key(norm_query, siis_hash)
    detail = _cache_detail(norm_query, slots, key, siis_hash, siis_title(siis))

    if not siis_clean:
        yield from _no_article(run, t, query_text, norm_query, slots, key, detail, wait_variations, sink)
        return
    try:
        no_siis.remember_article_later(siis_clean, siis_hash, siis_title(siis) or "")
    except Exception:  # noqa: BLE001 - memory is an extra; the request goes on
        run.degraded.append("article_memory")

    hit = _lookup(norm_query, slots, siis_hash)
    if hit is not None:
        yield from _serve_hit(run, hit, detail, t)
        return

    with store.single_flight(key):
        hit = _lookup(norm_query, slots, siis_hash)  # an identical request may have just solved it
        if hit is not None:
            yield from _serve_hit(run, hit, detail, t)
            return
        yield run.event(S.cache, t, "Cache miss (no exact key, no semantic match)", detail)
        if not (slots.component or slots.symptom or recognisable(norm_query, siis_clean)):
            # Nothing in the complaint can be read, so any plan would be a guess: no_match, no LLM call.
            run.degraded.append("query:unrecognisable")
            yield run.done([], fallback=FALLBACK_NO_MATCH)
            return
        yield from _cold(
            run,
            query_text,
            slots,
            siis_clean,
            key,
            siis_hash,
            wait_variations=wait_variations,
            sink=sink,
        )


def _no_article(
    run: _Run,
    t: float,
    query_text: str,
    norm_query: str,
    slots: Slots,
    key: str,
    detail: dict,
    wait_variations: bool,
    sink: dict | None,
) -> Iterator[StageEvent]:
    """No article: the same question answered before, a close cached plan, or the pipeline over a
    remembered article. Otherwise empty - the LLM never fills the gap (FAQ Q14, gate G5)."""
    detail["threshold"] = settings.no_siis_plan_threshold
    try:
        plan = exact_tier.get(key)
        hit = cache.CacheHit(plan, tier="exact", key=key) if plan is not None else None
        hit = hit or no_siis.lookup_no_siis(norm_query, slots)
    except Exception:  # noqa: BLE001 - a broken lookup only means no cached answer
        hit = None
    if hit is not None:
        yield from _serve_hit(run, hit, detail, t, no_article=True)
        return
    try:
        article = no_siis.find_article(norm_query)
    except Exception:  # noqa: BLE001
        article = None
    if article is None:
        yield run.event(
            S.cache, t, "No article, and no cached plan or remembered article is close enough", detail
        )
        yield run.done([], fallback=FALLBACK_NO_SIIS)
        return
    detail["retrieved_article"] = {
        "title": article.title,
        "siis_hash": article.siis_hash,
        "similarity": round(article.similarity, 3),
        "threshold": settings.article_match_threshold,
    }
    yield run.event(
        S.cache,
        t,
        f"No article: matched the remembered article '{article.title}' ({article.similarity:.2f})",
        detail,
    )
    with store.single_flight(key):
        yield from _cold(
            run,
            query_text,
            slots,
            article.text,
            key,
            None,
            wait_variations=wait_variations,
            sink=sink,
            retrieved=article,
        )


def _lookup(norm_query: str, slots: Slots, siis_hash: str | None):
    try:
        hit = cache.lookup(norm_query, slots, siis_hash)
    except Exception:  # noqa: BLE001 - a broken cache means a cold run, not an error
        return None
    if hit is not None and (hit.plan or {}).get("degraded") and _llm_configured():
        # A rules-only answer (every model was busy) repeats identically for a while, then the next
        # ask gets a cold run and a model's answer replaces it. Without this one busy minute on the
        # free tier would pin the weaker plan to that question for good.
        entry = store.entries().get(hit.key)
        if entry is not None and time.time() - entry.created_at > settings.degraded_cache_ttl_s:
            return None
    return hit


def run(query: str, siis: dict | str | None) -> dict:
    """The response body ({contexts, meta}) for one request. Never raises, never waits for variations."""
    body, _ = _drain(query, siis, wait=False)
    return body


def run_with_variations(query: str, siis: dict | str | None) -> tuple[dict, list[str]]:
    """(body, the 8-10 query variations) — results.jsonl needs both, so this waits for them."""
    return _drain(query, siis, wait=True)


def _drain(query: str, siis: dict | str | None, *, wait: bool) -> tuple[dict, list[str]]:
    last = None
    sink: dict = {}
    variations: list[str] = []
    for event in run_stream(query, siis, wait_for_variations=wait, sink=sink):
        if event.stage is S.enrich:
            variations = list(event.detail.get("variations") or [])
        last = event
    variations = sink.get("variations") or variations
    if last is None or last.stage is not S.done:
        return {
            "contexts": [],
            "meta": ResponseMeta(fallback=FALLBACK_NO_MATCH).model_dump(mode="json"),
        }, variations
    return last.detail, variations
