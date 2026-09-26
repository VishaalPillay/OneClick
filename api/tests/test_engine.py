"""Engine lane (C1, C4-C6, C8-C11, validate): the fixtures are the spec, the kit is the smoke test.

Runs without LLM keys, so it exercises the rules-only path CI uses.
"""

import json
import random
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import cache
from app.compiler.compile import compile_with_report, dummy_link
from app.compiler.scrub import has_leak, scrub
from app.compiler.templates import build_goal
from app.compiler.trimmer import trim_description, trim_title
from app.compiler.validate import validate_with_report
from app.main import app
from app.models import DraftAction, DraftStep, Intent, LinkDecision, LinkTier
from app.pipeline.categorize import categorize
from app.pipeline.extract import screen_path
from app.pipeline.ground import ground_with_report
from app.pipeline.multi_intent import dedupe_with_report
from app.pipeline.normalize import clean_siis, display_query, normalize_query
from app.pipeline.order import order
from app.pipeline.run import run_with_variations
from app.pipeline.segment import segment_with_sections, split_sections
from app.pipeline.text import recognisable
from app.schema import ContextDeeplinkResponse

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "data" / "fixtures"
SCENARIOS = ["touch_lag", "email_not_responding", "touch_multi_intent"]
KIT = json.loads((ROOT / "data/kit/siis_responses.json").read_text(encoding="utf-8"))["responses"]
LEAK = re.compile(
    r"https?://|www\.|\b[\w-]+\.(?:com|net|org|io|gov|edu)\b|[\w.+-]+@[\w-]+\.[\w.]+", re.IGNORECASE
)

client = TestClient(app)


def _load(*parts):
    return json.loads(FIXTURES.joinpath(*parts).read_text(encoding="utf-8"))


def _stream(name):
    return {e["stage"]: e["detail"] for e in _load(name, "stream.json")}


def _drafts(name):
    return [DraftAction.model_validate(a) for a in _load(name, "draft_actions.json")["actions"]]


def _strings(obj, key=""):
    if isinstance(obj, str):
        if key != "deeplink":
            yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _strings(v, k)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v, key)


# ---- C1 normalize + scrub ---------------------------------------------------------------------------
def test_scrub_removes_every_kind_of_link_and_keeps_the_text():
    assert scrub("Mail kidshome.pin@samsung.com today") == "Mail today"
    assert scrub("See [the guide](https://x.example/y) here") == "See the guide here"
    assert scrub("Visit www.samsung.com/support now.") == "Visit now."
    assert scrub('Go to <a href="http://a.b">link</a>.') == "Go to link."
    assert scrub("Tap Settings > Display.") == "Tap Settings > Display."
    assert scrub("Step 1.In Settings, tap Display.") == "Step 1.In Settings, tap Display."
    for text in ("http://x.y", "www.x", "a@b.com", "[a](b)", "samsung.com"):
        assert has_leak(text) and not has_leak(scrub(text))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Visit samsung.co.in for help.", "Visit for help."),  # India and short-link endings
        ("Scan t.co/abc123 for details.", "Scan for details."),
        ("Open 192.168.1.1:8080 in a browser.", "Open in a browser."),
        ("Visit samsung\u200b.com today.", "Visit today."),  # zero-width space hiding a domain
        ("ｈｔｔｐｓ：／／samsung．com", ""),  # full-width characters (NFKC)
        ("Go to &lt;a href=&quot;https://x.com&quot;&gt;here&lt;/a&gt;.", "Go to here."),  # HTML entities
        ("Mail kids.pin+x@mail.samsung.co.kr now.", "Mail now."),  # no ".kr" left behind
        ("Contact mailto:help@samsung.com please.", "Contact please."),
        ("[guide][1]\n\n[1]: https://samsung.com/guide", "guide"),  # markdown reference link
    ],
)
def test_scrub_catches_disguised_and_regional_links(text, expected):
    assert scrub(text) == expected
    assert has_leak(text) and not has_leak(scrub(text))


@pytest.mark.parametrize(
    "text",
    [
        "Step 1.In Settings, tap Display.",
        "Check the app.In the list, tap Storage.",  # glued kit text: capitalised ".In" is not a domain
        "Android 14.0 or One UI 6.1 is required.",
        "Your file is saved as report.pdf in My Files.",
        "It works on Galaxy S22/S23 models.",
        "Call 1-800-726-7864.",
    ],
)
def test_scrub_keeps_ordinary_text(text):
    assert scrub(text) == text and not has_leak(text)


def test_scrub_is_linear_on_hostile_input():
    """Every pattern is length-bounded: a 50k-character payload must not stall a worker."""
    import time

    for payload in ("a." * 25_000, "a" * 50_000, "a@" * 25_000, "[" * 50_000):
        started = time.perf_counter()
        scrub(payload)
        assert time.perf_counter() - started < 1.0, payload[:10]


def test_the_complaint_is_scrubbed_before_any_llm_sees_it():
    query = '1. "My email john.doe@gmail.com won\'t sync"\n2. "I followed https://bit.ly/fix"'
    assert display_query(query) == "My email won't sync I followed"
    assert normalize_query(query) == "my email john.doe@gmail.com won't sync i followed https://bit.ly/fix"


def test_kit_articles_are_clean_after_normalize_and_keep_their_words():
    for row in KIT:
        clean, digest = clean_siis(row["siis_response"])
        assert clean and len(digest) == 16 and not has_leak(clean) and not LEAK.search(clean)
    glued = next(r for r in KIT if "kidshome" in r["siis_response"]["content"])
    clean, _ = clean_siis(glued["siis_response"])
    assert "kidshome" not in clean and "usingyourregisteredemailaddress" in clean  # only the address goes


@pytest.mark.parametrize("name", SCENARIOS)
def test_normalize_matches_the_fixture_cache_keys(name):
    req, miss = _load(name, "request.json"), _stream(name)["cache"]
    assert normalize_query(req["query"]) == miss["norm_query"]
    assert clean_siis(req["siis_response"])[1] == miss["siis_hash"]


def test_normalize_strips_kit_numbering_on_one_line_or_many():
    assert normalize_query('1. "My Screen  is BLACK"') == "my screen is black"
    assert normalize_query('1. "Screen is cracked."\n2. "Touch fails."') == "screen is cracked. touch fails."
    assert normalize_query('1. "Screen is cracked." 2. "Touch fails."') == "screen is cracked. touch fails."
    # Only the next number in sequence splits: a version number inside a complaint stays.
    assert normalize_query("Android 14. The screen flickers") == "android 14. the screen flickers"
    assert clean_siis(None) == ("", None)


def test_every_input_txt_query_normalizes_like_its_kit_row():
    kit = ROOT / "data" / "kit"
    lines = [line for line in (kit / "input.txt").read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = json.loads((kit / "siis_responses.json").read_text(encoding="utf-8"))["responses"]
    assert len(lines) == len(rows)
    for line, row in zip(lines, rows):
        assert normalize_query(line) == normalize_query(row["original_query"])


# ---- C4 segment -------------------------------------------------------------------------------------
@pytest.mark.parametrize("name", SCENARIOS)
def test_segment_reproduces_the_fixture_sentences(name):
    clean, _ = clean_siis(_load(name, "request.json")["siis_response"])
    expected = [(s["section"], s["text"]) for s in _stream(name)["segment"]["sentences"]]
    got = [(section["heading"], text) for section in split_sections(clean) for text in section["sentences"]]
    assert got == expected
    sentences, sections = segment_with_sections(clean, [Intent(text="touch screen is slow")])
    assert [s.id for s in sentences] == [f"S{n}" for n in range(1, len(sentences) + 1)]
    assert all(0.0 <= s["relevance"][0] <= 1.0 for s in sections)


# ---- C6 ground --------------------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["touch_lag", "email_not_responding"])
def test_grounding_keeps_every_fixture_step_and_drops_inventions(name):
    clean, _ = clean_siis(_load(name, "request.json")["siis_response"])
    sentences, _ = segment_with_sections(clean, [Intent(text="screen problem")])
    drafts = _drafts(name)
    kept, report = ground_with_report(drafts, sentences)
    assert report["kept_steps"] == report["proposed_steps"] == sum(len(d.steps) for d in drafts)

    invented = DraftAction(
        name="Recalibrate Touchscreen",
        steps=[
            DraftStep(text="Recalibrate the touchscreen from the Samsung Members app.", src_ids=["S2"]),
            DraftStep(text="Tap the hidden developer switch.", src_ids=["S999"]),
        ],
    )
    kept, report = ground_with_report([invented], sentences)
    assert kept == [] and report["kept_steps"] == 0 and len(report["dropped_steps"]) == 2
    assert report["dropped_steps"][1]["reason"] == "unknown_source"


# ---- C8 categorize + C9 order -----------------------------------------------------------------------
@pytest.mark.parametrize("name", SCENARIOS)
@pytest.mark.parametrize("seed", [0, 1, 2])
def test_categorize_and_order_reproduce_the_fixture_from_any_input_order(name, seed):
    expected = _drafts(name)
    raw = [
        a.model_copy(update={"category": "manual", "disruption_rank": 0, "depends_on": []}) for a in expected
    ]
    random.Random(seed).shuffle(raw)
    got = categorize(raw)
    by_name = {a.name: a for a in got}
    for e in expected:
        g = by_name[e.name]
        assert (g.category, g.disruption_rank, g.depends_on) == (e.category, e.disruption_rank, e.depends_on)
    for index in {a.intent_index for a in got}:
        ordered = [a.name for a in order([a for a in got if a.intent_index == index])]
        assert ordered == [a.name for a in expected if a.intent_index == index]


def test_auto_needs_a_link_and_critical_wins_over_a_link():
    link = LinkDecision(tier=LinkTier.dummy, entry_id="DL-DUMMY")
    reset = DraftAction(name="Factory Data Reset", steps=[DraftStep(text="Reset.")], link=link)
    toggle = DraftAction(name="Enable Dark Mode", steps=[DraftStep(text="Tap Dark mode.")], link=link)
    bare = DraftAction(name="Enable Dark Mode", steps=[DraftStep(text="Tap Dark mode.")])
    assert [a.category for a in categorize([reset, toggle, bare])] == ["critical", "auto", "manual"]


# ---- C11 multi-intent -------------------------------------------------------------------------------
def test_duplicate_actions_stay_only_in_the_more_relevant_goal():
    intents = [Intent(text="a", relevance=0.4), Intent(text="b", relevance=0.9)]
    step = [DraftStep(text="Restart your phone.", src_ids=["S1"])]
    actions = [
        DraftAction(name="Restart Your Device", steps=step, intent_index=0),
        DraftAction(name="Restart Your Device", steps=step, intent_index=1),
        DraftAction(name="Only Here", steps=[DraftStep(text="Tap it.")], intent_index=0),
    ]
    kept, report = dedupe_with_report(intents, actions)
    assert [(a.name, a.intent_index) for a in kept] == [("Restart Your Device", 1), ("Only Here", 0)]
    assert report == [{"action": "Restart Your Device", "kept_in_intent": 1, "removed_from_intents": [0]}]


# ---- C10 compile ------------------------------------------------------------------------------------
@pytest.mark.parametrize("name", SCENARIOS)
def test_compiler_reproduces_the_fixture_plan(name):
    stream = _stream(name)
    intents = [Intent.model_validate(i) for i in stream["enrich"]["intents"]]
    coverage = {x["intent_index"]: x["grounding_coverage"] for x in stream["compile"]["score_inputs"]}
    goals, report = compile_with_report(
        intents, _drafts(name), topics=stream["extract"]["topics"], coverage=coverage
    )
    assert goals == _load(name, "plan.json")["contexts"]
    assert report["score_inputs"] == stream["compile"]["score_inputs"]


def test_string_rules():
    assert build_goal("Screen Damage Troubleshooting") == (
        "Follow these steps to perform this Screen Damage Troubleshooting."
    )
    assert build_goal("wi-fi configuration", "Configuration") == (
        "Follow these steps to perform this Wi-Fi Configuration."
    )
    assert trim_title("The Screen Display Damage Issue") == "Screen display damage"
    assert trim_title("battery") == "Battery issue"
    for draft in (
        "It will help you locate the nearest Samsung service center and schedule",
        "verify",
        "",
        "It will clear the temporary data of your email app.",
        "It will verify your internet connection",
    ):
        out = trim_description(draft)
        assert out.startswith("It will ") and 5 <= len(out.split()) <= 7 and not out.endswith("."), out
    assert (
        trim_description("It will verify your internet connection")
        == "It will verify your internet connection"
    )
    link = dummy_link("Settings > Apps > Email app > Storage")
    assert link["description"] == "Opens the email app storage settings page"
    assert link["message"] == "Open email app storage screen"
    assert all(
        5 <= len(dummy_link(p)[k].split()) <= 7
        for p in ("Settings > Display", "")
        for k in ("description", "message")
    )


# ---- validate ---------------------------------------------------------------------------------------
def test_validate_repairs_scrubs_and_drops_but_never_fails():
    body = {
        "contexts": [
            {
                "goal": "Follow these steps to perform this Screen Troubleshooting",
                "title": "the black screen problem today",
                "score": 1.7,
                "actions": [
                    {
                        "actionName": "Visit Support",
                        "description": "It will help you locate the nearest Samsung service center and schedule",
                        "stepGroups": [{"steps": ["1. Go to www.samsung.com/support", "Call us"]}],
                        "category": "auto",  # no link: must become manual
                    },
                    {"actionName": "Broken", "description": "x", "stepGroups": "nope"},
                ],
            },
            {"goal": "junk", "title": "t", "score": 0.5, "actions": []},
        ],
        "meta": {"trace_id": "t"},
    }
    out, report = validate_with_report(body)
    ContextDeeplinkResponse.model_validate(out)
    (goal,) = out["contexts"]
    assert goal["goal"].endswith("Screen Troubleshooting.") and goal["score"] == 1.0
    assert 2 <= len(goal["title"].split()) <= 3
    (action,) = goal["actions"]
    assert action["category"] == "manual" and action["stepGroups"][0]["steps"] == ["Go to.", "Call us."]
    assert report["url_leaks"] >= 1 and report["repairs"] == 1
    assert out["meta"] == {"trace_id": "t"}
    assert validate_with_report({})[0] == {"contexts": []}
    assert validate_with_report({"contexts": "garbage"})[0]["contexts"] == []


def test_screen_path_ends_on_the_setting_a_switch_changes():
    step = 'To do this, go to Settings, tap Display, and then tap the switch next to "Touch sensitivity".'
    assert screen_path([step]) == "Settings > Display > Touch sensitivity"
    assert screen_path(["Go to Settings, tap Display, then tap Navigation bar."]) == (
        "Settings > Display > Navigation bar"
    )


def test_title_case_after_a_hyphen_is_not_a_name():
    assert trim_title("Inner screen Non-Responsive") == "Inner screen non-responsive"
    assert trim_title("Wi-Fi keeps dropping") == "Wi-Fi keeps dropping"
    assert trim_title("Samsung Smart Switch transfer") == "Samsung Smart Switch"


def test_restarting_is_critical_in_every_form():
    for name in ("Restart Your Device", "Restarting Your Device", "Force Restart", "Reboot the phone"):
        (action,) = categorize([DraftAction(name=name, steps=[DraftStep(text="Press and hold Power.")])])
        assert action.category == "critical", name


WASHING_MACHINE = {
    "title": "Cleaning the filter on a Samsung washing machine",
    "content": "Home Appliance: # Cleaning the Debris Filter\n## Step 1: Open the Filter Cover\n"
    "Open the small cover at the bottom front of the washing machine and place a shallow tray under it.\n"
    "## Step 2: Drain the Hose\nPull out the emergency drain hose and let the remaining water run out.\n"
    "## Step 3: Clean the Filter\nTurn the filter counterclockwise, rinse it under running water, and "
    "screw it back in.",
}


def test_an_article_about_something_else_is_a_no_match_even_without_a_model():
    """Rules-only extraction copies the article's instructions, so it must refuse an article that is
    about something else: a phone complaint never gets washing machine steps."""
    cache.clear()
    r = client.post(
        "/v1/troubleshoot",
        json={
            "query": "My Galaxy S22 screen is completely black and will not turn on.",
            "siis_response": WASHING_MACHINE,
        },
    )
    assert r.status_code == 200 and r.json()["contexts"] == [] and r.json()["meta"]["fallback"] == "no_match"
    cache.clear()


@pytest.mark.parametrize("query", ["asdkjh qwe zzz 12345 ?????? ////", "", "   "])
def test_a_complaint_with_no_readable_word_is_a_no_match(query):
    cache.clear()
    row = KIT[0]
    r = client.post("/v1/troubleshoot", json={"query": query, "siis_response": row["siis_response"]})
    assert r.status_code == 200 and r.json()["contexts"] == [] and r.json()["meta"]["fallback"] == "no_match"
    cache.clear()


def test_typos_and_other_languages_are_still_read():
    assert recognisable("my galxy s22 screne is compltely blak", "The screen is black.")
    assert recognisable("bhai mera screen bilkul black ho gaya", "The screen is black.")
    assert not recognisable("asdkjh qwe zzz 12345", "The screen is black.")


# ---- the whole pipeline on the kit (rules-only: no keys in CI) ---------------------------------------
@pytest.fixture(scope="module")
def kit_runs():
    cache.clear()
    runs = [run_with_variations(row["original_query"], row["siis_response"]) for row in KIT]
    cache.clear()
    return runs


def test_every_kit_query_gets_a_valid_non_empty_leak_free_answer(kit_runs):
    for row, (body, variations) in zip(KIT, kit_runs):
        ContextDeeplinkResponse.model_validate(body)
        assert body["contexts"], row["id"]
        assert not [s for s in _strings(body["contexts"]) if LEAK.search(s) or has_leak(s)], row["id"]
        for goal in body["contexts"]:
            for action in goal["actions"]:
                has_link = any(g.get("actionableDeeplink") for g in action["stepGroups"])
                assert (action["category"] == "auto") == has_link, (row["id"], action["actionName"])
        if not body["meta"]["cache_hit"]:  # a kit row that shares an article may hit the cache
            assert 8 <= len(variations) <= 10, row["id"]


def test_endpoint_is_always_200_and_schema_valid():
    cache.clear()
    row = KIT[0]
    first = client.post(
        "/v1/troubleshoot", json={"query": row["original_query"], "siis_response": row["siis_response"]}
    )
    second = client.post(
        "/v1/troubleshoot", json={"query": row["original_query"], "siis_response": row["siis_response"]}
    )
    for r in (first, second):
        assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
        ContextDeeplinkResponse.model_validate(r.json())
    assert first.headers["x-cache-hit"] == "false" and second.headers["x-cache-hit"] == "true"
    assert second.json()["contexts"] == first.json()["contexts"]
    for siis in (None, "", {"weird": 1}, "no headers, just words", {"content": "# T\n" + "x" * 5000}):
        r = client.post("/v1/troubleshoot", json={"query": "screen is black", "siis_response": siis})
        assert r.status_code == 200
        ContextDeeplinkResponse.model_validate(r.json())
    r = client.post("/v1/troubleshoot", json={"query": "my washing machine drum makes a loud grinding noise"})
    assert r.json()["contexts"] == [] and r.json()["meta"]["fallback"] == "no_siis_context"
