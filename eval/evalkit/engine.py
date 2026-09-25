"""In-process adapter to the engine's mapping and cache code, for ablation.py and loadtest.py.

This is the one evalkit module that imports engine code, and only to *measure* it. The gate
checks (checks.py, catalog.py) stay independent of the engine so they can catch its bugs.

Importing points ONECLICK_SQLITE at a throwaway file first, always, even when it is exported: warm()
calls cache.clear(), so a measurement run must never touch a real cache. Requires the offline builds (run from api/):
python scripts/build_screengraph.py && python scripts/build_index.py
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from evalkit.paths import DATA_DIR, use_api_package

_SCRATCH = Path(tempfile.mkdtemp(prefix="oneclick-eval-"))
os.environ["ONECLICK_SQLITE"] = str(_SCRATCH / "cache.sqlite")
os.environ.setdefault("ONECLICK_DATA", str(DATA_DIR))
use_api_package()

from app import cache, retrieval
from app.config import settings
from app.models import CacheEntry, DraftAction, DraftStep
from app.obs import readiness
from app.pipeline.slots import extract_slots
from app.screengraph.resolver import resolve

GRAPH_PATH = DATA_DIR / "build" / "screengraph.json"
VECTORS_PATH = DATA_DIR / "build" / "catalog_vectors.npz"

__all__ = [
    "CacheEntry",
    "cache",
    "extract_slots",
    "hybrid_top1",
    "resolve_step",
    "settings",
    "warm",
]


def warm() -> dict:
    """Load catalog, Screen Graph, vector index and an empty cache, exactly as the API boots."""
    missing = [p.name for p in (GRAPH_PATH, VECTORS_PATH) if not p.exists()]
    if missing:
        raise SystemExit(
            f"missing {', '.join(missing)} in data/build/: run api/scripts/build_screengraph.py "
            "and build_index.py first"
        )
    state = readiness.warm()
    if not state["ready"]:
        raise SystemExit(f"engine failed to warm: {state['error']}")
    cache.clear()
    return state


def resolve_step(step: str, screen: str, verb: str | None) -> tuple[str, str | None]:
    """The shipping resolver: (tier, entry_id). Tier is catalog, dummy or manual."""
    action = DraftAction(steps=[DraftStep(text=step)], screen_path=screen, intent_verb=verb)
    decision = resolve(action)
    tier = decision.tier.value
    return tier, decision.entry_id if tier == "catalog" else None


def hybrid_top1(screen: str, verb: str | None) -> str | None:
    """Raw BM25 + dense fusion over catalog entries: no Screen Graph, leaf boost, polarity or tiers."""
    results = retrieval.hybrid_search(f"{screen} {verb or ''}".strip(), k=1)
    return results[0][0] if results else None
