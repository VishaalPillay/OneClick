# CLAUDE.md

Guidance for Claude Code (and humans) working in this repository. Shared by the whole team — keep it accurate, and update it in the same PR that changes the behaviour it describes.

## What this is

OneClick is a Smart Guided Troubleshooting engine for the Samsung PRISM GenAI Hackathon 2026 (Theme 2). A vague user complaint plus a SIIS knowledge article go in; a grounded, schema-valid troubleshooting plan with verified Galaxy Settings deeplinks comes out.

The engine is complete end to end and in freeze for the tag. Each module's docstring states which design component it implements (C1–C12); [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) is the design, and its last section lists where the submission differs from it. The only stubs left are the device simulator (`device/simulator.py`, `routes/device.py`), which is cut from the submission and on the roadmap.

## Commands

All API commands run from `api/` (pytest sets `pythonpath = ["."]`, so `app.*` imports resolve from there).

```bash
# API, local
cd api
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload                        # http://localhost:8000
pytest                                               # whole suite
pytest tests/test_api.py::test_health                # single test
ruff check . && ruff format --check .                # lint (line-length 110, target py310)

# Full stack: API on :8000, site on :3000 (ONECLICK_CONSOLE_PORT to move it)
cp .env.example .env                                 # GEMINI_API_KEY, MISTRAL_API_KEY
docker compose up -d --build && curl localhost:8000/health

# Offline builds (run from api/, in this order, before the API can resolve links)
python scripts/build_screengraph.py   # clean catalog -> data/build/screengraph.json
python scripts/build_index.py         # dense vectors over catalog entries (BM25 is built at startup)
python scripts/make_results.py        # cold run over data/kit -> results.jsonl

# Evaluation (run from repo root, against a running API)
python eval/sets/validate_sets.py  # test sets + gold labels; runs in CI, so edit a set and check this
python eval/tools/label_gold.py --owner <you>   # label your ~33 of data/gold/deeplink_gold.jsonl
python eval/gate_replica.py --api http://localhost:8000 --results results.jsonl   # G2-G5 + A1-A5, each query twice on an empty cache
python eval/judge.py          # step accuracy 0-3, deeplink relevance 0-2
python eval/loadtest.py --mode api --api http://localhost:8000   # p50/p95 for repeat-hit, paraphrase-hit, cold
python eval/report.py         # regenerates docs/metrics.md
python eval/tools/record_story.py   # re-records the site's walkthrough from the real engine (spends quota); rebuild console/

# Console stream: runs the real pipeline, so it spends LLM quota (run from repo root)
curl -N -X POST localhost:8000/v1/troubleshoot/stream -H "Content-Type: application/json" \
     -d @data/fixtures/touch_lag/request.json          # ?mock= only applies with settings.stream_mock on

# Console (Next.js 16 + React 19 + Tailwind v4 — see console/README.md)
cd console
npm install
npm run dev                   # http://localhost:3000
npm run lint && npm run typecheck && npm run build
```

`test_all_modules_import` in [api/tests/test_api.py](api/tests/test_api.py) walks every package: a syntax error or bad import anywhere in `app/` fails the suite, even in code nobody calls yet.

## Architecture

One request through [api/app/pipeline/run.py](api/app/pipeline/run.py):

```
normalize -> cache lookup -> enrich (LLM A) -> segment -> extract (LLM B)
          -> ground -> resolve -> categorize -> order -> multi-intent
          -> compile -> validate -> cache write
```

- **normalize / slots** — whitespace and numbering fixes (every line of a numbered kit query), URL/email scrub of the SIIS text **and the complaint** *before any LLM sees them*, `siis_hash`; slots come from `data/slot_lexicon.json`, never from an LLM. The scrub (`compiler/scrub.py`) canonicalises first (HTML entities, NFKC, invisible characters) and every pattern is length-bounded, so it stays linear on hostile input; keep it that way (no unbounded `+`/`*` before a required character).
- **cache** — Tier 0 exact (`norm_query + siis_hash`), Tier 1 semantic (brute-force cosine over each solved plan's original query *and* its 8–10 variations). A hit requires similarity ≥ τ (0.70) **and** compatible slots (component, intent, one-sided symptom) **and** a matching SIIS hash. The SIIS cache ships empty.
- **no article** — missing, `null`, `""`, `{}`, whitespace or title-only content is one case ([cache/no_siis.py](api/app/cache/no_siis.py)): the same question answered before → a cached plan from the kit table (results.jsonl, loaded at startup) or any solved plan, similarity ≥ 0.80 + slot guard (`source: cached_plan`) → the full pipeline over a remembered article (every article the API receives, the kit's pre-loaded), similarity ≥ 0.82 and 0.04 ahead of the runner-up, scores scaled by that similarity (`source: retrieved_article`) → otherwise empty. All three carry `fallback: no_siis_context`. The LLM never fills the gap (FAQ Q14, G5). `no_match` means an article was given and there is no grounded answer: nothing survived grounding, every model that answered chose nothing from the article (at least `extract_empty_votes` of them), rules-only extraction met an article whose best section scores under `rules_min_relevance`, or the complaint holds no readable word (`pipeline/text.recognisable`).
- **enrich** — canonical query, 1–3 intents, domain, 2–3 word title, 12 candidate variations filtered down to 8–10 (drop token Jaccard ≥ 0.6, drop embedding cosine < 0.6).
- **segment / extract** — SIIS split into sections and numbered sentences `S1…Sn`; the LLM returns actions whose every step cites sentence ids.
- **ground** — a step survives only if it clears the embedding threshold against its cited sentences **and** shares a content term with them. Failing steps are dropped; actions left empty are dropped.
- **resolve** — `screen_path + verb` → BM25 + dense over catalog entries → RRF (cross-encoder rerank off, `use_rerank`) → Screen Graph screen → entry chosen by polarity. Tiered outcome: catalog link, `bixby://dummy_positive`, or manual.
- **compile / validate** — builds the official `ContextDeeplinkResponse`, then URL scrub → schema validation → one repair cycle → drop the offending action.

The endpoints are listed in [README.md](README.md); the full design lives in `docs/Smart Guided Troubleshooting Engine - Architecture & System Design.pdf` and should be exported into [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Hard rules

1. **Never edit [api/app/schema.py](api/app/schema.py).** It is the organisers' official file; ruff is configured to skip it. Every response must validate against it.
2. **Never invent a step.** Every step traces back to SIIS sentence ids. If nothing survives grounding, return empty `contexts` with `fallback: "no_match"` — an empty answer beats a fabricated one.
3. **Zero URLs in output.** No `http(s)`, `www.`, bare domains, emails or markdown links in any string. The catalog `bixby://` URIs in deeplink fields are the only exception. Run the scrub on cache hits too.
4. **Always 200 from `/v1/troubleshoot`,** with a schema-valid body, whatever fails internally. Degrade, never error.
5. **Thresholds and budgets live in [api/app/config.py](api/app/config.py).** Never hard-code a number in a module; add new settings under your own section header.
6. **[api/app/models.py](api/app/models.py) is the frozen shared contract.** Change it only by team agreement, merged by Vishaal.
7. **Never commit `.env` or API keys.** `data/kit/` contains a real-looking address (`kidshome.pin@samsung.com`) — that is exactly why the SIIS scrub runs before the LLM call.
8. **Copy catalog values verbatim.** The deeplink URIs in `data/kit/deeplinks.json` are masked placeholders; match on `description` / `message` / `qna_description` / `originalType`, then copy the URI and the entry's *own* validation object unchanged.
9. **Gates stay green.** After M1, no PR merges if it breaks G2–G5 on the gate replica.

## Output string rules (compiler)

These are graded, so build them by rule rather than letting the LLM free-write them:

| Field | Rule |
| --- | --- |
| `goal` | Strip a trailing "Troubleshooting"/"Configuration" from the topic, then `Follow these steps to perform this {Topic} Troubleshooting.` |
| `title` | LLM-proposed, 2–3 words, sentence case |
| `actionName` | Title Case, deduplicated; same-screen actions merged into one |
| `description` | `It will` + 5–7 words **total, counting "It will"** |
| `steps` | Imperative, trailing period, no numbering |
| `score` | `0.4 * section relevance + 0.3 * grounding coverage + 0.3 * link coverage`, clamped 0–1 (catalog link = 1.0, dummy = 0.5, manual excluded) |
| `category` | `critical` for restart / safe mode / software update / factory reset; `manual` for physical or external steps; `auto` only when a link resolved |

[data/kit/sample_output.json](data/kit/sample_output.json) is the reference shape.

## Conventions

- Python 3.11 in Docker, ruff targets py310 — do not rely on 3.11-only syntax. Line length 110.
- Absolute imports from the `app` package (`from app.models import DraftAction`), never relative.
- Typed signatures and pydantic models throughout: internal models in `models.py`, official output in `schema.py`. Only compiler output crosses into `schema.py` types.
- One module per design component, with the component number in the docstring. Keep stub signatures stable — other lanes code against them.
- LLM calls go through [api/app/llm/router.py](api/app/llm/router.py), never a client directly. The team runs on **free tiers** (no billing): measured on 2026-09-24, every Gemini model answers 503 on the free tier under load and Mistral's free plan returns 429 for Small/Medium, but serves the open-weight Ministral 3B/8B/14B (~80–100 tokens/s, concurrent calls allowed). So call B (extract) runs in **select mode** (`extract.v3.md`): the model lists the article sentence ids of each action instead of rewriting steps, and the steps are the article's own sentences, split into single instructions. The answer is compact one-line JSON with short keys (`ids`, `desc`, `path`, `verb`; `extract._long_keys` renames them), `ids` is an enum of the article's own sentence ids (so no invented id can be decoded), at most `extract_max_actions` (8) actions per goal, and `path` must reach the setting itself, not its parent menu. An action whose name shares no word with its own steps and screen (the model labelled it after another sentence) is renamed from its screen. `ministral-14b-latest` is raced against `ministral-8b-latest`; 14B wins if it is back within 5 s (`extract_prefer_deadline_s`), and the stage gives up at 6.3 s so a cold answer stays under 8 s. Intents come from call B too; the 8–10 variations come from a background call (`variations.v1.md`) that never delays an answer. `gemini-3-flash-preview` is the last fallback; a model answering 429/5xx is skipped for 60 s. All of it is in the engine section of `config.py`; with billing, `extract_mode = "rewrite"` and Gemini models switch back to the original design. Gemini runs at temperature 1.0 (Google's Gemini 3 guidance), Mistral at 0; repeatability comes from the cache and the compiler. With no key set, or every provider down, the pipeline runs rules-only (the article's own instruction sentences, score capped at 0.5). Every valid answer is cached, a degraded one too, so repeats are fast and identical; a degraded one only for `degraded_cache_ttl_s` (10 min), after which the question gets a cold run again. Variations that change the problem's slots (a different component, symptom or intent) are dropped unless fewer than 8 would be left. Prompts are versioned files in `api/app/llm/prompts/` (versions in `settings.prompt_versions`); add a new version rather than mutating a shipped one, and change `settings.prompt_version` (the cache-key tag) with it.

## Ownership and workflow

[docs/TEAM.md](docs/TEAM.md) is the authoritative split — read it before touching an unfamiliar directory.

| Lane | Owner | Directories |
| --- | --- | --- |
| Engine & integration | Vishaal (lead) | `api/app/pipeline/` (except `slots.py`), `compiler/`, `llm/`, `main.py`, `routes/troubleshoot.py`, `routes/stream.py`, `models.py`, `scripts/make_results.py` |
| Mapping, cache & infra | Karur | `api/app/screengraph/`, `retrieval/`, `cache/`, `device/`, `obs/`, `pipeline/slots.py`, `routes/device.py`, `routes/metrics.py`, the other `api/scripts/`, Docker & hosting, `data/slot_lexicon.json`, `data/appliance_exclusions.json` |
| Console & evaluation | Nikhil | `console/`, `eval/`, `docs/metrics.md`, `docs/deck/`, `docs/VIDEO.md` |

Stay in your lane; needed changes elsewhere go through a GitHub issue tagging the owner. Branch → PR → green CI → merge, with branches named `feat/<area>-<thing>` or `fix/<area>-<thing>`. No direct pushes to `main`.

## Gotchas

- `data/build/` is gitignored, so the Screen Graph and indexes are never committed — build them locally (and bake them into the image) before expecting link resolution to work.
- The console service (`console/Dockerfile`) is prerendered from repo files outside `console/` — fixtures, prompts, `config.py`, the catalog, gold labels, `eval/sets/`, `docs/metrics.md` — so `.dockerignore` excludes `docs/` and `eval/` *except* `docs/metrics.md` and `eval/sets/`. Keep those exceptions. Its `NEXT_PUBLIC_API_URL` is baked in at build and called by the viewer's browser (`ONECLICK_PUBLIC_API_URL` in compose).
- The docker build context is the repo root. The image copies `data/kit/`, `slot_lexicon.json`, `dependencies.json`, `appliance_exclusions.json` and `results.jsonl` into `/data` and builds the Screen Graph and vectors itself; `data/fixtures/` is **not** in it, so the mock stream only works from a checkout.
- `/health` returns 503 until the catalog, Screen Graph, vector index and cache snapshot are loaded (`app/obs/readiness.py`). Startup also pre-warms the no-article path (~2 s): the kit table from `results.jsonl` and the 11 kit articles. The Docker image must contain `results.jsonl` (`/data/results.jsonl`, or set `ONECLICK_RESULTS`); without it the kit table is empty and only article memory answers.
- `cache.sqlite` (default `sqlite_path`) is written into whatever directory the API is started from, and the API reloads it at boot. A leftover file makes the next gate-replica run start warm, so delete it (or set `ONECLICK_SQLITE`) before measuring cold numbers. Tests (`api/tests/conftest.py`) use a throwaway file and blank LLM keys, so they never warm the dev cache or spend quota. `scripts/make_results.py` always uses a throwaway file (it clears the cache before every row) but needs the real keys: it is the submission run.
- `results.jsonl` at the repo root is generated by `scripts/make_results.py`; it is the submission artefact, not a scratch file.
- `POST /v1/troubleshoot/stream` runs the real pipeline (`pipeline.run.run_stream`); `/v1/troubleshoot` drains the same generator, so the two can never disagree. Setting `settings.stream_mock = True` replays [data/fixtures/](data/fixtures/README.md) instead, with frames marked `X-Mock: true` / `detail.mock`; that only works when the API runs from the repo.
- `data/fixtures/` is a contract for Karur and Nikhil, guarded by [api/tests/test_fixtures.py](api/tests/test_fixtures.py): official schema, zero URLs, verbatim catalog links, every step traced to a real article sentence, the score formula. If you change a fixture, keep those tests green and tell the other lanes.
- Catalog quirk the fixtures and the simulator must respect: all 138 `onURL` (enable) entries carry a full validation object (key, condition, value) and every `offURL`, `onClickURL` and `updateURL` entry is key-only. So the design's "138 fully validatable entries" are exactly the enable toggles, and only those can show "Verified". `DL-0022 View Reset Options` is the *auto* factory reset, not Factory data reset.
- A catalog entry's `message` can contradict its `description`: `DL-0397`/`DL-0398` read "Adaptive Display" but are adaptive **battery**. Match on `description`. Some entries are exact duplicates (`DL-0518`, `DL-0552`). Several common screens have no entry at all — software update, Safe mode, Dark mode, auto-rotate, per-app storage, Factory data reset — so those steps resolve to `bixby://dummy_positive` or stay manual, and that is the correct answer, not a bug.
- **`.gitignore` entries must be anchored.** It started as the stock Python template, whose
  unanchored `lib/` silently swallowed `console/lib/` — the console would have been committed
  without its design tokens and failed to build for everyone else. The distribution/packaging
  entries are now anchored (`/lib/`, `/build/`, `/dist/`…). Do not re-add an unanchored directory
  name; it matches at every level, not just the repo root.
- The story page replays a **real** engine run from `console/recordings/`, written by
  `python eval/tools/record_story.py` (shipping pipeline, real keys, throwaway cache). Re-record after
  any engine change the page shows (prompts, extraction, resolver, compiler) and rebuild the site.
  `data/fixtures/` stays the engine tests' illustrative contract; the page no longer renders it.
- `next dev` generates `console/AGENTS.md` and `console/CLAUDE.md` and keeps regenerating them.
  Both are gitignored — the second would otherwise collide with this file.
- `data/gold/deeplink_gold.jsonl` is the answer key for deeplink precision@1, split ~33 each; 123 are labelled so far (Karur 87, Nikhil 36). Label yours with `eval/tools/label_gold.py`, and read the chosen entry's own description before accepting it — the tool's BM25 shortlist gets 1 in 3 wrong.

## Open questions with the organisers

Answers may change compiler behaviour, so check before "fixing" these: does the goal string end with a period (currently following the FAQ regex); is an extra `meta` key allowed in the body (behind `settings.include_meta`); do service-centre steps come before or after critical actions (critical last for now).
