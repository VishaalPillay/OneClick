# Build context is the repo root (see docker-compose.yml): the API needs data/kit at runtime.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FASTEMBED_CACHE_PATH=/opt/models \
    ONECLICK_SQLITE=/data/cache.sqlite

WORKDIR /app

COPY api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Bake the embedding model into the image: a cold container must not download 130 MB on its
# first request, and the judging host may have no outbound access to the model host.
RUN python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='BAAI/bge-small-en-v1.5')"

COPY api/ /app/
# The engine reads these at runtime: the catalog, the slot lexicon (cache guard) and the
# dependency edges (action ordering). Without them the service starts but degrades silently.
COPY data/kit/ /data/kit/
COPY data/slot_lexicon.json data/dependencies.json data/appliance_exclusions.json /data/

# Screen Graph + catalog vectors, so /health goes green in well under a second (ADR-007).
RUN python scripts/build_screengraph.py && python scripts/build_index.py

# The kit plans the no-article path (cache/no_siis.py) loads at startup: without them a request
# with no article gets an empty answer. Copied after the build so a new results.jsonl does not
# rebuild the index.
COPY results.jsonl /data/results.jsonl

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)"

# One worker on purpose: each worker would hold its own in-memory cache and vector index, so a
# repeat query could land on the other one and miss. The request path waits on LLM calls, which
# release the GIL, so a second worker buys little.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
