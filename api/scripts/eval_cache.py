"""Measure the cache the way block A3 scores it.

Run from api/:
    python scripts/eval_cache.py              # real run: results.jsonl + the team eval sets
    python scripts/eval_cache.py --sweep      # the same, across similarity thresholds
    python scripts/eval_cache.py --authored   # the older hand-written set, no LLM variations

Real mode warms the cache exactly as a served request would - the kit query plus the 8-10 LLM
variations recorded in results.jsonl, keyed on the real article hash - then replays
eval/sets/paraphrases.jsonl (expect a hit) and eval/sets/near_miss.jsonl (expect a miss: same
component, different symptom or intent). The 20 kit rows share 11 articles, so a hit can serve a
sibling row's plan, as it can live: that is the wrong-plan count.
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import cache
from app.config import settings
from app.models import CacheEntry
from app.pipeline.normalize import clean_siis, display_query, normalize_query
from app.pipeline.slots import extract_slots

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "results.jsonl"
KIT = ROOT / "data" / "kit" / "siis_responses.json"
PARAPHRASES = ROOT / "eval" / "sets" / "paraphrases.jsonl"
NEAR_MISS = ROOT / "eval" / "sets" / "near_miss.jsonl"
AUTHORED = ROOT / "data" / "gold" / "cache_paraphrases.jsonl"


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def kit_rows() -> dict[str, dict]:
    return {row["id"]: row for row in json.loads(KIT.read_text(encoding="utf-8"))["responses"]}


def warm_from_results(kit: dict[str, dict]) -> tuple[dict[str, str], dict[str, str]]:
    """Load every solved plan from results.jsonl. Returns row_id -> cache key, row_id -> article hash.

    Key, slots and stored text come from the engine's own normalize_query, display_query and
    clean_siis. A rougher stand-in lost kit row 1, whose "1. " numbering results.jsonl no longer has.
    """
    by_query = {normalize_query(row["original_query"]): row_id for row_id, row in kit.items()}

    cache.clear()
    keys: dict[str, str] = {}
    hashes: dict[str, str] = {}
    for row in read_jsonl(RESULTS):
        norm_query = normalize_query(row["query"])
        row_id = by_query.get(norm_query)
        if row_id is None:  # results.jsonl query text drifted from the kit
            continue
        _, siis_hash = clean_siis(kit[row_id]["siis_response"])
        key = cache.make_key(norm_query, siis_hash)
        cache.put(
            CacheEntry(
                key=key,
                siis_hash=siis_hash,
                slots=extract_slots(norm_query),
                plan=row["response"],
                query_texts=[display_query(row["query"]), *row.get("query_variations", [])],
                created_at=time.time(),
            )
        )
        keys[row_id], hashes[row_id] = key, siis_hash
    return keys, hashes


def measure_real() -> dict:
    kit = kit_rows()
    keys, hashes = warm_from_results(kit)
    paraphrases = [p for p in read_jsonl(PARAPHRASES) if p["row_id"] in keys]
    near_misses = [n for n in read_jsonl(NEAR_MISS) if n["row_id"] in keys]

    def lookup(text: str, row_id: str):
        norm_query = normalize_query(text)
        return cache.lookup(norm_query, extract_slots(norm_query), hashes[row_id])

    hits = wrong = 0
    elapsed = 0.0
    misses_by_row: dict[str, int] = {}
    for case in paraphrases:
        start = time.perf_counter()
        hit = lookup(case["query"], case["row_id"])
        elapsed += (time.perf_counter() - start) * 1000
        if hit is None:
            misses_by_row[case["row_id"]] = misses_by_row.get(case["row_id"], 0) + 1
            continue
        hits += 1
        wrong += hit.key != keys[case["row_id"]]

    false_hits = sum(lookup(case["query"], case["row_id"]) is not None for case in near_misses)

    repeat_hits, repeat_ms = 0, 0.0
    for row_id in keys:  # the kit query verbatim, numbering and all, as the scorer sends it
        start = time.perf_counter()
        hit = lookup(kit[row_id]["original_query"], row_id)
        repeat_ms += (time.perf_counter() - start) * 1000
        repeat_hits += hit is not None and hit.tier == "exact"

    return {
        "cached": len(keys),
        "repeat_rate": repeat_hits / max(len(keys), 1),
        "repeat_ms": repeat_ms / max(len(keys), 1),
        "para_total": len(paraphrases),
        "para_rate": hits / max(len(paraphrases), 1),
        "para_ms": elapsed / max(len(paraphrases), 1),
        "para_wrong": wrong,
        "near_total": len(near_misses),
        "false_rate": false_hits / max(len(near_misses), 1),
        "worst_rows": sorted(misses_by_row.items(), key=lambda kv: -kv[1])[:3],
    }


def measure_authored() -> dict:
    """The older hand-written set: 6 queries, 3 variations cached, 5 held out each."""
    cases = read_jsonl(AUTHORED)
    cache.clear()
    plans = {}
    for case in cases:
        plans[case["id"]] = {"contexts": [{"goal": case["id"]}]}
        cache.put(
            CacheEntry(
                key=cache.make_key(case["query"], "article-hash"),
                siis_hash="article-hash",
                slots=extract_slots(case["query"]),
                plan=plans[case["id"]],
                query_texts=[case["query"], *case["paraphrases"][:3]],
                created_at=time.time(),
            )
        )
    total = hits = wrong = 0
    for case in cases:
        for text in case["paraphrases"][3:]:
            total += 1
            hit = cache.lookup(text, extract_slots(text), "article-hash")
            if hit is None:
                continue
            hits += 1
            wrong += hit.plan != plans[case["id"]]
    return {
        "cached": len(cases),
        "repeat_rate": 1.0,
        "repeat_ms": 0.0,
        "para_total": total,
        "para_rate": hits / total,
        "para_ms": 0.0,
        "para_wrong": wrong,
        "near_total": 0,
        "false_rate": 0.0,
        "worst_rows": [],
    }


def report(label: str, r: dict) -> None:
    print(
        f"{label:>10}  repeat {r['repeat_rate']:4.0%} ({r['repeat_ms']:.2f} ms)"
        f"   paraphrase {r['para_rate']:4.0%} of {r['para_total']:3} ({r['para_ms']:.0f} ms)"
        f"   wrong-plan {r['para_wrong']:2}"
        f"   false hits {r['false_rate']:4.0%} of {r['near_total']:2}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sweep", action="store_true", help="try several similarity thresholds")
    parser.add_argument("--authored", action="store_true", help="use the hand-written set instead")
    parser.add_argument("--verbose", action="store_true", help="name the rows that miss most")
    args = parser.parse_args()

    measure = measure_authored if args.authored else measure_real
    if not args.authored and not RESULTS.exists():
        sys.exit("results.jsonl not found: run scripts/make_results.py first, or pass --authored")

    print("targets: repeat >= 90%, paraphrase >= 80%, false hits <= 2%\n")
    thresholds = [0.70, 0.72, 0.75, 0.78, 0.80, 0.85] if args.sweep else [settings.cache_sim_threshold]
    for threshold in thresholds:
        settings.cache_sim_threshold = threshold
        result = measure()
        report(f"sim>={threshold:.2f}", result)
        if args.verbose and result["worst_rows"]:
            print(f"             rows missing most: {result['worst_rows']}")
    cache.clear()


if __name__ == "__main__":
    main()
