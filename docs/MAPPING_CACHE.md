# Mapping, cache & infra (Karur's lane)

What is built, how it behaves, what the numbers are, and what the other lanes call.
Everything here is measured, not estimated; every figure has the command that produced it.
Numbers as of the engine freeze (2026-09-25).

## What the lane does

```
Vishaal's extractor            YOU                         Vishaal's compiler
   DraftAction        ->   resolve(action)        ->     LinkDecision
   (screen_path,           catalog / dummy /             (tier, entry_id,
    intent_verb,           manual                         node_id, confidence)
    steps)

   request in         ->   cache.lookup(...)      ->     CacheHit | None
   (norm_query,            tier 0 exact, then            (plan, tier, similarity)
    slots, siis_hash)      tier 1 semantic
```

## The API the other lanes call

| Call | Returns | Used by |
| --- | --- | --- |
| `screengraph.resolver.resolve(action)` | `LinkDecision(tier, node_id, entry_id, confidence)` | pipeline, after grounding |
| `pipeline.slots.extract_slots(norm_query)` | `Slots(component, symptom, intent)` | pipeline, cache guard |
| `cache.lookup(norm_query, slots, siis_hash)` | `CacheHit \| None` | pipeline, before any LLM call |
| `cache.put(CacheEntry)` | — | pipeline, after compile |
| `cache.init()` | entries loaded from the snapshot | startup |
| `cache.no_siis.lookup_no_siis(...)`, `find_article(...)` | a plan, or a remembered article | pipeline, requests with no article |
| `retrieval.dense.embed(texts)` | L2-normalized vectors | grounding, variation filtering, both caches |
| `obs.metrics.record(trace, latency_ms, fallback)` | — | once per served request |
| `obs.trace.new_trace()` / `put(trace)` | `Trace` / — | pipeline |
| `GET /v1/metrics`, `/v1/traces`, `/v1/trace/{id}` | rolling window, recent ids, last 200 traces | console, eval |
| `screengraph.resolver.validation_for(entry_id)` | the entry's own validation object | no caller: the compiler reads the catalog itself (`compiler/catalog.py`) |

`LinkDecision.tier` is `catalog`, `dummy` or `manual`. On `dummy` the entry id is `DL-DUMMY`
and the compiler writes its own 5-7 word description naming the screen. On `manual` there is no
deeplink and the categorizer decides `manual` vs `critical`.

## How a link is chosen

1. **Off-screen check.** Physical and external steps (restart, visit a service centre, clean the
   port, charge) never reach the search. A Settings path with an in-app verb is always searched,
   whatever words the step contains, so "turn on Fast charging for the cable charger" is not
   mistaken for a physical step.
2. **BM25** over the cleaned catalog (`message` and `qna_description` weighted 2x).
3. **Dense embeddings** (BAAI/bge-small-en-v1.5, ONNX on CPU) over the same documents.
4. **Reciprocal rank fusion** (k = 60) merges the two by position, because a BM25 score of 18.6
   and a cosine of 0.86 are not comparable.
5. **Adjustments** on the fused score (normalised so the best hit is 1.00):
   - leaf-name match, up to +0.60, two-way so an entry's extra words count against it
     ("Storage" must not score full marks against "Storage Share")
   - polarity +0.25 when the entry type matches the verb, -0.35 when it is the opposite toggle
   - surface -0.30 for TV / Samsung Members entries
6. **Tier decision**: >= 1.60 (of 1.85) is a catalog link, otherwise a Settings path gets
   `dummy`, anything vaguer gets `manual`.
7. **Screen Graph** corrects the on/off choice inside the matched screen.

What each part is worth, on the 87 gold steps that have a catalog answer
(`scripts/eval_retrieval.py` with the flag shown):

| Configuration | precision@1 | Per step |
| --- | --- | --- |
| As shipped | **91%** | ~5 ms |
| Without polarity (`--no-polarity`) | 84% | |
| Searching screens instead of entries (`--graph`) | 86% | |
| Cross-encoder on every step (`--rerank`) | 87% | 150 ms |

### Cross-encoder rerank: implemented, disabled

`settings.use_rerank = False`. On the 123-step gold it lowers precision@1 from 91% to 87% and
costs 150 ms per step against ~5 ms. The catalog is terse fragments ("power saving mode"),
which is not what the model was trained on. Re-measure with
`python scripts/eval_retrieval.py --rerank` if the gold set grows.

## Screen Graph

577 catalog entries cluster into **413 screens** by shared validation deeplink, confirmed by
identical cleaned description (the two signals agreed on 419 vs 420 groups before merging).

```
SN-0267 "power saving mode"
    off  -> DL-0411    on -> DL-0412    open -> DL-0519
```

It is **not** the search index: searching screens instead of entries measures 86% precision@1
against 91%. It is kept for the on/off correction, the console's Screen Graph explorer, and the
scaling story (steps map to screens, screens map to links).

## Cache

```
Tier 0  exact     key = sha256(prompt_version | norm_query | siis_hash)[:16]     ~1 ms
Tier 1  semantic  cosine over every stored phrasing (original + variations)      ~8 ms
        a hit needs  similarity >= 0.70  AND  slots agree  AND  same siis_hash
```

- Every variation of a solved query is indexed pointing at the same plan. That is the whole of
  the paraphrase hit rate.
- **The slot guard** (`cache/slot_guard.py`) is what stops "screen is black" answering "screen
  is cracked", which embeddings rate 0.91 alike. The order matters: incoming query first, cached
  entry second.
  - *component*: two filled values that differ block the hit (battery is not screen).
  - *intent*: `fault` and `configure` never match, so "I want my screen to go black while Smart
    Switch runs" does not get the black-screen fault plan. An entry cached before the field
    existed has none, and that matches anything.
  - *symptom, one-sided*: when the query names a symptom and the entry has none, it misses.
    The reverse stays a wildcard, because a terse paraphrase often names no symptom at all:
    making it strict dropped paraphrase hits from 82% to 54%.
- **The article hash must match**, so the same question with a different article never gets a
  stale plan.
- SQLite snapshot for restarts, single-flight lock so identical concurrent misses compute once.
- The SIIS cache **ships empty**: the scorer's first call has to be genuinely cold.
- **Prompt version**: only the exact key contains it. The semantic tier matches on similarity,
  slots and article hash, so after a prompt change an old snapshot would still serve old plans.
  Start from an empty cache after a prompt change (see Running it).

### Threshold: 0.70

Decided 2026-09-25 on a 0.55-0.85 sweep. `python scripts/eval_cache.py --sweep`, on the real
cache (the 20 kit plans and their LLM variations from `results.jsonl`, keyed on the real article
hashes) against the eval lane's 200 paraphrases and 60 near misses:

| Threshold | Paraphrase hits (A3: >= 80%) | Served a sibling row's plan | Near-miss false hits |
| --- | --- | --- | --- |
| **0.70** | **84%** | 2 | 1/60 |
| 0.75 | 82% | 2 | 1/60 |
| 0.80 | 79% | 1 | 1/60 |
| 0.85 | 73% | 0 | 1/60 |

With the guards in place the false hits no longer move with the threshold, so the threshold
only trades paraphrase hits. The two sibling-plan hits are rows that share an article (the 20
kit rows use 11): a misspelt "screen stays blank" paraphrase served another blank-screen row's
plan, and a "cracked at the hinge" paraphrase served the "screen completely cracked" plan.
Neither mixes up two faults. The one near-miss hit is "I want to *add* a floating circle"
against the kit row "I want to *remove* it": both are `configure`, so no slot tells them apart.

## No article (`cache/no_siis.py`)

Written by Vishaal during the freeze. A request with no usable article is never answered from
nothing:

1. **A cached plan**: the kit table (the 20 kit plans and their variations, loaded from
   `results.jsonl` at startup) or any plan this server has solved, at similarity >= 0.80 and
   passing the slot guard.
2. **A remembered article**: every article the API has received (the 11 kit articles are
   pre-loaded). If one is clearly closest (>= 0.82 and 0.04 ahead of the runner-up), the full
   pipeline runs over it.
3. **Otherwise empty**, with `fallback: no_siis_context`.

The image copies `results.jsonl` to `/data/results.jsonl`; without it the kit table is empty.
Measured in the Docker image: all 20 kit queries sent with no article are answered from the kit
table, and an unrelated complaint comes back empty.

## Slots

`data/slot_lexicon.json`: 10 components, 12 symptoms, 13 intent phrases. Phrase match only, no LLM.

- **Earliest mention wins**, because a complaint names its subject first: "my screen went black
  ... cannot use Smart Switch" is a screen problem, not an app problem.
- **An app is a weak component**: "opening an email in Gmail makes the screen flash" is a screen
  problem. `app` wins only when no real part is named (7 of 200 paraphrases needed this).
- **Denied phrases are ignored**: "there is no physical damage" must not read as `cracked`.
- Longer phrase breaks a tie, so "touch screen" beats "screen".
- **Intent** is `configure` when the query asks for a behaviour ("I want", "is there a way to",
  "how do I set / keep / turn / make") and `fault` otherwise. "How do I fix my black screen"
  stays a fault.

All 20 kit queries read as `screen`; 18 also get a symptom; 19 are `fault` and 1 is `configure`
(row 12, "I want to remove it"). Grow the lexicon freely: it is data, not code.

## Measured results

| Metric | Target | Measured |
| --- | --- | --- |
| Deeplink precision@1 (the 87 of 123 gold steps with a catalog answer) | - | **91%** (79/87) |
| Recall@3 | - | 94% (82/87) |
| Served link on those 87 steps | - | 77 right, 3 wrong, 7 declined to `dummy` |
| Wrong link attached, all 123 steps | as low as possible | **4%** (5/123) |
| `resolver_cases.json` (Vishaal's contract) | 12/12 | **12/12**, no `must_not_match` hit |
| Physical steps refused before search | all | 14/14, **0** false refusals |
| Article-worded gold (30 steps, 8 with a catalog answer) | - | 75% p@1, 88% r@3; 9/9 physical refused, 1 false refusal |
| Cache repeat hit | >= 90% | **100%** at ~1 ms |
| Cache paraphrase hit | >= 80% | **84%** (168/200) at ~8 ms |
| Cache near-miss false hits | <= 2% | **1.7%** (1/60) |
| Resolve latency | <= 500 ms budget | ~5 ms per step |
| Boot to healthy (Docker) | < 20 s | 8 s, including the ~2 s no-article pre-warm |

The article-worded false refusal is "Quick settings > Wi-Fi": the off-screen rule only trusts a
path that starts at Settings.

### How much to trust these

87 of the 123 gold steps were labelled by this lane, so they flatter. On the 36 steps labelled
independently by the eval lane, which include deliberate lookalikes ("Wi-Fi" vs "Wi-Fi
scanning"), 20 of the 24 catalog steps get the right link and 4 of the 36 get a wrong one; on
this lane's own 87 it is 57 of 63 and 1 of 87. The honest numbers are those, plus **12/12 on
`resolver_cases.json`**, which Vishaal wrote with traps. The cache sets (`eval/sets/`) are the
eval lane's, not ours.

## Running it

```bash
# whole service, no Python setup needed
docker compose up -d --build          # /health 200 about 8 s after start
curl localhost:8000/health
curl localhost:8000/v1/metrics

# redeploy with an empty cache (after any prompt change): a new container has no cache.sqlite
docker compose up -d --build --force-recreate

# local development
cd api && pip install -r requirements.txt
python scripts/build_screengraph.py   # 577 entries -> 413 screens
python scripts/build_index.py         # embeds the catalog once -> data/build/
uvicorn app.main:app --reload
pytest                                # 189 tests, the whole API suite

# measurement
python scripts/eval_retrieval.py                 # 123-step team gold
python scripts/eval_retrieval.py --articles      # article-worded gold
python scripts/eval_retrieval.py --graph         # ablation: search screens, not entries
python scripts/eval_retrieval.py --no-polarity   # ablation: what polarity is worth
python scripts/eval_cache.py --sweep             # threshold sweep
```

`data/build/` is gitignored; the Docker image builds it at image-build time, so a container
starts warm and needs no network. The compose file mounts no volume, so the cache lives and dies
with the container. A local run keeps `cache.sqlite` in the directory it starts from and reloads
it at boot: delete it before measuring cold numbers or after a prompt change.

## Files

| Path | What |
| --- | --- |
| `api/app/retrieval/` | bm25, dense (+ save/load), fuse, rerank (off), scoring in `__init__` |
| `api/app/screengraph/` | `clean` (boilerplate, polarity, surface), `build` (clustering), `resolver` |
| `api/app/cache/` | `exact`, `semantic`, `slot_guard`, `store` (SQLite), `no_siis` (no-article path) |
| `api/app/pipeline/slots.py` | the only file this lane owns under `pipeline/` |
| `api/app/obs/` | `metrics`, `trace`, `logging`, `readiness` |
| `api/scripts/` | `build_screengraph`, `build_index`, `eval_retrieval`, `eval_cache` |
| `api/Dockerfile`, `docker-compose.yml`, `.dockerignore` | the image; `tests/test_docker.py` checks it ships every runtime data file |
| `data/gold/` | 123-step team gold, 30-step article-worded gold, the older 6-query cache set (`eval_cache.py --authored`) |
| `data/slot_lexicon.json` | the slot word lists |

## Still open in this lane

- **Device simulator** (`device/`, `routes/device.py`): cut from the submission (see the last
  section of [ARCHITECTURE.md](ARCHITECTURE.md)). Note the catalog quirk: only the 138 `onURL`
  entries carry condition and value, so only those could show "Verified".
- **`data/appliance_exclusions.json`** is an empty list and `screengraph/clean.py` does not read
  it yet: only `DL-DUMMY` is dropped (578 -> 577), and TV entries are score-penalised instead.
- **Prompt version on the semantic tier** (see Cache): handled by redeploying with an empty cache.
