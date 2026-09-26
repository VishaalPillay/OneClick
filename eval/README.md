# Evaluation harness

This is our own copy of the organisers' automated scorer, plus the quality measurements that feed `docs/metrics.md`.

The checks are deliberately **independent of the engine**. Nothing in `eval/` imports `app.compiler` or `app.pipeline`, so a bug in the engine's scrub or trimmer shows up here instead of being checked by itself. The one shared file is the official `api/app/schema.py`.

The exception is `evalkit/engine.py`, which loads the mapping lane's resolver and cache in-process so `ablation.py` and `loadtest.py --mode cache` can *measure* them. It never feeds a check.

## Setup

Run everything from the repo root:

```bash
pip install -r api/requirements.txt -r eval/requirements.txt
pytest eval/tests                      # unit tests for the checkers and the sets
python eval/sets/validate_sets.py      # schema and sanity checks on the sets + gold
ruff check eval && ruff format --check eval
```

## Gate replica

```bash
# Offline: audit a results file (G3, G4, G5, A1, A2, A5)
python eval/gate_replica.py --results results.jsonl

# Live: against a freshly started API (G2, G4, G5, A1-A4). Restart the API first so the SIIS cache is empty.
python eval/gate_replica.py --api http://localhost:8000

# Both, failing the run if an enforced gate fails or was not measured
python eval/gate_replica.py --api http://localhost:8000 --results results.jsonl --enforce G2,G3,G4,G5
```

In live mode, each kit query is sent twice, first cold and then as a repeat, with its own SIIS article. Up to 60 paraphrases from `sets/paraphrases.jsonl` are sent with the same article, and every `sets/unseen.jsonl` scenario is sent once. The report is printed and written to `eval/results/gates.json`.

| Check | Rule (source) | Enforced as |
| --- | --- | --- |
| G2 | `/health` returns 200 `{"status":"ok"}` (FAQ Q9) | gate |
| G3 | ≥ 95% of kit queries have a line in the results file (FAQ Q9) | gate |
| G4 | ≥ 90% of responses validate against `schema.py` (FAQ Q9) | gate |
| G5 | Zero URLs anywhere: schemes, `www.`, `.com`/`.html`-style domains, emails, markdown links and images, HTML link tags. `bixby://` is allowed only inside deeplink fields (FAQ Q6, spec 4.2) | gate |
| A1 (15) | Schema, goal regex, title 2–3 words, description "It will" + 5–7 words, no leaks, score 0–1 | mean pass rate × 15 |
| A2 (15) | Deeplink in the catalog (or `dummy_positive`), validation is the entry's **own** and unchanged; auto actions have a link | (validity + auto-link rate) / 2 × 15 |
| A3 (15) | Repeat p95 ≤ 300 ms with ≥ 90% hits; paraphrase hits ≥ 80%; cold p95 ≤ 8 s | 5 per sub-target met |
| A4 (10) | Unseen scenarios give valid, non-empty responses | share × 10 |
| A5 (5) | 8–10 unique variations per line; mean pairwise token Jaccard reported | share × 5 |

The points are **our estimate**. The organisers publish the blocks and targets, but not the weighting inside each block.

Also reported: always-200, pure JSON, identical plans on a repeat call, warnings for style rules, and whether the cache was warm before the "cold" call.

### Goal regex: `--goal-mode`

The FAQ gives the goal as `Follow these steps to perform this <Name> Troubleshooting.`, with a trailing period. `sample_output.json` and the spec's Appendix B omit the period.

The default, `strict`, follows the FAQ, as the compiler does. `--goal-mode lenient` accepts both forms. This is still an open question with the organisers.

Note: the kit's own `sample_output.json` also breaks the 5–7-word description rule, with 9- and 12-word descriptions. Treat it as a reference for the *shape* of a response, not for its rules.

## Test sets and gold labels

`sets/` holds the held-out inputs (200 paraphrases, 60 near misses, 15 unseen scenarios, 15
adversarial cases) and `data/gold/deeplink_gold.jsonl` the hand-labelled link answers. Both have
their own README; `python eval/sets/validate_sets.py` guards them and runs in CI.

The gold set is labelled with `tools/label_gold.py`, which shortlists candidates using
`evalkit/bm25.py` rather than the engine's retriever — grading a resolver with labels the same
resolver produced would only measure self-agreement. On the 24 catalog-tier labels committed so
far, that BM25 scores 67% precision@1, which is the baseline the Screen Graph has to beat.

## Measurements for `docs/metrics.md`

The mapping and cache runs load the Screen Graph and vector index, so build them once first (from `api/`: `python scripts/build_screengraph.py && python scripts/build_index.py`; they land in the gitignored `data/build/`).

```bash
python eval/ablation.py                 # section 5: mapping variants on the gold set -> results/ablation.json
python eval/loadtest.py --mode cache    # sections 3-4: cache paths in-process      -> results/loadtest.json
python eval/loadtest.py --mode api --api http://localhost:8000   # adds cold, over HTTP (needs the engine)
python eval/judge.py                    # section 2: step accuracy on results.jsonl  -> results/judge.json
python eval/report.py                   # rewrites docs/metrics.md in the Appendix C layout
```

For honest cold numbers, start the API on an empty cache: `ONECLICK_SQLITE=/tmp/cold.sqlite uvicorn app.main:app` from `api/`, with the LLM keys in `.env`. The API mode waits `--settle` seconds (default 12) after the cold pass, because each answer's 8–10 variations are generated in the background and paraphrase hits depend on them.

In the API mode's near-miss pass, the engine caches every answer it computes, so a near miss that misses runs cold and a later near miss on the same article can hit *that* answer. Each hit is therefore classified by the plan it served, compared with the plans from earlier in the run: a kit answer (the row's own, or another row's with the same article), an earlier near miss's answer, or a paraphrase's cold answer. Only kit answers count toward the ≤ 2% false-hit target; the others are reported in `near_miss.hits_by_source` and `leaked`. The in-process `--mode cache` pass never stores near misses, so it needs no such split.

**`ablation.py`** maps every gold step with each variant and scores them the same way:

| Row | Variant | What it is |
| --- | --- | --- |
| Baseline | `llm` | Whole catalog in a Mistral prompt; the model picks an id, `DUMMY` or `MANUAL`. Needs `MISTRAL_API_KEY`. |
| Variant A | `hybrid` | BM25 + dense fusion over raw catalog entries, top hit. No Screen Graph, polarity or tiers. |
| Variant B | `rules` | Physical-step keywords, exact screen-name match, verb → entry type. |
| extra | `bm25` | `evalkit/bm25.py` alone. |
| ours | `screengraph` | The shipping `resolve()`: hybrid search, Screen Graph polarity, catalog / dummy / manual tiers. |

Deeplink relevance (0–2) follows `evalkit/relevance.py`: 2 for the exact entry, 1 for the right screen with the wrong control or a labelled parent menu, 0 for a wrong screen or any link on a physical step. Results are split by labeller, because the mapping lane tuned its thresholds on this file.

**`loadtest.py --mode cache`** warms the real cache with the 20 kit queries (original phrasing only, so the paraphrase hit rate is a lower bound), then times ≥ 30 lookups per path and counts false hits on the near-miss set. `--sweep` repeats it across similarity thresholds.

**`judge.py`** asks an LLM to grade each plan against its complaint and article: a verdict per step (`correct` / `partial` / `wrong`, with the main issue: `not_an_instruction`, `fragment`, `irrelevant`, `unsupported`, `duplicate`, `wrong_action`), 0–2 per catalog link, the article fixes the plan leaves out, whether the order works, and a 0–3 score for completeness, correctness and ordering. The rubric is `prompts/judge.v2.md`: v1 plus the organisers' ordering rule (critical actions after every other action, contacting support included), which v1 marked as a problem on plans the spec requires it of. The step issues are the part to read when tuning the extraction prompt.

```bash
python eval/judge.py --dry-run                     # print the first prompt; no key needed
python eval/judge.py                               # the 20 plans in results.jsonl
python eval/judge.py --api http://localhost:8000   # kit + the 15 unseen scenarios, live
```

The judge should not grade its own work: it uses Gemini when `GEMINI_API_KEY` is set and Mistral otherwise (`--provider`, `--model` override), and any plan written by the judge's model family is counted as self-graded in the output and the report. A plan with no steps scores 0 without a call. Judgments are cached in `results/judge_cache.json`, so a re-run after an engine change only pays for the plans that changed. Failed calls are retried with backoff and then reported as unjudged, never scored.

**`report.py`** fills only what a run measured. An empty plan passes every format rule trivially, so section 1 stays "not measured" until the engine returns non-empty plans.

## CI

`.github/workflows/ci.yml` runs on every PR:

- the api tests and lint
- the eval tests and lint
- the set and gold validator
- the gate replica against a fresh `uvicorn`, with **G2, G4 and G5 enforced** and the rest reported

The `gates.json` report is uploaded as an artifact.

## Layout

| Path | What |
| --- | --- |
| `evalkit/checks.py` | Per-response and per-results-line rules, with a URL-leak scanner |
| `evalkit/catalog.py` | Deeplink validity against `data/kit/deeplinks.json` |
| `evalkit/client.py` | HTTP client that records latency, cache flag and tier the way a scorer would |
| `evalkit/sets.py`, `evalkit/stats.py` | Kit and set loaders; percentiles, Jaccard, query normalisation |
| `evalkit/bm25.py` | Standalone BM25 over the catalog, used to shortlist gold candidates and as an ablation variant |
| `evalkit/relevance.py` | Gold loader and the 0–2 deeplink relevance rubric |
| `evalkit/mappers.py` | Rules, BM25 and LLM deeplink mappers for the ablation |
| `evalkit/engine.py` | In-process adapter to the engine's resolver and cache (measurement only) |
| `gate_replica.py` | G2–G5 and A1–A5 |
| `ablation.py` | The mapping ablation (metrics.md sections 2 and 5) |
| `loadtest.py` | Latency at N ≥ 30 per path, cache hit and false-hit rates (sections 3 and 4) |
| `report.py` | Writes `docs/metrics.md` in the Appendix C layout |
| `judge.py`, `prompts/judge.v2.md` | LLM judge: step accuracy 0–3, per-step issues, end-to-end link relevance 0–2 (section 2) |
| `sets/` | Paraphrase, near-miss, unseen and adversarial sets (see `sets/README.md`) |
| `sets/validate_sets.py` | Validates the four sets and `data/gold/deeplink_gold.jsonl`; runs in CI |
| `tools/label_gold.py` | Interactive labelling helper for the gold set (see `data/gold/README.md`) |
| `results/` | JSON outputs of the runs above, read by `report.py` |
