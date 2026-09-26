"""Record the runs the story page at `/` walks through, from the real engine.

The page used to replay data/fixtures/, which the fixture README marks as illustrative (timings,
token counts, grounding scores and the dropped step were made up before the engine existed). This
records the same requests through the shipping pipeline instead, so every number on the page is one
the engine produced:

    touch_lag           the cold run (every stage frame, the background variations call with the
                        rewordings it dropped), then the same words again (exact hit), then a held-out
                        paraphrase from eval/sets/paraphrases.jsonl (semantic hit)
    touch_multi_intent  a two-problem complaint on the same article (a lag fault and a navigation
                        change), cold. The fixture's own two-problem query ("touch is laggy and my
                        swipes are read as the wrong gesture") is one touchscreen problem to the model,
                        so it gets one goal; this one names two.

It runs in-process (the code path POST /v1/troubleshoot/stream drains) on a throwaway cache, with the
LLM keys from .env. The page describes the normal path: the primary extract model answering (and, for
the multi-intent run, two goals). A cold run that took another path (the fast model after the prefer
deadline, or rules-only when the free tier is busy) is retried after a pause, up to --tries, keeping the
best one seen; the number of tries is written next to the run.

    python eval/tools/record_story.py                  # -> console/recordings/
    python eval/tools/record_story.py --pause 20 --tries 4

The fixtures stay what they are: the contract the engine's own tests check. Rebuild the site after
recording (it reads these files at build time).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

from evalkit import engine  # points the cache at a throwaway file before any engine module loads
from evalkit.paths import REPO_ROOT

load_dotenv(REPO_ROOT / ".env")

FIXTURES = REPO_ROOT / "data" / "fixtures"
OUT = REPO_ROOT / "console" / "recordings"
PARAPHRASES = REPO_ROOT / "eval" / "sets" / "paraphrases.jsonl"
TOUCH_LAG_ROW = "row_21"
MULTI_QUERY = (
    "My Galaxy S22 touchscreen lags behind my taps, and I also want to switch the navigation back to "
    "buttons instead of swipe gestures."
)


def _commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return out.stdout.strip() or None


def _events(query: str, siis: object, sink: dict | None = None) -> list[dict]:
    from app.pipeline.run import run_stream

    frames = run_stream(query, siis, wait_for_variations=sink is not None, sink=sink)
    return [e.model_dump(mode="json") for e in frames]


def _stage(events: list[dict], name: str) -> dict:
    return next((e for e in events if e["stage"] == name), {})


def cold(query: str, siis: object, tries: int, pause: float, goals: int = 1) -> tuple[list[dict], dict, int]:
    """A cold run on the normal path (the primary model answered, with at least `goals` goals), else the
    best one seen in `tries`, and how many tries were made."""
    from app.cache import store
    from app.config import settings

    best: tuple[int, list[dict], dict] | None = None
    for attempt in range(1, tries + 1):
        store.clear()
        sink: dict = {}
        events = _events(query, siis, sink)
        extract = _stage(events, "extract").get("detail", {})
        found = len(events[-1]["detail"]["contexts"])
        print(
            f"  cold try {attempt}: {extract.get('source')} {extract.get('model')}, {found} goal(s), {events[-1]['ms']} ms"
        )
        rank = (
            (extract.get("source") == "llm")
            + (extract.get("model") == settings.extract_model)
            + (found >= goals)
        )
        if best is None or rank > best[0]:
            best = (rank, events, sink)
        if rank == 3:
            return events, sink, attempt
        time.sleep(pause)
    if best is None or _stage(best[1], "extract").get("detail", {}).get("source") != "llm":
        raise SystemExit(f"no model answered in {tries} tries: the free tier is busy, try again later")
    return best[1], best[2], tries


def semantic_hit(row_id: str, siis: object) -> tuple[list[dict], str]:
    """The first held-out paraphrase of the row that the cache answers by meaning."""
    for line in PARAPHRASES.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if item.get("row_id") != row_id:
            continue
        events = _events(item["query"], siis)
        if _stage(events, "cache").get("detail", {}).get("tier") == "semantic":
            return events, item["id"]
    raise SystemExit(f"no paraphrase of {row_id} hit the semantic cache")


def _write(folder: Path, name: str, value: object) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--tries", type=int, default=4)
    parser.add_argument("--pause", type=float, default=15.0, help="seconds between tries")
    args = parser.parse_args()
    engine.warm()
    from app.cache import store
    from app.config import settings
    from app.pipeline.normalize import normalize_query

    about = {
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "commit": _commit(),
        "prompt_version": settings.prompt_version,
        "extract_model": settings.extract_model,
        "extract_fast_model": settings.extract_fast_model,
        "variations_model": settings.variations_model,
        "how": "eval/tools/record_story.py: the shipping pipeline in-process, throwaway cache, real keys",
    }

    request = json.loads((FIXTURES / "touch_lag" / "request.json").read_text(encoding="utf-8"))
    print("touch_lag")
    events, sink, tries = cold(request["query"], request["siis_response"], args.tries, args.pause)
    exact = _events(request["query"], request["siis_response"])
    semantic, paraphrase_id = semantic_hit(TOUCH_LAG_ROW, request["siis_response"])
    folder = args.out / "touch_lag"
    _write(folder, "request.json", request)
    _write(folder, "stream.json", events)
    _write(folder, "plan.json", events[-1]["detail"])
    _write(
        folder,
        "cache_events.json",
        {
            "exact": {"event": _stage(exact, "cache"), "meta": exact[-1]["detail"]["meta"]},
            "semantic": {
                "event": _stage(semantic, "cache"),
                "meta": semantic[-1]["detail"]["meta"],
                "paraphrase_id": paraphrase_id,
            },
        },
    )
    _write(
        folder,
        "variations.json",
        {
            "kept": sink.get("variations", []),
            "dropped": sink.get("variations_dropped", []),
            **{k: v for k, v in (sink.get("variations_info") or {}).items() if k != "attempts"},
        },
    )
    _write(folder, "about.json", {**about, "cold_tries": tries, "query": normalize_query(request["query"])})

    request = {"query": MULTI_QUERY, "siis_response": request["siis_response"]}
    print("touch_multi_intent")
    events, _, tries = cold(request["query"], request["siis_response"], args.tries, args.pause, goals=2)
    folder = args.out / "touch_multi_intent"
    _write(folder, "request.json", request)
    _write(folder, "stream.json", events)
    _write(folder, "plan.json", events[-1]["detail"])
    _write(folder, "about.json", {**about, "cold_tries": tries})
    store.clear()
    print(f"written to {args.out}")


if __name__ == "__main__":
    main()
