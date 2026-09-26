/**
 * Everything the story page at `/` shows, derived from the shipped fixtures.
 *
 * Nothing here is typed in by hand: the complaint, the model's output, the article sentences,
 * the grounding verdicts, the resolver's candidates and the final plan all come from
 * data/fixtures/touch_lag/, which api/tests/test_fixtures.py guards. The fixture README marks
 * stage latencies, token counts, grounding scores and the dropped step as illustrative; the
 * page says so wherever one of them appears.
 *
 * Built on the server and handed to the client as plain JSON, so only what the page renders
 * reaches the browser: the article arrives as the segment stage's numbered sentences, never as
 * the raw request body.
 */

import cacheJson from "@fixtures/cache_events.json";
import planJson from "@fixtures/plan.json";
import requestJson from "@fixtures/request.json";
import streamJson from "@fixtures/stream.json";
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
    dropped: { action: string; text: string; src: string[]; score: number; reason: string };
  };
  resolve: {
    counts: { catalog: number; dummy: number; manual: number };
    featured: {
      action: string;
      step: string;
      path: string;
      verb: string;
      entryId: string;
      candidates: { path: string; score: number }[];
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
  const enrich = detail<{
    canonical_query: string;
    intents: { title: string; domain: string }[];
    variations: string[];
    dropped_variations: { text: string; reason: string; jaccard: number }[];
    model: string;
    tokens_in: number;
    tokens_out: number;
  }>("enrich");
  const segment = detail<{
    sections: { id: string; heading: string; relevant: boolean; relevance: number[] }[];
    sentences: { id: string; section: string; text: string }[];
    relevance_floor: number;
  }>("segment");
  const extract = detail<{
    actions: RawAction[];
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
  }>("ground");
  const resolve = detail<{
    counts: { catalog: number; dummy: number; manual: number };
    links: {
      action: string;
      screen_path: string | null;
      intent_verb: string | null;
      tier: string;
      entry_id: string | null;
      candidates: { path: string; score: number }[];
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

  const dropped = ground.dropped_steps[0];
  const inputs = compile.score_inputs[0];

  return {
    query: request.query,
    slots: cache.slots,
    articleTitle: cache.siis_title,
    cache: { ms: msOf("cache"), threshold: cache.threshold },
    enrich: {
      ms: msOf("enrich"),
      model: enrich.model,
      tokensIn: enrich.tokens_in,
      tokensOut: enrich.tokens_out,
      canonical: enrich.canonical_query,
      intent: { title: enrich.intents[0].title, domain: enrich.intents[0].domain },
      kept: enrich.variations,
      dropped: enrich.dropped_variations,
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
      dropped: {
        action: dropped.action,
        text: dropped.text,
        src: dropped.src_ids,
        score: dropped.grounding_score,
        reason: dropped.reason,
      },
    },
    resolve: {
      counts: resolve.counts,
      featured: {
        action: link.action,
        step: switchStep.text,
        path: link.screen_path ?? "",
        verb: link.intent_verb ?? "",
        entryId: link.entry_id ?? "",
        candidates: link.candidates,
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
