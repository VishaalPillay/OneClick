/**
 * Everything the story page at `/` shows, derived from one recorded run of the real engine.
 *
 * Nothing here is typed in by hand: the complaint, the model's answer, the article sentences, the
 * grounding verdicts, the resolver's candidates, the final plan, both cache hits and the background
 * variations call all come from console/recordings/touch_lag/, written by
 * `python eval/tools/record_story.py` (the shipping pipeline, in-process, real keys). Timings and
 * token counts are that run's own. data/fixtures/ stays what it is: the engine tests' contract.
 *
 * Built on the server and handed to the client as plain JSON, so only what the page renders
 * reaches the browser: the article arrives as the segment stage's numbered sentences, never as
 * the raw request body.
 */

import aboutJson from "@/recordings/touch_lag/about.json";
import cacheJson from "@/recordings/touch_lag/cache_events.json";
import planJson from "@/recordings/touch_lag/plan.json";
import requestJson from "@/recordings/touch_lag/request.json";
import streamJson from "@/recordings/touch_lag/stream.json";
import variationsJson from "@/recordings/touch_lag/variations.json";
import { checkBody, type CatalogEntry, type Check } from "@/lib/checks";
import type { PlanContext, ResponseBody } from "@/lib/plan";
import type { StageEvent, StageName } from "@/lib/trace";

export interface PromptFile {
  /** e.g. `extract.v1.md` — the highest version on disk. */
  file: string;
  /** The instructions, or null while the file is still a TODO stub. */
  body: string | null;
}

/**
 * docs/metrics.md, as numbers. Percentages are 0-100 as the report prints them. The cache
 * threshold is the engine's own `cache_sim_threshold` from api/app/config.py.
 */
export interface Metrics {
  commit: string | null;
  date: string | null;
  schemaValid: number | null;
  urlLeaks: number | null;
  stepAccuracy: number | null;
  judgedPlans: number | null;
  relevance: number | null;
  resolver: {
    n: number | null;
    top1: number | null;
    bm25Top1: number | null;
    wrongLink: number | null;
    llmTop1: number | null; // the whole catalog handed to an LLM, per step
    llmP95: number | null;
  };
  latency: { exactP95: number | null; paraphraseP95: number | null; coldP50: number | null; coldP95: number | null };
  cache: { threshold: number; paraphraseHit: number | null; falseHit: number | null; nearMisses: number | null };
}

export interface Proof {
  sets: { paraphrases: number; nearMiss: number; unseen: number; adversarial: number };
  gold: {
    labelled: number;
    target: number;
    catalog: number;
    dummy: number;
    manual: number;
    owners: Record<string, number>;
  };
  catalog: { entries: number; verifiable: number };
  /** Mean token overlap with the kit query, from `python eval/sets/validate_sets.py`. */
  overlap: { paraphrase: number; nearMiss: number };
  /** The measured numbers, read from docs/metrics.md at build time; null = not measured there. */
  metrics: Metrics;
  nearMisses: { query: string; differsIn: string; slots: Record<string, string | null> }[];
}

/** A preset for the live section: a real request from the fixtures or the held-out sets. */
export interface Preset {
  id: string;
  label: string;
  hint: string;
  query: string;
  siis: unknown;
  articleTitle: string;
  mock?: "exact" | "semantic";
}

export interface MultiIntent {
  query: string;
  goals: { title: string; score: number; actions: number }[];
  deduped: { action: string; keptIn: string; removedFrom: string[] }[];
}

/** The engine's model settings, read from api/app/config.py at build time. */
export interface LlmConfig {
  model: string; // call B (extract), raced against fastModel
  fastModel: string;
  variationsModel: string; // the background call
  temperature: number; // Mistral models
  fallback: string; // last resort for every call
  fallbackTemperature: number;
  preferDeadline: number; // seconds the 14B answer is waited for
  budget: number; // the extract stage gives up here
}

export interface StoryInputs {
  prompts: { variations: PromptFile; extract: PromptFile };
  llm: LlmConfig;
  proof: Proof;
  catalog: CatalogEntry[];
  multiIntent: MultiIntent;
  presets: Preset[];
}

export interface StoryStep {
  text: string;
  src: string[];
  score: number;
}

export interface StoryAction {
  name: string;
  category: string;
  steps: StoryStep[];
}

export interface StoryData {
  query: string;
  slots: Record<string, string | null>;
  articleTitle: string;
  cache: { ms: number; threshold: number };
  enrich: {
    ms: number;
    model: string;
    tokensIn: number;
    tokensOut: number;
    canonical: string;
    intent: { title: string; domain: string };
    kept: string[];
    dropped: { text: string; reason: string; jaccard: number }[];
  };
  article: {
    sections: { id: string; heading: string; relevant: boolean; relevance: number }[];
    sentences: { id: string; section: string; text: string }[];
    floor: number;
  };
  extract: {
    ms: number;
    model: string;
    tokensIn: number;
    tokensOut: number;
    actions: StoryAction[];
    proposed: number;
  };
  ground: {
    threshold: number;
    kept: number;
    proposed: number;
    coverage: number;
    /** The first step grounding threw out, if the run had one (select mode rarely does). */
    dropped: { action: string; text: string; src: string[]; score: number; reason: string } | null;
    /** The kept step that cleared the bar by the least. */
    lowest: { action: string; text: string; src: string[]; score: number };
  };
  resolve: {
    counts: { catalog: number; dummy: number; manual: number };
    featured: {
      action: string;
      step: string;
      path: string;
      verb: string;
      entryId: string;
      candidates: { path: string; id: string; score: number }[];
      uri: string;
      message: string;
      originalType: string;
      validation: { key: string; condition?: string; value?: string };
    };
  };
  score: { value: number; relevance: number; grounding: number; links: number };
  plan: PlanContext;
  timing: {
    stages: { stage: StageName; ms: number }[];
    total: number;
    exact: number;
    semantic: number;
  };
  semanticHit: { query: string; matched: string; similarity: number; threshold: number };
  /** When and how the run was recorded (eval/tools/record_story.py). */
  recording: { at: string; commit: string | null; coldTries: number; model: string };
  prompts: { variations: PromptFile; extract: PromptFile };
  llm: LlmConfig;
  proof: Proof;
  compile: {
    body: ResponseBody;
    checks: Check[];
    schemaValid: boolean;
    repairs: number;
    droppedActions: number;
    formula: string;
  };
  multiIntent: MultiIntent;
  presets: Preset[];
}

const events = streamJson as unknown as StageEvent<Record<string, unknown>>[];
const plan = planJson as unknown as { contexts: PlanContext[]; meta: { latency_ms: number } };
const request = requestJson as unknown as { query: string };

const detail = <T,>(stage: StageName) => (events.find((e) => e.stage === stage)?.detail ?? {}) as T;
const msOf = (stage: StageName) => events.find((e) => e.stage === stage)?.ms ?? 0;

interface RawStep {
  text: string;
  src_ids: string[];
  grounding_score: number;
}
interface RawAction {
  name: string;
  category: string;
  steps: RawStep[];
}

const toAction = (a: RawAction): StoryAction => ({
  name: a.name,
  category: a.category,
  steps: a.steps.map((s) => ({ text: s.text, src: s.src_ids, score: s.grounding_score })),
});

export function buildStory({ prompts, llm, proof, catalog, multiIntent, presets }: StoryInputs): StoryData {
  const cache = detail<{
    slots: Record<string, string | null>;
    threshold: number;
    siis_title: string;
  }>("cache");
  const enrich = detail<{ canonical_query: string }>("enrich");
  // The variations call runs in the background (it never delays the answer), so its result is not a
  // stage frame: the recorder waited for it and kept what it returned.
  const variations = variationsJson as unknown as {
    kept: string[];
    dropped: { text: string; reason: string; jaccard: number }[];
    model: string;
    tokens_in: number;
    tokens_out: number;
  };
  const segment = detail<{
    sections: { id: string; heading: string; relevant: boolean; relevance: number[] }[];
    sentences: { id: string; section: string; text: string }[];
    relevance_floor: number;
  }>("segment");
  const extract = detail<{
    actions: RawAction[];
    intents: { title: string; domain: string }[];
    model: string;
    tokens_in: number;
    tokens_out: number;
  }>("extract");
  const ground = detail<{
    threshold: number;
    kept_steps: number;
    proposed_steps: number;
    coverage: number;
    dropped_steps: { action: string; text: string; src_ids: string[]; grounding_score: number; reason: string }[];
    actions: RawAction[];
  }>("ground");
  const resolve = detail<{
    counts: { catalog: number; dummy: number; manual: number };
    links: {
      action: string;
      screen_path: string | null;
      intent_verb: string | null;
      tier: string;
      entry_id: string | null;
      candidates: { entry_id: string; score: number }[];
    }[];
  }>("resolve");
  const compile = detail<{
    score_inputs: { score: number; relevance: number; grounding_coverage: number; link_coverage: number }[];
    score_formula: string;
    schema_valid: boolean;
    repairs: number;
    dropped_actions: number;
  }>("compile");
  const hits = cacheJson as unknown as Record<
    "exact" | "semantic",
    {
      event: { detail: { query: string; matched_query: string; similarity: number; threshold: number } };
      meta: { latency_ms: number };
    }
  >;

  const ctx = plan.contexts[0];

  // The resolver's showcase is the first catalog link: a real enable toggle whose catalog entry
  // carries a full validation object, so it is the one the phone can show as verified.
  const link = resolve.links.find((l) => l.tier === "catalog")!;
  const planAction = ctx.actions.find((a) => a.actionName === link.action)!;
  const group = planAction.stepGroups[0];
  const extracted = extract.actions.find((a) => a.name === link.action)!;
  const switchStep = extracted.steps.find((s) => /switch/i.test(s.text)) ?? extracted.steps[0];

  // Extraction proposes steps ungraded; the ground stage scores them. Each proposed step takes the
  // score grounding gave it (a dropped step keeps its own, from dropped_steps).
  const graded = new Map([
    ...ground.actions.flatMap((a) => a.steps.map((st) => [`${a.name}|${st.text}`, st.grounding_score] as const)),
    ...ground.dropped_steps.map((d) => [`${d.action}|${d.text}`, d.grounding_score] as const),
  ]);
  for (const a of extract.actions) {
    for (const st of a.steps) st.grounding_score = graded.get(`${a.name}|${st.text}`) ?? st.grounding_score;
  }
  const dropped = ground.dropped_steps[0];
  const kept = extract.actions.flatMap((a) => a.steps.map((st) => ({ action: a.name, ...st })));
  const lowest = kept.reduce((low, st) => (st.grounding_score < low.grounding_score ? st : low), kept[0]);
  const inputs = compile.score_inputs[0];
  // A candidate is a catalog entry; its own message names the screen ("Enable Touch sensitivity").
  const screenName = (id: string) =>
    (catalog.find((e) => e.id === id)?.message ?? id).replace(/^(Enable|Disable|View|Adjust|Open)\s+/, "");
  const about = aboutJson as unknown as { recorded_at: string; commit: string | null; cold_tries: number };

  return {
    query: request.query,
    slots: cache.slots,
    articleTitle: cache.siis_title,
    cache: { ms: msOf("cache"), threshold: cache.threshold },
    enrich: {
      ms: msOf("enrich"),
      model: variations.model,
      tokensIn: variations.tokens_in,
      tokensOut: variations.tokens_out,
      canonical: enrich.canonical_query,
      intent: { title: extract.intents[0].title, domain: extract.intents[0].domain },
      kept: variations.kept,
      dropped: variations.dropped,
    },
    article: {
      sections: segment.sections.map((s) => ({
        id: s.id,
        heading: s.heading,
        relevant: s.relevant,
        relevance: s.relevance[0] ?? 0,
      })),
      sentences: segment.sentences.map((s) => ({ id: s.id, section: s.section, text: s.text })),
      floor: segment.relevance_floor,
    },
    extract: {
      ms: msOf("extract"),
      model: extract.model,
      tokensIn: extract.tokens_in,
      tokensOut: extract.tokens_out,
      actions: extract.actions.map(toAction),
      proposed: extract.actions.reduce((n, a) => n + a.steps.length, 0),
    },
    ground: {
      threshold: ground.threshold,
      kept: ground.kept_steps,
      proposed: ground.proposed_steps,
      coverage: ground.coverage,
      dropped: dropped
        ? {
            action: dropped.action,
            text: dropped.text,
            src: dropped.src_ids,
            score: dropped.grounding_score,
            reason: dropped.reason,
          }
        : null,
      lowest: { action: lowest.action, text: lowest.text, src: lowest.src_ids, score: lowest.grounding_score },
    },
    resolve: {
      counts: resolve.counts,
      featured: {
        action: link.action,
        step: switchStep.text,
        path: link.screen_path ?? "",
        verb: link.intent_verb ?? "",
        entryId: link.entry_id ?? "",
        candidates: link.candidates.map((c) => ({ path: screenName(c.entry_id), id: c.entry_id, score: c.score })),
        uri: group.actionableDeeplink?.deeplink ?? "",
        message: group.actionableDeeplink?.message ?? "",
        originalType: group.actionableDeeplink?.originalType ?? "",
        validation: group.validationDeeplink as unknown as StoryData["resolve"]["featured"]["validation"],
      },
    },
    score: {
      value: inputs.score,
      relevance: inputs.relevance,
      grounding: inputs.grounding_coverage,
      links: inputs.link_coverage,
    },
    plan: ctx,
    timing: {
      stages: events.filter((e) => e.stage !== "done").map((e) => ({ stage: e.stage, ms: e.ms })),
      total: plan.meta.latency_ms,
      exact: hits.exact.meta.latency_ms,
      semantic: hits.semantic.meta.latency_ms,
    },
    semanticHit: {
      query: hits.semantic.event.detail.query,
      matched: hits.semantic.event.detail.matched_query,
      similarity: hits.semantic.event.detail.similarity,
      threshold: hits.semantic.event.detail.threshold,
    },
    recording: {
      at: about.recorded_at,
      commit: about.commit,
      coldTries: about.cold_tries,
      model: extract.model,
    },
    prompts,
    llm,
    proof,
    compile: {
      body: { contexts: plan.contexts, meta: plan.meta as unknown as Record<string, unknown> },
      checks: checkBody({ contexts: plan.contexts }, catalog),
      schemaValid: compile.schema_valid,
      repairs: compile.repairs,
      droppedActions: compile.dropped_actions,
      formula: compile.score_formula,
    },
    multiIntent,
    presets,
  };
}
