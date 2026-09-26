"""OneClick API entrypoint."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware

from app.cache import no_siis
from app.obs import readiness
from app.pipeline import segment
from app.routes import device, metrics, stream, troubleshoot


@asynccontextmanager
async def lifespan(app: FastAPI):
    readiness.warm()  # catalog, Screen Graph, vector index, cache snapshot
    try:
        no_siis.prewarm()  # kit plans + kit articles for requests without an article (~2 s)
    except Exception:  # noqa: BLE001 - loads lazily on the first no-article request instead
        logging.getLogger("oneclick").warning("no-article pre-warm failed; it will load on first use")
    try:
        segment.prewarm_kit()  # kit article sections embedded: the first cold request is not slower
    except Exception:  # noqa: BLE001 - the first requests are just slower
        logging.getLogger("oneclick").warning("section pre-warm failed")
    yield


app = FastAPI(title="OneClick", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/health")
def health(response: Response) -> dict:
    """{"status": "ok"} only once the request path can actually serve; 503 until then (ADR-007)."""
    if not readiness.ensure():
        response.status_code = 503
        return {"status": "starting", **readiness.state()}
    return {"status": "ok"}


app.include_router(troubleshoot.router)
app.include_router(stream.router)
app.include_router(device.router)
app.include_router(metrics.router)
