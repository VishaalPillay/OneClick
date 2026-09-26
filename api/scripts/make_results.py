"""Offline: run cold pipeline over data/kit -> results.jsonl (meta beside response).

Every kit query runs on an empty cache, so each line is a genuinely cold answer. One JSON line per
query (Appendix B): {query, query_variations, response, meta}.

    python scripts/make_results.py                                   # -> <repo>/results.jsonl
    python scripts/make_results.py --extract-model gemini-3.5-flash-lite \\
        --out ../eval/results/bakeoff_gemini-3.5-flash-lite.jsonl     # model bake-off (Phase 3)
    python scripts/make_results.py --pause 4                         # free-tier keys: pace the calls
    python scripts/make_results.py --rows row_21                     # redo some rows, keep the other lines
"""

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# A measurement run must never read or write the API's real cache.sqlite: every row calls
# cache.clear(), so the path is always a throwaway, even when ONECLICK_SQLITE is exported.
os.environ["ONECLICK_SQLITE"] = str(Path(tempfile.mkdtemp(prefix="oneclick-results-")) / "cache.sqlite")

from app import cache
from app.config import settings
from app.obs import readiness
from app.pipeline.run import run_with_variations

REPO = Path(__file__).resolve().parents[2]


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--kit", type=Path, default=Path(settings.data_dir) / "kit" / "siis_responses.json")
    parser.add_argument("--input", type=Path, default=Path(settings.data_dir) / "kit" / "input.txt")
    parser.add_argument("--out", type=Path, default=REPO / "results.jsonl")
    parser.add_argument("--extract-model", help="override settings.extract_model for this run")
    parser.add_argument("--pause", type=float, default=0.0, help="seconds between queries (free-tier limits)")
    parser.add_argument(
        "--rows", help="comma-separated kit row ids to redo; every other line of --out is kept"
    )
    args = parser.parse_args()

    if args.extract_model:
        settings.extract_model = args.extract_model

    state = readiness.warm()
    if not state["ready"]:
        sys.exit(f"engine not ready: {state['error']} (run scripts/build_screengraph.py and build_index.py)")

    rows = json.loads(args.kit.read_text(encoding="utf-8"))["responses"]
    # The query is the organisers' input.txt line, as the scorer sends it; siis_responses.json
    # keeps its own copy with "1. " prefixes and newlines, so it only supplies the article.
    queries = [line.strip() for line in args.input.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(queries) != len(rows):
        sys.exit(f"{args.input} has {len(queries)} queries but {args.kit} has {len(rows)} rows")
    redo = set(args.rows.split(",")) if args.rows else None
    kept: list[str] = []
    if redo is not None:
        unknown = redo - {row["id"] for row in rows}
        kept = args.out.read_text(encoding="utf-8").splitlines() if args.out.exists() else []
        if unknown or len(kept) != len(rows):
            sys.exit(f"--rows needs known row ids and a complete {args.out} (unknown: {sorted(unknown)})")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    empty, latencies = 0, []
    lines: list[str] = []
    for n, (query, row) in enumerate(zip(queries, rows)):
        if redo is not None and row["id"] not in redo:
            lines.append(kept[n])
            continue
        if lines and args.pause:
            time.sleep(args.pause)
        cache.clear()  # every line is a cold answer
        body, variations = run_with_variations(query, row.get("siis_response"))
        meta = body.get("meta", {})
        line = {
            "query": query,
            "query_variations": variations,
            "response": {"contexts": body.get("contexts", [])},
            "meta": meta,
        }
        lines.append(json.dumps(line, ensure_ascii=False))
        empty += not line["response"]["contexts"]
        latencies.append(meta.get("latency_ms", 0.0))
        print(
            f"{row['id']:>7}  {meta.get('latency_ms', 0):7.0f} ms  goals={len(line['response']['contexts'])}"
            f"  model={meta.get('model')}  vars={len(variations)}  fallback={meta.get('fallback')}"
        )
    with args.out.open("w", encoding="utf-8", newline="\n") as out:
        out.write("".join(line + "\n" for line in lines))
    cache.clear()
    latencies.sort()
    p95 = latencies[max(0, round(0.95 * len(latencies)) - 1)] if latencies else 0.0
    print(
        f"wrote {len(rows)} lines to {args.out}, {len(latencies)} regenerated ({empty} empty, cold p95 {p95:.0f} ms)"
    )


if __name__ == "__main__":
    main()
