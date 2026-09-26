"""report.py renders the Appendix C layout and never shows a number it was not given."""

import json
from types import SimpleNamespace

import pytest

import report

APPENDIX_C_HEADINGS = [
    "# System Performance Metrics & Evaluation Report",
    "## 1. Schema & Rule Compliance",
    "## 2. Accuracy Benchmarks",
    "## 3. Latency Benchmarks (N >= 30 requests per path)",
    "## 4. Operational Cost & Cache Efficacy",
    "## 5. Architectural Ablation Analysis",
    "## 6. Known Edge Cases & System Limitations",
]


def render(tmp_path, **files) -> str:
    for name, body in files.items():
        (tmp_path / f"{name}.json").write_text(json.dumps(body))
    args = SimpleNamespace(model="m", embeddings="e", env="x", results_dir=tmp_path)
    return report.build(args)


def row(md: str, label: str) -> str:
    return next(line for line in md.splitlines() if line.startswith(f"| {label}"))


def test_empty_results_render_every_section_as_not_measured(tmp_path):
    md = render(tmp_path)
    positions = [md.index(h) for h in APPENDIX_C_HEADINGS]
    assert positions == sorted(positions)
    assert "not measured" in row(md, "Schema-valid output lines")
    assert "not measured" in row(md, "Step accuracy")
    assert "not measured" in row(md, "Cold query - full pipeline")
    assert "Baseline: Full LLM Deeplink Mapping" in md


GATES_EMPTY_PLANS = {
    "git_sha": "abc",
    "gates": {"G4": {"value": 1.0}, "G5": {"value": 0}},
    "blocks": {
        "A1": {"detail": {"responses": 40, "non_empty": 0, "rule_pass_rate": {"goal": 1.0}}},
        "A2": {"detail": {"catalog_validity": None, "auto_with_link": None}},
    },
}


def test_empty_plans_are_not_reported_as_full_compliance(tmp_path):
    md = render(tmp_path, gates=GATES_EMPTY_PLANS)
    assert "100.0%" not in row(md, "Schema-valid output lines")
    assert "all had empty `contexts`" in md


def test_non_empty_plans_fill_section_1(tmp_path):
    gates = json.loads(json.dumps(GATES_EMPTY_PLANS))
    gates["blocks"]["A1"]["detail"].update(
        non_empty=40, rule_pass_rate={"goal": 1.0, "title": 0.9, "description": 0.8}
    )
    gates["blocks"]["A2"]["detail"].update(catalog_validity=1.0, auto_with_link=0.95)
    md = render(tmp_path, gates=gates)
    assert "| 100.0% |" in row(md, "Schema-valid output lines")
    assert "90.0%" in row(md, "Rule compliance")
    assert "| 0 |" in row(md, "Absolute URL leaks")
    assert "95.0%" in row(md, "Auto actions carrying")


def variant(key, name, rel, measured=True):
    if not measured:
        return {"key": key, "name": name, "row": key, "measured": False, "reason": "MISTRAL_API_KEY not set"}
    group = {
        "n": 10,
        "relevance_mean": rel,
        "p_at_1": 0.8,
        "p_at_1_n": 5,
        "tier_accuracy": 0.9,
        "wrong_link_rate": 0.1,
    }
    return {
        "key": key,
        "name": name,
        "row": key,
        "measured": True,
        "all": group,
        "by_owner": {"a": group},
        "latency_ms": {"p50": 1.0, "p95": 2.0},
        "cost_per_step_usd": 0.0,
        "misses": [{"gold_tier": "catalog", "got_tier": "dummy", "screen": "Settings > Volume"}],
    }


ABLATION = {
    "commit": "abc",
    "gold": {"n": 10, "by_tier": {"catalog": 5, "dummy": 3, "manual": 2}, "by_owner": {"a": 10}},
    "variants": [
        variant("llm", "Baseline: Full LLM Deeplink Mapping", None, measured=False),
        variant("screengraph", "Ours: Screen Graph resolver", 1.8),
    ],
}


def test_ablation_fills_relevance_and_the_table(tmp_path):
    md = render(tmp_path, ablation=ABLATION)
    assert "| 1.80 |" in row(md, "Deeplink relevance")
    assert "MISTRAL_API_KEY not set" in row(md, "Baseline")
    assert "2.0 ms / step" in row(md, "Ours")
    assert "Settings > Volume" in md  # over-cautious fallback surfaces in section 6


def test_cache_mode_latency_and_hit_rate(tmp_path):
    section = {
        "source": "in-process",
        "warmed_with": "20 kit queries",
        "repeat": {"n": 40, "p50_ms": 0.2, "p95_ms": 0.3, "hit_rate": 1.0},
        "paraphrase": {"n": 200, "p50_ms": 8.0, "p95_ms": 12.0, "hit_rate": 0.805, "wrong_plan": 0},
        "near_miss": {"n": 60, "hits": 3, "hit_rate": 0.05, "false_hit_rate": 0.05, "leaked": []},
    }
    md = render(tmp_path, loadtest={"cache": section})
    assert "| 0.2 | 0.3 |" in row(md, "Cache hit - exact query match")
    assert "80.5%" in row(md, "Semantic cache hit rate")
    assert "not measured" in row(md, "Cold query - full pipeline")
    assert "5.0%" in md


@pytest.mark.parametrize("value", [None, 0.5])
def test_pct(value):
    assert report.pct(value) == ("not measured" if value is None else "50.0%")


JUDGE = {
    "judged": 20,
    "failed": 0,
    "empty_plans": 1,
    "self_graded": 0,
    "step_accuracy_mean": 2.35,
    "by_source": {"kit": {"n": 20, "step_accuracy_mean": 2.35}},
    "steps": {
        "n": 60,
        "verdicts": {"correct": 50, "partial": 6, "wrong": 4},
        "issues": {"not_an_instruction": 4},
    },
    "link_relevance_mean": 1.8,
    "links_judged": 10,
    "order_problems": 2,
    "plans_missing_a_fix": 3,
    "judge_model": "gemini-3-flash-preview",
    "prompt_version": "judge-v1",
    "source": "results.jsonl",
}


def test_judge_fills_step_accuracy(tmp_path):
    md = render(tmp_path, judge=JUDGE)
    assert "| 2.35 |" in row(md, "Step accuracy")
    assert "gemini-3-flash-preview as judge" in md
    assert "not an instruction 4" in md and "2 plans with an ordering problem" in md
    assert "step accuracy (`judge.py`)" not in md


def test_api_cold_cost_and_models(tmp_path):
    cold = {"n": 35, "hits": 0, "hit_rate": 0.0, "p50_ms": 3900.0, "p95_ms": 6400.0, "mean_cost_usd": 0.0}
    cold["models"] = {"ministral-14b-latest": 30, "rules": 5}
    load = {"api": {"source": "HTTP against x", "cold": cold}}
    args = SimpleNamespace(model=None, embeddings="e", env="x", results_dir=tmp_path)
    (tmp_path / "loadtest.json").write_text(json.dumps(load))
    md = report.build(args)
    assert "| $0.0000 |" in row(md, "Cold query average inference cost")
    assert "**Model(s):** ministral-14b-latest (30 cold queries), rules (5 cold queries)" in md
    assert "| 6400.0 |" in row(md, "Cold query - full pipeline")


def test_api_near_miss_sources_are_explained(tmp_path):
    nm = {
        "n": 60,
        "hits": 32,
        "hit_rate": 0.533,
        "false_hit_rate": 0.3,
        "hits_by_source": {"own_kit_answer": 16, "other_kit_answer": 2, "earlier_near_miss": 14},
    }
    md = render(tmp_path, loadtest={"api": {"source": "HTTP", "near_miss": nm}})
    assert "30.0% (target <= 2%)" in md
    assert "16 their own row's, 2 another row's" in md
    assert "earlier near miss 14" in md


def test_near_miss_limitation_counts_only_kit_answers(tmp_path):
    leaked = [
        {"query": "I want my screen dark", "differs_in": "intent", "source": "own_kit_answer"},
        {"query": "screen cracked", "differs_in": "symptom", "source": "earlier_near_miss"},
    ]
    nm = {"n": 60, "hits": 2, "hit_rate": 0.03, "false_hit_rate": 0.017, "leaked": leaked}
    md = render(tmp_path, loadtest={"api": {"source": "HTTP", "near_miss": nm}})
    assert "**Near-miss cache hits.** 1 of 60 near misses were served a kit answer (intent: 1)" in md
    assert "screen cracked" not in md and "slot guard compares" not in md
