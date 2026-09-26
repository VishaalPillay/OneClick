/**
 * Build-time reads for the story page: the LLM prompt files and the eval sets.
 *
 * Server only — it touches the filesystem. `next build` runs from `console/`, so the repo root
 * is one level up. Every value here is read or counted from a file in the repo; the two that
 * need Python to recompute are constants with the command that produced them.
 */

import { existsSync, readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import type { CatalogEntry } from "@/lib/checks";
import type { LlmConfig, Metrics, MultiIntent, Preset, PromptFile, Proof, StoryInputs } from "@/lib/story";

// The page is prerendered, so these reads only ever run during `next build`. The ignore keeps
// the bundler from tracing the whole repo into the server output on account of them.
const REPO = path.join(/* turbopackIgnore: true */ process.cwd(), "..");
const PROMPTS = path.join(REPO, "api/app/llm/prompts");

/**
 * The newest version of a prompt, e.g. `extract.v2.md` over `extract.v1.md`.
 *
 * Prompts are versioned rather than edited in place (the version is part of the cache key), so
 * the page always shows whatever the engine would send today. A file that is still the
 * `TODO(A)` stub comes back with `body: null` and the page says so instead of inventing one.
 */
function latestPrompt(stem: "variations" | "extract"): PromptFile {
  const pattern = new RegExp(`^${stem}\\.v(\\d+)\\.md$`);
  const versions = existsSync(PROMPTS)
    ? readdirSync(PROMPTS)
        .map((f) => ({ f, v: Number(pattern.exec(f)?.[1] ?? NaN) }))
        .filter((x) => Number.isFinite(x.v))
        .sort((a, b) => b.v - a.v)
    : [];
  const file = versions[0]?.f ?? `${stem}.v1.md`;
  const raw = versions[0] ? readFileSync(path.join(PROMPTS, file), "utf8") : "";

  // Drop the title line; what is left is the instruction text.
  const body = raw
    .split("\n")
    .filter((line, i) => !(i === 0 && line.startsWith("#")))
    .join("\n")
    .trim();
  const stub = body === "" || /^TODO\b/.test(body);
  return { file, body: stub ? null : body };
}

function jsonl(rel: string): Record<string, unknown>[] {
  const p = path.join(REPO, rel);
  if (!existsSync(p)) return [];
  return readFileSync(p, "utf8")
    .split("\n")
    .filter((l) => l.trim())
    .map((l) => JSON.parse(l) as Record<string, unknown>);
}

function json<T>(rel: string): T | null {
  const p = path.join(REPO, rel);
  return existsSync(p) ? (JSON.parse(readFileSync(p, "utf8")) as T) : null;
}

/**
 * The model settings from the engine section of api/app/config.py, so the page names the models
 * and temperatures the engine really uses. The fallbacks are the values at the engine freeze.
 */
function readLlmConfig(): LlmConfig {
  const file = path.join(REPO, "api/app/config.py");
  const src = existsSync(file) ? readFileSync(file, "utf8") : "";
  const str = (name: string, fallback: string) =>
    new RegExp(`^\\s*${name}: str = "([^"]*)"`, "m").exec(src)?.[1] || fallback;
  const num = (name: string, fallback: number) => {
    const v = Number(new RegExp(`^\\s*${name}: float = ([\\d.]+)`, "m").exec(src)?.[1]);
    return Number.isFinite(v) ? v : fallback;
  };
  return {
    model: str("extract_model", "ministral-14b-latest"),
    fastModel: str("extract_fast_model", "ministral-8b-latest"),
    variationsModel: str("variations_model", "ministral-14b-latest"),
    temperature: num("llm_temperature_mistral", 0),
    fallback: str("fallback_model", "gemini-3-flash-preview"),
    fallbackTemperature: num("llm_temperature_gemini", 1),
  };
}

/**
 * The Proof section's numbers, parsed from docs/metrics.md (written by eval/report.py), so the page
 * can only ever show what the committed report says. A cell the report marks "not measured", or
 * a line it does not have, comes back null and the page shows it as pending.
 */
function readMetrics(): Metrics {
  const file = path.join(REPO, "docs/metrics.md");
  const md = existsSync(file) ? readFileSync(file, "utf8") : "";
  const config = path.join(REPO, "api/app/config.py");
  const cfg = existsSync(config) ? readFileSync(config, "utf8") : "";
  const threshold = Number(/^\s*cache_sim_threshold: float = ([\d.]+)/m.exec(cfg)?.[1] ?? NaN);

  const num = (s: string | undefined) => {
    const v = Number.parseFloat((s ?? "").replace(/[,$%]/g, ""));
    return Number.isFinite(v) ? v : null;
  };
  // Cells of the table row whose first cell starts with `label` (index 0 is that first cell).
  const cells = (label: string) =>
    md
      .split("\n")
      .find((l) => l.startsWith(`| ${label}`))
      ?.split("|")
      .slice(1, -1)
      .map((c) => c.trim()) ?? [];
  const match = (re: RegExp, text = md) => re.exec(text)?.[1];

  const ours = cells("Ours:")[4] ?? "";
  const bm25 = cells("Extra: BM25")[4] ?? "";
  const llm = cells("Baseline: Full LLM");
  const exact = cells("Cache hit - exact");
  const para = cells("Cache hit - unseen semantic");
  const cold = cells("Cold query - full pipeline");
  const nearMiss = /False hits on (\d+) near misses[^:]*: ([\d.]+)%/.exec(md);

  return {
    commit: match(/at commit `(\w+)`/) ?? null,
    date: match(/at commit `\w+` on (\d{4}-\d\d-\d\d)/) ?? null,
    schemaValid: num(cells("Schema-valid output lines")[2]),
    urlLeaks: num(cells("Absolute URL leaks")[2]),
    stepAccuracy: num(cells("Step accuracy")[2]),
    judgedPlans: num(match(/judge.*? over (\d+) plans/)),
    relevance: num(cells("Deeplink relevance")[2]),
    resolver: {
      n: num(match(/Precision@1 [\d.]+% on the (\d+) steps/)),
      top1: num(match(/P@1 ([\d.]+)%/, ours)),
      bm25Top1: num(match(/P@1 ([\d.]+)%/, bm25)),
      wrongLink: num(match(/wrong or unsafe link on ([\d.]+)%/, ours)),
      llmTop1: num(match(/P@1 ([\d.]+)%/, llm[4] ?? "")),
      llmP95: num(match(/^([\d.]+) ms/, llm[2] ?? "")),
    },
    latency: {
      exactP95: num(exact[3]),
      paraphraseP95: num(para[3]),
      coldP50: num(cold[2]),
      coldP95: num(cold[3]),
    },
    cache: {
      threshold: Number.isFinite(threshold) ? threshold : 0.7,
      paraphraseHit: num(cells("Semantic cache hit rate")[2]),
      falseHit: nearMiss ? num(nearMiss[2]) : null,
      nearMisses: nearMiss ? num(nearMiss[1]) : null,
    },
  };
}

function readCatalog(): CatalogEntry[] {
  return json<{ deeplinks: CatalogEntry[] }>("data/kit/deeplinks.json")?.deeplinks ?? [];
}

function readProof(catalog: CatalogEntry[]): Proof {
  const gold = jsonl("data/gold/deeplink_gold.jsonl");
  const tier = (t: string) => gold.filter((g) => g.tier === t).length;
  const owners: Record<string, number> = {};
  for (const g of gold) owners[String(g.owner)] = (owners[String(g.owner)] ?? 0) + 1;

  const nearMiss = jsonl("eval/sets/near_miss.jsonl");

  return {
    sets: {
      paraphrases: jsonl("eval/sets/paraphrases.jsonl").length,
      nearMiss: nearMiss.length,
      unseen: jsonl("eval/sets/unseen.jsonl").length,
      adversarial: jsonl("eval/sets/adversarial.jsonl").length,
    },
    gold: {
      labelled: gold.length,
      target: 100,
      catalog: tier("catalog"),
      dummy: tier("dummy"),
      manual: tier("manual"),
      owners,
    },
    catalog: {
      entries: catalog.length,
      // Only enable toggles carry a full validation object (key, condition, value).
      verifiable: catalog.filter((d) => d.originalType === "onURL").length,
    },
    // `python eval/sets/validate_sets.py` — "mean overlap with the row query".
    overlap: { paraphrase: 0.3, nearMiss: 0.49 },
    metrics: readMetrics(),
    // The touch-lag row's near misses: the same article, a different problem.
    nearMisses: nearMiss
      .filter((n) => n.row_id === "row_21")
      .map((n) => ({
        query: n.query as string,
        differsIn: n.differs_in as string,
        slots: n.expected_slots as Record<string, string | null>,
      })),
  };
}

interface Fixture {
  request: { query: string; siis_response: { title?: string } | null };
  plan: { contexts: { title: string; score: number; actions: unknown[] }[] };
  stream: { stage: string; detail?: Record<string, unknown> }[];
}

function fixture(dir: string): Fixture {
  const base = `data/fixtures/${dir}`;
  return {
    request: json(`${base}/request.json`)!,
    plan: json(`${base}/plan.json`)!,
    stream: json(`${base}/stream.json`)!,
  };
}

function readMultiIntent(): MultiIntent {
  const f = fixture("touch_multi_intent");
  const titles = f.plan.contexts.map((c) => c.title);
  const compile = f.stream.find((e) => e.stage === "compile")?.detail ?? {};
  const deduped = (compile.deduped as { action: string; kept_in_intent: number; removed_from_intents: number[] }[]) ?? [];
  return {
    query: f.request.query,
    goals: f.plan.contexts.map((c) => ({ title: c.title, score: c.score, actions: c.actions.length })),
    deduped: deduped.map((d) => ({
      action: d.action,
      keptIn: titles[d.kept_in_intent] ?? "",
      removedFrom: d.removed_from_intents.map((i) => titles[i] ?? ""),
    })),
  };
}

/**
 * The live section's presets. Every one is a real request: the recorded fixtures, or a row from
 * the held-out eval sets, which the engine is never tuned on.
 */
function readPresets(): Preset[] {
  const touch = fixture("touch_lag").request;
  const multi = fixture("touch_multi_intent").request;
  const email = fixture("email_not_responding").request;
  const titleOf = (siis: unknown) =>
    siis && typeof siis === "object" && "title" in siis ? String((siis as { title: string }).title) : "no article";
  const row = (set: string, id: string) => jsonl(`eval/sets/${set}.jsonl`).find((r) => r.id === id);
  const firstOf = (set: string, key: string, value: string) =>
    jsonl(`eval/sets/${set}.jsonl`).find((r) => r[key] === value);

  const reworded = row("paraphrases", "para_21_02");
  const nearMiss = row("near_miss", "nm_21_1");
  const unseen = firstOf("unseen", "domain", "Battery");
  const injection = row("adversarial", "adv_08");
  const offTopic = row("adversarial", "adv_12");

  const make = (
    id: string,
    label: string,
    hint: string,
    req: { query?: unknown; siis_response?: unknown } | undefined,
    siis: unknown = req?.siis_response,
    mock?: Preset["mock"],
  ): Preset | null =>
    req ? { id, label, hint, query: String(req.query), siis, articleTitle: titleOf(siis), mock } : null;

  const presets = [
    make("cold", "The complaint", "full cold run", touch),
    make("exact", "Ask it again", "exact cache hit", touch, touch.siis_response, "exact"),
    make("semantic", "Reworded", "held-out paraphrase", reworded, touch.siis_response, "semantic"),
    make("near", "Cracked screen", "near miss, must not reuse", nearMiss, touch.siis_response),
    make("multi", "Two problems", "multi-intent", multi),
    make("email", "Mismatched article", "all three link tiers", email),
    make("unseen", "Battery drain", "unseen domain", unseen),
    make("inject", "Prompt injection", "instructions inside the article", injection),
    make("offtopic", "Wrong article", "must return no_match", offTopic),
  ];
  return presets.filter((p): p is Preset => Boolean(p));
}

export function readStoryInputs(): StoryInputs {
  const catalog = readCatalog();
  return {
    prompts: { variations: latestPrompt("variations"), extract: latestPrompt("extract") },
    llm: readLlmConfig(),
    proof: readProof(catalog),
    catalog,
    multiIntent: readMultiIntent(),
    presets: readPresets(),
  };
}
