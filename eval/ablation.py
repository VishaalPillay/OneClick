"""Deeplink mapping ablation on the gold set (metrics.md section 5).

Every variant maps the same gold steps (data/gold/deeplink_gold.jsonl) and is scored the same way:
deeplink relevance 0-2 (evalkit/relevance.py), precision@1 on steps with a real catalog answer,
whether it picked the right tier (catalog / dummy / manual), p50/p95 per step and cost per step.

    Baseline   llm          full LLM mapping, whole catalog in the prompt (needs MISTRAL_API_KEY)
    Variant A  hybrid       BM25 + dense fusion over raw catalog entries, top hit, always links
    Variant B  rules        pure rules: physical keywords, exact screen-name match, verb -> entry type
    extra      bm25         keyword retrieval alone
    ours       screengraph  the shipping resolver: hybrid search, Screen Graph polarity, tiers

Scores are split by who labelled the gold. The mapping lane tuned its thresholds on this file, so
the eval lane's labels are the less optimistic half, though not strictly held out: they were in
the file when that tuning ran.

Usage (from the repo root, after api/scripts/build_screengraph.py and build_index.py):
    python eval/ablation.py                     # every variant that can run here
    python eval/ablation.py --only rules,bm25   # no engine or key needed
    python eval/ablation.py --misses            # print every step a variant got wrong
Writes eval/results/ablation.json.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))

from evalkit.mappers import Bm25Mapper, LlmMapper, RulesMapper
from evalkit.paths import GOLD_PATH, REPO_ROOT, RESULTS_DIR
from evalkit.relevance import (
    CATALOG,
    MANUAL,
    TIERS,
    GoldCase,
    Prediction,
    Screens,
    exact,
    load_gold,
    relevance,
)
from evalkit.stats import percentile

VARIANTS = {
    "llm": ("Baseline: Full LLM Deeplink Mapping", "baseline"),
    "hybrid": ("Variant A: Hybrid BM25 + Dense Embedding Retrieval", "variant_a"),
    "rules": ("Variant B: Pure Rules-Based Deeplink Mapping", "variant_b"),
    "bm25": ("Extra: BM25 keyword retrieval only", "extra"),
    "screengraph": ("Ours: Screen Graph resolver (hybrid + polarity + tiers)", "ours"),
}
EVAL_OWNER = "nikhil"
OUT_PATH = RESULTS_DIR / "ablation.json"


def commit_sha() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return out.stdout.strip() or None


def score_group(cases: list[GoldCase], preds: list[Prediction], screens: Screens) -> dict:
    """Aggregate metrics over one slice of the gold set."""
    n = len(cases)
    if not n:
        return {"n": 0}
    rel = [relevance(c, p, screens) for c, p in zip(cases, preds, strict=True)]
    catalog = [(c, p) for c, p in zip(cases, preds, strict=True) if c.tier == CATALOG]
    # A link that opens the wrong screen, or any link on a physical step: worse than no link.
    wrong_links = sum(1 for p, r in zip(preds, rel, strict=True) if p.tier != MANUAL and r == 0)
    return {
        "n": n,
        "relevance_mean": round(sum(rel) / n, 3),
        "relevance_hist": {str(k): v for k, v in sorted(Counter(rel).items())},
        "p_at_1": round(sum(exact(c, p) for c, p in catalog) / len(catalog), 3) if catalog else None,
        "p_at_1_n": len(catalog),
        "tier_accuracy": round(sum(c.tier == p.tier for c, p in zip(cases, preds, strict=True)) / n, 3),
        "wrong_link_rate": round(wrong_links / n, 3),
        "predicted_tiers": {t: sum(p.tier == t for p in preds) for t in TIERS},
    }


def run_variant(key: str, mapper, cases: list[GoldCase], screens: Screens) -> dict:
    preds, latencies = [], []
    for case in cases:
        start = time.perf_counter()
        preds.append(mapper(case.step, case.screen, case.verb))
        latencies.append((time.perf_counter() - start) * 1000)
    owners = sorted({c.owner for c in cases})
    by_owner = {}
    for owner in owners:
        idx = [i for i, c in enumerate(cases) if c.owner == owner]
        by_owner[owner] = score_group([cases[i] for i in idx], [preds[i] for i in idx], screens)
    misses = [
        {
            "step": c.step,
            "screen": c.screen,
            "verb": c.verb,
            "gold_tier": c.tier,
            "expected": c.expected_id,
            "got_tier": p.tier,
            "got": p.entry_id,
            "relevance": r,
            "owner": c.owner,
        }
        for c, p in zip(cases, preds, strict=True)
        if (r := relevance(c, p, screens)) < 2
    ]
    return {
        "key": key,
        "name": VARIANTS[key][0],
        "row": VARIANTS[key][1],
        "measured": True,
        "all": score_group(cases, preds, screens),
        "by_owner": by_owner,
        "latency_ms": {
            "p50": round(percentile(latencies, 50) or 0.0, 2),
            "p95": round(percentile(latencies, 95) or 0.0, 2),
        },
        "cost_per_step_usd": 0.0,
        "misses": misses,
    }


def not_measured(key: str, reason: str) -> dict:
    return {
        "key": key,
        "name": VARIANTS[key][0],
        "row": VARIANTS[key][1],
        "measured": False,
        "reason": reason,
    }


def build_mappers(keys: list[str], args) -> tuple[dict, dict]:
    """(key -> mapper, key -> reason it cannot run)."""
    mappers, skipped = {}, {}
    if "rules" in keys:
        mappers["rules"] = RulesMapper()
    if "bm25" in keys:
        mappers["bm25"] = Bm25Mapper()
    if "llm" in keys:
        llm = LlmMapper(model=args.llm_model)
        if llm.available:
            mappers["llm"] = llm
        else:
            skipped["llm"] = "MISTRAL_API_KEY not set"
    if {"hybrid", "screengraph"} & set(keys):
        try:
            from evalkit import engine
        except ImportError as exc:
            for k in ("hybrid", "screengraph"):
                if k in keys:
                    skipped[k] = f"engine not importable: {exc}"
            return mappers, skipped
        engine.warm()
        if "hybrid" in keys:
            mappers["hybrid"] = lambda step, screen, verb: _hybrid(engine, screen, verb)
        if "screengraph" in keys:
            mappers["screengraph"] = lambda step, screen, verb: Prediction(
                *engine.resolve_step(step, screen, verb)
            )
    return mappers, skipped


def _hybrid(engine, screen: str, verb: str | None) -> Prediction:
    entry_id = engine.hybrid_top1(screen, verb)
    return Prediction(CATALOG, entry_id) if entry_id else Prediction(MANUAL)


def print_table(results: list[dict]) -> None:
    print(
        f"\n{'variant':<12} {'rel 0-2':>8} {'P@1':>6} {'tier ok':>8} {'wrong link':>11} {'p95 ms':>8}  eval-lane rel"
    )
    for r in results:
        if not r["measured"]:
            print(f"{r['key']:<12} not measured: {r['reason']}")
            continue
        a = r["all"]
        mine = r["by_owner"].get(EVAL_OWNER, {}).get("relevance_mean")
        print(
            f"{r['key']:<12} {a['relevance_mean']:>8.2f} {a['p_at_1']:>6.0%} {a['tier_accuracy']:>8.0%} "
            f"{a['wrong_link_rate']:>11.0%} {r['latency_ms']['p95']:>8.1f}  {mine if mine is not None else '-'}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--only", help="comma-separated subset of: " + ", ".join(VARIANTS))
    parser.add_argument("--gold", type=Path, default=GOLD_PATH)
    parser.add_argument("--out", type=Path, default=OUT_PATH)
    parser.add_argument("--llm-model", default="ministral-14b-latest")
    parser.add_argument("--rate-in", type=float, default=0.10, help="USD per 1M prompt tokens")
    parser.add_argument("--rate-out", type=float, default=0.30, help="USD per 1M completion tokens")
    parser.add_argument("--misses", action="store_true", help="print every step scored below 2")
    args = parser.parse_args()
    load_dotenv(REPO_ROOT / ".env")

    keys = args.only.split(",") if args.only else list(VARIANTS)
    unknown = set(keys) - set(VARIANTS)
    if unknown:
        parser.error(f"unknown variant(s): {', '.join(sorted(unknown))}")

    cases = load_gold(args.gold)
    screens = Screens.load()
    mappers, skipped = build_mappers(keys, args)

    results = []
    for key in keys:
        if key in skipped:
            results.append(not_measured(key, skipped[key]))
            continue
        result = run_variant(key, mappers[key], cases, screens)
        if key == "llm":
            usage = mappers[key].usage
            cost = (usage.prompt_tokens * args.rate_in + usage.completion_tokens * args.rate_out) / 1e6
            result["cost_per_step_usd"] = round(cost / max(usage.calls, 1), 6)
            result["llm"] = {
                "model": args.llm_model,
                "rates_usd_per_mtok": {"in": args.rate_in, "out": args.rate_out},
                **vars(usage),
            }
        results.append(result)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "commit": commit_sha(),
        "gold": {
            "path": str(args.gold.relative_to(REPO_ROOT))
            if args.gold.is_relative_to(REPO_ROOT)
            else str(args.gold),
            "n": len(cases),
            "by_tier": dict(Counter(c.tier for c in cases)),
            "by_owner": dict(Counter(c.owner for c in cases)),
        },
        "rubric": "relevance 0-2 per evalkit/relevance.py; P@1 over steps whose gold tier is catalog",
        "variants": results,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")

    print_table(results)
    if args.misses:
        for r in results:
            for m in r.get("misses", []):
                print(
                    f"  {r['key']:<11} rel {m['relevance']}  {m['screen']} [{m['verb']}]  "
                    f"want {m['gold_tier']}:{m['expected']}  got {m['got_tier']}:{m['got']}"
                )
    print(f"\nwritten to {args.out}")


if __name__ == "__main__":
    main()
