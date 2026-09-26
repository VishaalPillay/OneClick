# OneClick

Vague complaint in, one-tap fix out. Grounded troubleshooting plans with verified Galaxy Settings deeplinks.
Samsung PRISM GenAI Hackathon 2026, Theme 2: Smart Guided Troubleshooting.

## Quick start

```bash
cp .env.example .env      # add your API keys
docker compose up --build
curl localhost:8000/health
```

That starts two services: the engine API on `http://127.0.0.1:8000` and the site on
`http://localhost:3000`, whose **Try it live** section streams the API from your browser. If port 3000
is taken, `ONECLICK_CONSOLE_PORT=3100 docker compose up --build`. The site is prerendered, so the API
address is baked in at build time: set `ONECLICK_PUBLIC_API_URL` to serve it against an API elsewhere.

## Local development (API)

```bash
cd api
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
pytest
```

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | /health | Readiness check |
| POST | /v1/troubleshoot | Complaint + SIIS in, troubleshooting plan out (no SIIS: a remembered plan or article, else empty) |
| POST | /v1/troubleshoot/stream | Same engine, live stage events (console) |
| GET | /v1/metrics, /v1/traces, /v1/trace/{id} | Observability |

The simulated device (`/v1/device/apply`, `/v1/device/validate`) is designed but not in this submission;
see the last section of [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Repository layout

| Path | What lives there |
| --- | --- |
| `api/app/pipeline/` | Normalize, slots, enrich, segment, extract, ground, categorize, order, multi-intent |
| `api/app/screengraph/` | Catalog cleaning, Screen Graph nodes, deeplink resolver |
| `api/app/retrieval/` | BM25, dense embeddings, fusion, rerank |
| `api/app/compiler/` | Goal templates, trimmer, URL scrub, final validation |
| `api/app/cache/` | Exact + semantic tiers, slot guard, no-SIIS lookup, SQLite store |
| `api/app/llm/` | Gemini + Mistral router and versioned prompts |
| `api/app/device/` | Device simulator |
| `api/app/obs/` | Logs, metrics, traces |
| `api/scripts/` | Offline builds and `results.jsonl` generation |
| `console/` | Next.js site: the scroll story and the live try-it section |
| `eval/` | Gate replica, judge, ablation, load tests, test sets |
| `data/` | Kit files, gold labels, slot lexicon, dependency table |
| `docs/` | Architecture, metrics.md, deck, video link |

Read [CLAUDE.md](CLAUDE.md) (commands, hard rules, gotchas) and [docs/TEAM.md](docs/TEAM.md) (who owns what) before opening a PR.
