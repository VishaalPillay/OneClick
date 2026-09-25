"""judge.py: plan flattening, prompt, clean-up of the judge's answer, caching and the summary."""

import json

import judge

PLAN = [
    {
        "goal": "Follow these steps to perform this Touchscreen Troubleshooting.",
        "title": "Touch lag",
        "actions": [
            {
                "actionName": "Turn On Touch Sensitivity",
                "category": "auto",
                "stepGroups": [
                    {
                        "steps": ["Open Settings.", "Tap Display.", "Turn on Touch sensitivity."],
                        "actionableDeeplink": {
                            "deeplink": "bixby://x",
                            "message": "Touch sensitivity",
                            "description": "Turns on touch sensitivity",
                        },
                    }
                ],
            },
            {
                "actionName": "Restart The Phone",
                "category": "critical",
                "stepGroups": [
                    {
                        "steps": ["Restart the phone."],
                        "actionableDeeplink": {"deeplink": "bixby://dummy_positive"},
                    }
                ],
            },
        ],
    }
]


def item(contexts=PLAN, model="ministral-14b-latest", item_id="row_1"):
    return judge.Item(item_id, "kit", "my screen lags", "Open Settings. Tap Display.", contexts, model)


class FakeClient:
    model = "gemini-3-flash-preview"

    def __init__(self, answer):
        self.answer, self.prompts = answer, []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        return self.answer


ANSWER = {
    "steps": [
        {"id": "A1.1", "verdict": "correct", "issue": "fragment"},  # a correct step has no issue
        {"id": "A1.2", "verdict": "partial", "issue": "fragment"},
        {"id": "A1.3", "verdict": "bogus", "issue": "made_up"},
        {"id": "A9.9", "verdict": "correct", "issue": "none"},  # not in the plan
    ],
    "links": [{"action": "A1", "relevance": 7}, {"action": "A2", "relevance": 2}],
    "missing": ["Remove the screen protector"],
    "order_ok": False,
    "order_problem": "Restart comes last",
    "score": "2",
    "reason": "ok",
}


def test_flatten_numbers_steps_and_lists_only_catalog_links():
    plan = judge.flatten(PLAN)
    assert plan.step_ids == ["A1.1", "A1.2", "A1.3", "A2.1"]
    assert plan.link_ids == ["A1"]  # the dummy placeholder is not a link to grade
    assert "A1.3 Turn on Touch sensitivity." in plan.text
    assert "Touch sensitivity" in plan.links
    assert judge.flatten([]).links == "(no catalog links)"


def test_prompt_fills_every_hole_and_drops_the_title():
    template = judge.PROMPT_PATH.read_text()
    prompt = judge.build_prompt(template, item(), judge.flatten(PLAN))
    assert "{{" not in prompt
    assert not prompt.startswith("#")
    assert "my screen lags" in prompt and "A2.1 Restart the phone." in prompt


def test_article_text_handles_every_siis_shape():
    assert judge.article_text({"title": "T", "content": "C"}) == "T\nC"
    assert judge.article_text("plain") == "plain"
    assert judge.article_text({}) == "" and judge.article_text(None) == ""
    assert judge.article_text("x" * (judge.MAX_ARTICLE_CHARS + 5)).endswith("[article truncated]")


def test_article_and_query_are_scrubbed_before_the_judge_sees_them():
    siis = {
        "title": "Help at www.example.com",
        "content": "Mail kidshome.pin@samsung.com or see https://x.io/a.",
    }
    text = judge.article_text(siis)
    assert "@" not in text and "http" not in text and "www." not in text
    item = judge.Item("r", "kit", "see https://x.io my screen lags", text, [])
    prompt = judge.build_prompt("{{query}}\n{{article}}", item, judge.flatten([]))
    assert "http" not in prompt and "my screen lags" in prompt


def test_clean_clamps_and_accounts_for_skipped_steps():
    out = judge.clean(ANSWER, judge.flatten(PLAN))
    assert out["score"] == 2
    assert [s["id"] for s in out["steps"]] == ["A1.1", "A1.2", "A1.3"]
    assert out["steps"][0]["issue"] == "none"
    assert out["steps"][2] == {"id": "A1.3", "verdict": "wrong", "issue": "none"}
    assert out["unjudged_steps"] == ["A2.1"]
    assert out["links"] == [{"action": "A1", "relevance": 2}]
    assert judge.clean({"score": "high"}, judge.flatten(PLAN))["score"] == 0


def test_empty_plan_scores_zero_without_a_call():
    client = FakeClient(ANSWER)
    rows = judge.judge_items([item(contexts=[])], client, "{{plan}}", {})
    assert client.prompts == []
    assert rows[0]["score"] == 0 and rows[0]["empty"]


def test_cache_skips_the_second_call_and_self_grading_is_flagged():
    cache: dict = {}
    first = FakeClient(ANSWER)
    judge.judge_items([item()], first, "{{plan}}", cache)
    second = FakeClient(None)  # would fail if called
    rows = judge.judge_items([item(model="gemini-3-flash-preview")], second, "{{plan}}", cache)
    assert second.prompts == []
    assert rows[0]["cached"] and rows[0]["judged"]
    assert rows[0]["self_graded"]


def test_failed_call_is_reported_not_scored():
    rows = judge.judge_items([item()], FakeClient(None), "{{plan}}", {})
    assert rows == [{**rows[0], "judged": False}]
    summary = judge.summarise(rows)
    assert summary["failed"] == 1 and summary["step_accuracy_mean"] is None


def test_summary():
    rows = judge.judge_items([item(), item(contexts=[], item_id="row_2")], FakeClient(ANSWER), "{{plan}}", {})
    s = judge.summarise(rows)
    assert s["step_accuracy_mean"] == 1.0  # (2 + 0) / 2
    assert s["empty_plans"] == 1
    assert s["steps"]["verdicts"] == {"correct": 1, "partial": 1, "wrong": 1}
    assert s["steps"]["credit"] == 0.5
    assert s["steps"]["issues"] == {"fragment": 1}
    assert s["steps"]["unjudged"] == 1
    assert s["link_relevance_mean"] == 2.0
    assert s["order_problems"] == 1 and s["plans_missing_a_fix"] == 1
    assert s["score_histogram"] == {"0": 1, "1": 0, "2": 1, "3": 0}


def test_family():
    assert judge.family("ministral-8b-latest") == judge.family("mistral-small-latest") == "mistral"
    assert judge.family("gemini-3-flash-preview") == "gemini"
    assert judge.family("rules") is None and judge.family(None) is None


def test_main_judges_results_file_end_to_end(tmp_path):
    kit = judge.load_kit()
    line = {"query": kit[0].query, "response": {"contexts": PLAN}, "meta": {"model": "rules"}}
    results = tmp_path / "results.jsonl"
    results.write_text(json.dumps(line) + "\n" + json.dumps({"query": "not a kit query"}) + "\n")
    out = tmp_path / "judge.json"
    args = ["--results", str(results), "--out", str(out), "--cache", str(tmp_path / "c.json"), "--pause", "0"]
    assert judge.main(args, client=FakeClient(ANSWER)) == 0
    report = json.loads(out.read_text())
    assert report["step_accuracy_mean"] == 2.0
    assert report["judge_model"] == "gemini-3-flash-preview"
    assert report["items"][0]["id"] == kit[0].row_id
    assert any("not in the kit" in n for n in report["notes"])
    assert json.loads((tmp_path / "c.json").read_text())


def test_dry_run_needs_no_key(tmp_path, capsys):  # conftest blanks both keys
    kit = judge.load_kit()
    results = tmp_path / "results.jsonl"
    results.write_text(json.dumps({"query": kit[0].query, "response": {"contexts": PLAN}}) + "\n")
    assert judge.main(["--results", str(results), "--dry-run"]) == 0
    assert "A1.1 Open Settings." in capsys.readouterr().out
