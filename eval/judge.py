"""LLM-as-judge: step accuracy 0-3 and deeplink relevance 0-2 (metrics.md section 2).

One judge call per plan. The judge sees the complaint, the SIIS article and the plan, and returns a
verdict per step (correct / partial / wrong, with the main issue), a 0-2 relevance per catalog link,
the article instructions the plan leaves out, whether the order works, and a 0-3 score for the plan.
The per-step issues are what to read when tuning the extraction prompt; the 0-3 mean is the number
the report publishes.

Plans come from results.jsonl (the 20 kit rows, joined to their articles by query) or, with --api,
from a running API over the kit rows and the 15 held-out unseen scenarios. A plan with no steps
scores 0 without a call: every article in these sets has a fix for its complaint.

The judge should not be the model that wrote the steps. By default it is Gemini when
GEMINI_API_KEY is set, otherwise Mistral; a plan written by the judge's own model family is
flagged as self-graded in the output. Judgments are cached in eval/results/judge_cache.json by
(model, prompt, plan), so re-running after a prompt change only re-judges the plans that changed.

Usage (from the repo root; keys are read from .env):
    python eval/judge.py --dry-run                            # print the first prompt, no key needed
    python eval/judge.py                                      # judge results.jsonl
    python eval/judge.py --api http://localhost:8000          # kit + unseen, live
    python eval/judge.py --provider mistral --model ministral-14b-latest --limit 5
Writes eval/results/judge.json.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))

from evalkit.paths import REPO_ROOT, RESULTS_DIR, RESULTS_JSONL, use_api_package
from evalkit.sets import load_kit, load_set, read_jsonl
from evalkit.stats import norm_query

use_api_package()

# The engine's own scrub, so the judge's provider sees exactly what the engine's LLM sees: the kit
# carries a real-looking address, and nothing unscrubbed goes to a third-party model.
from app.compiler.scrub import scrub
from app.pipeline.normalize import clean_siis, siis_title

# v2 adds the organisers' ordering rule (critical actions last, after contacting support); v1 marked
# that order as a problem on plans the spec requires it of.
PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "judge.v2.md"
PROMPT_VERSION = "judge-v2"
OUT_PATH = RESULTS_DIR / "judge.json"
CACHE_PATH = RESULTS_DIR / "judge_cache.json"
DUMMY_URI = "bixby://dummy_positive"
DEFAULT_MODELS = {"gemini": "gemini-3.5-flash-lite", "mistral": "ministral-14b-latest"}
KEY_ENV = {"gemini": "GEMINI_API_KEY", "mistral": "MISTRAL_API_KEY"}
MAX_ARTICLE_CHARS = 14000

VERDICTS = ("correct", "partial", "wrong")
VERDICT_CREDIT = {"correct": 1.0, "partial": 0.5, "wrong": 0.0}
ISSUES = (
    "none",
    "not_an_instruction",
    "fragment",
    "irrelevant",
    "unsupported",
    "duplicate",
    "wrong_action",
)

SCHEMA = {
    "type": "object",
    "properties": {
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "verdict": {"type": "string", "enum": list(VERDICTS)},
                    "issue": {"type": "string", "enum": list(ISSUES)},
                },
                "required": ["id", "verdict", "issue"],
                "additionalProperties": False,
            },
        },
        "links": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"action": {"type": "string"}, "relevance": {"type": "integer"}},
                "required": ["action", "relevance"],
                "additionalProperties": False,
            },
        },
        "missing": {"type": "array", "items": {"type": "string"}},
        "order_ok": {"type": "boolean"},
        "order_problem": {"type": "string"},
        "score": {"type": "integer"},
        "reason": {"type": "string"},
    },
    "required": ["steps", "links", "missing", "order_ok", "order_problem", "score", "reason"],
    "additionalProperties": False,
}


# ------------------------------------------------------------------------------------------ items


@dataclass
class Item:
    item_id: str
    source: str  # kit | unseen
    query: str
    article: str
    contexts: list
    engine_model: str | None = None


@dataclass
class Plan:
    """The plan flattened for the prompt: step ids A<action>.<step>, action ids A<n>."""

    text: str
    links: str
    step_ids: list[str] = field(default_factory=list)
    link_ids: list[str] = field(default_factory=list)


def article_text(siis: dict | str | None) -> str:
    """Title and body with URLs and email addresses scrubbed (`clean_siis`), as the engine's LLM sees them."""
    body, _ = clean_siis(siis)
    text = "\n".join(p for p in (siis_title(siis), body) if p and p.strip()).strip()
    if len(text) > MAX_ARTICLE_CHARS:
        text = text[:MAX_ARTICLE_CHARS] + "\n[article truncated]"
    return text


def flatten(contexts: list) -> Plan:
    lines, links, step_ids, link_ids = [], [], [], []
    n_action = 0
    for g, goal in enumerate(contexts if isinstance(contexts, list) else [], start=1):
        if not isinstance(goal, dict):
            continue
        lines.append(f"Goal {g}: {goal.get('title', '')} ({goal.get('goal', '')})")
        for action in goal.get("actions") or []:
            n_action += 1
            aid = f"A{n_action}"
            lines.append(f"  {aid}. {action.get('actionName', '')} [{action.get('category', '')}]")
            n_step = 0
            for group in action.get("stepGroups") or []:
                for step in group.get("steps") or []:
                    n_step += 1
                    sid = f"{aid}.{n_step}"
                    step_ids.append(sid)
                    lines.append(f"    {sid} {step}")
                link = group.get("actionableDeeplink") or {}
                uri = link.get("deeplink")
                if uri and uri != DUMMY_URI and aid not in link_ids:
                    link_ids.append(aid)
                    links.append(f"{aid} opens: {link.get('message', '')}. {link.get('description', '')}")
    return Plan("\n".join(lines), "\n".join(links) or "(no catalog links)", step_ids, link_ids)


def build_prompt(template: str, item: Item, plan: Plan) -> str:
    template = re.sub(r"\A#[^\n]*\n+", "", template)  # the title line is for humans
    values = {"query": scrub(item.query), "article": item.article, "plan": plan.text, "links": plan.links}
    return re.sub(r"\{\{(\w+)\}\}", lambda m: values.get(m.group(1), m.group(0)), template)


def items_from_results(path: Path) -> tuple[list[Item], list[str]]:
    """results.jsonl lines joined to their kit article by query."""
    by_query = {norm_query(r.query): r for r in load_kit()}
    items, notes = [], []
    for n, line in enumerate(read_jsonl(path), start=1):
        row = by_query.get(norm_query(str(line.get("query", ""))))
        if row is None:
            notes.append(f"{path.name} line {n}: query not in the kit, skipped")
            continue
        response = line.get("response") or {}
        meta = line.get("meta") or response.get("meta") or {}
        items.append(
            Item(
                row.row_id,
                "kit",
                row.query,
                article_text(row.siis),
                response.get("contexts") or [],
                meta.get("model"),
            )
        )
    return items, notes


def items_from_api(base_url: str) -> list[Item]:
    from evalkit.client import ApiClient

    client = ApiClient(base_url, timeout_s=60.0)
    requests = [(r.row_id, "kit", r.query, r.siis) for r in load_kit()]
    requests += [(u["id"], "unseen", u["query"], u.get("siis_response")) for u in load_set("unseen")]
    items = []
    for item_id, source, query, siis in requests:
        call = client.troubleshoot(query, siis)
        meta = call.body.get("meta") if isinstance(call.body, dict) else None
        model = meta.get("model") if isinstance(meta, dict) else None
        items.append(Item(item_id, source, query, article_text(siis), call.contexts, model))
    client.close()
    return items


# ------------------------------------------------------------------------------------------ judge


def family(model: str | None) -> str | None:
    m = (model or "").lower()
    if m.startswith("gemini"):
        return "gemini"
    if "stral" in m:  # mistral-*, ministral-*, magistral-*, devstral-*
        return "mistral"
    return None


class JudgeClient:
    """Plain REST, JSON-schema output, retries 429/5xx with backoff (free tiers answer both)."""

    def __init__(
        self, provider: str, model: str, temperature: float = 0.0, attempts: int = 5, timeout_s=90.0
    ):
        self.provider, self.model, self.temperature, self.attempts = provider, model, temperature, attempts
        self.key = os.environ.get(KEY_ENV[provider], "")
        self.http = httpx.Client(timeout=timeout_s)
        self.tokens_in = self.tokens_out = self.calls = self.failures = 0

    @property
    def available(self) -> bool:
        return bool(self.key)

    def _request(self, prompt: str) -> tuple[str, dict, dict]:
        if self.provider == "gemini":
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
            body = {
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "responseJsonSchema": SCHEMA,
                    "temperature": self.temperature,
                },
            }
            return url, {"x-goog-api-key": self.key}, body
        body = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "judgment", "schema": SCHEMA, "strict": True},
            },
        }
        return "https://api.mistral.ai/v1/chat/completions", {"Authorization": f"Bearer {self.key}"}, body

    def _text(self, data: dict) -> str:
        if self.provider == "gemini":
            usage = data.get("usageMetadata") or {}
            self.tokens_in += int(usage.get("promptTokenCount") or 0)
            self.tokens_out += int(usage.get("candidatesTokenCount") or 0) + int(
                usage.get("thoughtsTokenCount") or 0
            )
            cands = data.get("candidates") or []
            parts = (cands[0].get("content") or {}).get("parts") if cands else None
            return "".join(p.get("text", "") for p in parts or [] if not p.get("thought"))
        usage = data.get("usage") or {}
        self.tokens_in += int(usage.get("prompt_tokens") or 0)
        self.tokens_out += int(usage.get("completion_tokens") or 0)
        content = data["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(c.get("text", "") for c in content if isinstance(c, dict))
        return content

    def __call__(self, prompt: str) -> dict | None:
        url, headers, body = self._request(prompt)
        for attempt in range(self.attempts):
            try:
                r = self.http.post(url, json=body, headers=headers)
            except httpx.HTTPError:
                r = None
            if r is not None and r.status_code == 200:
                try:
                    self.calls += 1
                    return json.loads(self._text(r.json()))
                except (ValueError, KeyError, IndexError, TypeError):
                    pass  # a malformed answer is retried like a transient error
            elif r is not None and r.status_code not in (429, 500, 502, 503, 504):
                print(f"  judge call failed: HTTP {r.status_code} {r.text[:200]}", file=sys.stderr)
                break
            wait = 2 ** (attempt + 1)
            if r is not None and (r.headers.get("retry-after") or "").isdigit():
                wait = max(wait, int(r.headers["retry-after"]))
            if attempt < self.attempts - 1:
                time.sleep(min(wait, 60))
        self.failures += 1
        return None


def _clamp(value: object, top: int) -> int:
    try:
        return max(0, min(top, int(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def clean(raw: dict, plan: Plan) -> dict:
    """Keep only what the schema allows, clamp scores, and account for steps the judge skipped."""
    by_id = {}
    for s in raw.get("steps") or []:
        sid = str(s.get("id", "")).strip()
        if sid in plan.step_ids and sid not in by_id:
            verdict = s.get("verdict") if s.get("verdict") in VERDICTS else "wrong"
            issue = s.get("issue") if s.get("issue") in ISSUES else "none"
            by_id[sid] = {"id": sid, "verdict": verdict, "issue": "none" if verdict == "correct" else issue}
    links = {}
    for link in raw.get("links") or []:
        aid = str(link.get("action", "")).strip()
        if aid in plan.link_ids and aid not in links:
            links[aid] = _clamp(link.get("relevance"), 2)
    return {
        "score": _clamp(raw.get("score"), 3),
        "steps": [by_id[s] for s in plan.step_ids if s in by_id],
        "unjudged_steps": [s for s in plan.step_ids if s not in by_id],
        "links": [{"action": a, "relevance": links[a]} for a in plan.link_ids if a in links],
        "missing": [str(m) for m in (raw.get("missing") or [])][:5],
        "order_ok": bool(raw.get("order_ok", True)),
        "order_problem": str(raw.get("order_problem") or ""),
        "reason": str(raw.get("reason") or ""),
    }


def load_cache(path: Path) -> dict:
    try:
        return json.loads(path.read_text()) if path.exists() else {}
    except json.JSONDecodeError:
        return {}


def judge_items(
    items: list[Item], client, template: str, cache: dict, pause_s: float = 0.0, save=None
) -> list[dict]:
    """`save(cache)` runs after every new judgment, so an interrupted free-tier run keeps its work."""
    out = []
    model = getattr(client, "model", "?")
    judge_family = family(model)
    for i, item in enumerate(items, start=1):
        plan = flatten(item.contexts)
        row = {
            "id": item.item_id,
            "source": item.source,
            "query": item.query,
            "engine_model": item.engine_model,
            "self_graded": judge_family is not None and family(item.engine_model) == judge_family,
            "n_steps": len(plan.step_ids),
        }
        if not plan.step_ids:
            out.append({**row, "empty": True, "judged": True, "score": 0, "reason": "plan has no steps"})
            print(f"[{i}/{len(items)}] {item.item_id}: empty plan, 0")
            continue
        prompt = build_prompt(template, item, plan)
        key = hashlib.sha256(f"{model}\n{PROMPT_VERSION}\n{prompt}".encode()).hexdigest()[:24]
        cached = key in cache
        raw = cache.get(key) or client(prompt)
        if raw is None:
            out.append({**row, "empty": False, "judged": False})
            print(f"[{i}/{len(items)}] {item.item_id}: judge call failed")
            continue
        cache[key] = raw
        if save and not cached:
            save(cache)
        verdict = clean(raw, plan)
        out.append({**row, "empty": False, "judged": True, "cached": cached, **verdict})
        issues = Counter(s["issue"] for s in verdict["steps"] if s["issue"] != "none")
        print(
            f"[{i}/{len(items)}] {item.item_id}: {verdict['score']}/3"
            + (f"  issues {dict(issues)}" if issues else "")
            + ("" if verdict["order_ok"] else "  ORDER")
            + (f"  missing {len(verdict['missing'])}" if verdict["missing"] else "")
        )
        if pause_s and not cached and i < len(items):
            time.sleep(pause_s)
    return out


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def summarise(rows: list[dict]) -> dict:
    judged = [r for r in rows if r.get("judged")]
    steps = [s for r in judged for s in r.get("steps", [])]
    links = [link["relevance"] for r in judged for link in r.get("links", [])]
    by_source = {}
    for source in sorted({r["source"] for r in judged}):
        scores = [r["score"] for r in judged if r["source"] == source]
        by_source[source] = {"n": len(scores), "step_accuracy_mean": _mean(scores)}
    return {
        "n": len(rows),
        "judged": len(judged),
        "failed": len(rows) - len(judged),
        "empty_plans": sum(1 for r in judged if r.get("empty")),
        "self_graded": sum(1 for r in judged if r.get("self_graded") and not r.get("empty")),
        "step_accuracy_mean": _mean([r["score"] for r in judged]),
        "score_histogram": {str(k): sum(1 for r in judged if r["score"] == k) for k in range(4)},
        "by_source": by_source,
        "steps": {
            "n": len(steps),
            "credit": _mean([VERDICT_CREDIT[s["verdict"]] for s in steps]),
            "verdicts": {v: sum(1 for s in steps if s["verdict"] == v) for v in VERDICTS},
            "issues": dict(Counter(s["issue"] for s in steps if s["issue"] != "none").most_common()),
            "unjudged": sum(len(r.get("unjudged_steps", [])) for r in judged),
        },
        "link_relevance_mean": _mean(links),
        "links_judged": len(links),
        "order_problems": sum(1 for r in judged if not r.get("empty") and not r.get("order_ok", True)),
        "plans_missing_a_fix": sum(1 for r in judged if r.get("missing")),
    }


# ------------------------------------------------------------------------------------------- main


def main(argv: list[str] | None = None, client=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--results", type=Path, default=RESULTS_JSONL, help="results.jsonl to judge")
    parser.add_argument("--api", help="judge live answers from this API (kit + unseen) instead")
    parser.add_argument("--provider", choices=["gemini", "mistral"], help="default: gemini if its key is set")
    parser.add_argument("--model", help=f"default: {DEFAULT_MODELS}")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--limit", type=int, help="judge only the first N plans")
    parser.add_argument("--pause", type=float, default=1.0, help="seconds between uncached calls")
    parser.add_argument("--dry-run", action="store_true", help="print the first prompt and exit")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--out", type=Path, default=OUT_PATH)
    parser.add_argument("--cache", type=Path, default=CACHE_PATH)
    args = parser.parse_args(argv)

    try:
        from dotenv import load_dotenv

        load_dotenv(REPO_ROOT / ".env", override=False)
    except ImportError:
        pass

    template = PROMPT_PATH.read_text()
    notes: list[str] = []
    if args.api:
        items, source = items_from_api(args.api), f"HTTP against {args.api} (kit + unseen)"
    else:
        items, notes = items_from_results(args.results)
        source = str(
            args.results.relative_to(REPO_ROOT) if args.results.is_relative_to(REPO_ROOT) else args.results
        )
    items = items[: args.limit] if args.limit else items
    if not items:
        print("nothing to judge", file=sys.stderr)
        return 1

    if args.dry_run:
        first = next((i for i in items if flatten(i.contexts).step_ids), items[0])
        print(build_prompt(template, first, flatten(first.contexts)))
        return 0

    if client is None:
        provider = args.provider or ("gemini" if os.environ.get(KEY_ENV["gemini"]) else "mistral")
        client = JudgeClient(provider, args.model or DEFAULT_MODELS[provider], args.temperature)
        if not client.available:
            print(f"{KEY_ENV[provider]} is not set (add it to .env); try --dry-run", file=sys.stderr)
            return 2

    cache = {} if args.no_cache else load_cache(args.cache)

    def save(c: dict) -> None:
        args.cache.parent.mkdir(parents=True, exist_ok=True)
        args.cache.write_text(json.dumps(c, indent=1) + "\n")

    rows = judge_items(
        items, client, template, cache, pause_s=args.pause, save=None if args.no_cache else save
    )

    summary = summarise(rows)
    if summary["self_graded"]:
        notes.append(
            f"{summary['self_graded']} plans were written by the judge's own model family; "
            "their scores may be optimistic"
        )
    report = {
        **summary,
        "judge_model": getattr(client, "model", None),
        "prompt_version": PROMPT_VERSION,
        "temperature": args.temperature,
        "source": source,
        "engine_models": dict(Counter(r["engine_model"] or "none" for r in rows)),
        "judge_tokens": {"in": getattr(client, "tokens_in", 0), "out": getattr(client, "tokens_out", 0)},
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "notes": notes,
        "items": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")

    s = summary
    print(f"\nstep accuracy {s['step_accuracy_mean']} / 3 over {s['judged']} plans ({s['failed']} failed)")
    print(
        f"step credit {s['steps']['credit']}  verdicts {s['steps']['verdicts']}  issues {s['steps']['issues']}"
    )
    print(f"link relevance {s['link_relevance_mean']} / 2 over {s['links_judged']} catalog links")
    print(f"order problems {s['order_problems']}, plans missing a fix {s['plans_missing_a_fix']}")
    for note in notes:
        print(f"note: {note}")
    print(f"written to {args.out}")
    return 0 if not s["failed"] else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)  # progress shows while a long run is piped to a file
    sys.exit(main())
