"use client";

import { useEffect, useRef, useState } from "react";
import { Galaxy } from "@/components/story/Galaxy";
import { Mark } from "@/components/story/Logo";
import { gsap, useGSAP } from "@/lib/gsap";
import type { PlanContext } from "@/lib/plan";
import type { Preset, StoryData } from "@/lib/story";
import { API_URL, checkHealth, type Health, streamTroubleshoot } from "@/lib/stream";
import { LLM_STAGES, type StageEvent } from "@/lib/trace";

/**
 * Try it live: the one part of the page that is not a recording.
 *
 * It streams POST /v1/troubleshoot/stream and renders every stage frame as it arrives, then puts
 * the real answer on the phone. The badge names the model that answered (`meta.model`: a Ministral
 * model, or `rules` when the engine ran without an LLM) or the cache tier that did. If the API is
 * switched to its mock replay (`settings.stream_mock`), every frame carries `detail.mock` and the
 * section says so rather than passing a replay off as a live run.
 */

type Frame = StageEvent<Record<string, unknown>>;
type Status = "idle" | "running" | "done" | "offline";

// A hosted API may be asleep or still loading its indexes: /health wakes it and says when it can
// answer. Poll until it is ready, then stop; give up after a few minutes of nothing.
const HEALTH_POLL_MS = 4000;
const HEALTH_GIVE_UP_MS = 180_000;
const LOCAL_API = /^https?:\/\/(127\.0\.0\.1|localhost|\[::1\])(:|\/|$)/.test(API_URL);

const fmt = (ms: number) => (ms >= 100 ? Math.round(ms).toLocaleString("en-US") : ms.toFixed(1));

const str = (v: unknown) => (typeof v === "string" && v.trim() ? v : null);
const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : null);

/** The one line under a stage worth reading live: what the cache matched, how the model worked. */
function stageNote(f: Frame): string | null {
  const d = f.detail ?? {};
  if (f.stage === "cache" && d.hit === true) {
    const matched = str(d.matched_query);
    const sim = num(d.similarity);
    const thr = num(d.threshold);
    if (d.tier === "exact" || !matched) return matched ? `same request as “${matched}”` : null;
    return `matched “${matched}”` + (sim !== null && thr !== null ? ` · similarity ${sim.toFixed(2)} ≥ ${thr.toFixed(2)}` : "");
  }
  if (f.stage === "enrich" && d.variations_pending === true) {
    return "8–10 rewordings are being written in the background; the answer does not wait for them";
  }
  if (f.stage === "extract") {
    const model = str(d.model);
    const intents = Array.isArray(d.intents)
      ? d.intents.map((i) => str((i as { title?: unknown })?.title)).filter(Boolean)
      : [];
    const parts = [
      d.mode === "select" ? "picked article sentences by id, wrote none" : null,
      model,
      intents.length ? `${intents.length === 1 ? "problem" : "problems"}: ${intents.join(" · ")}` : null,
      d.source === "rules" ? "no model answered: the article's own instructions" : null,
    ];
    return parts.filter(Boolean).join(" · ") || null;
  }
  return null;
}

export function Live({ data }: { data: StoryData }) {
  const root = useRef<HTMLElement>(null);
  const abort = useRef<AbortController | null>(null);
  const [preset, setPreset] = useState<Preset>(data.presets[0]);
  const [query, setQuery] = useState(data.presets[0].query);
  const [frames, setFrames] = useState<Frame[]>([]);
  const [status, setStatus] = useState<Status>("idle");
  const [health, setHealth] = useState<Health>("unknown");

  useEffect(() => {
    const ctrl = new AbortController();
    const started = Date.now();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      const h = await checkHealth(ctrl.signal);
      if (ctrl.signal.aborted) return;
      setHealth(h);
      if (h !== "ready" && Date.now() - started < HEALTH_GIVE_UP_MS) timer = setTimeout(poll, HEALTH_POLL_MS);
    };
    void poll();
    return () => {
      ctrl.abort();
      clearTimeout(timer);
    };
  }, []);

  const run = async (p: Preset, q: string) => {
    abort.current?.abort();
    const ctrl = new AbortController();
    abort.current = ctrl;
    setFrames([]);
    setStatus("running");
    try {
      await streamTroubleshoot(
        { query: q, siis_response: p.siis },
        (ev) => setFrames((f) => [...f, ev]),
        // A mock hint only when the words are still the preset's own; a typed query stands alone.
        { mock: q === p.query ? p.mock : undefined, signal: ctrl.signal },
      );
      if (!ctrl.signal.aborted) {
        setStatus("done");
        setHealth("ready");
      }
    } catch {
      if (!ctrl.signal.aborted) setStatus("offline");
    }
  };

  const pick = (p: Preset) => {
    setPreset(p);
    setQuery(p.query);
    void run(p, p.query);
  };

  useGSAP(
    () => {
      const mm = gsap.matchMedia();
      mm.add("(prefers-reduced-motion: no-preference)", () => {
        const $ = gsap.utils.selector(root);
        gsap
          .timeline({
            defaults: { ease: "expo.out" },
            scrollTrigger: { trigger: root.current, start: "top 70%" },
          })
          .from($(".lv-card"), { y: 120, scale: 0.96, autoAlpha: 0, duration: 1.3 })
          .from($(".lv-left > *"), { y: 40, autoAlpha: 0, stagger: 0.08, duration: 1 }, 0.3)
          .from($(".lv-phone"), { y: 160, rotate: 6, autoAlpha: 0, duration: 1.4 }, 0.35);
      });
    },
    { scope: root },
  );

  const done = frames.find((f) => f.stage === "done");
  const mock = frames.some((f) => f.detail?.mock === true);
  const contexts = (done?.detail?.contexts as PlanContext[] | undefined) ?? [];
  const meta =
    (done?.detail?.meta as {
      fallback?: string | null;
      trace_id?: string;
      latency_ms?: number;
      model?: string | null;
      cache_tier?: string | null;
    }) ?? {};
  const noScenario = mock && meta.trace_id === "t_mock_none";
  const stages = frames.filter((f) => f.stage !== "done");

  const badge =
    status === "offline"
      ? { cls: "off", text: "Engine offline" }
      : mock
        ? { cls: "mock", text: "Mock replay of a recorded run" }
        : status === "done"
          ? { cls: "live", text: `Live engine · ${answeredBy(meta)}` }
          : health === "ready"
            ? { cls: "ready", text: `Engine ready · ${API_URL.replace(/^https?:\/\//, "")}` }
            : health === "starting"
              ? { cls: "mock", text: "Engine waking up…" }
              : health === "offline"
                ? { cls: "off", text: "Engine offline" }
                : { cls: "idle", text: API_URL.replace(/^https?:\/\//, "") };

  return (
    <section className="lv" id="live" data-nav="light" ref={root}>
      <div className="lv-card">
        <div className="lv-left">
          <p className="st-eyebrow">08 · Try it live</p>
          <h2 className="st-h2">Ask it anything.</h2>
          <p className="st-lead lv-lead">Everything above was one recorded run. This part calls the engine.</p>

          <div className="lv-box">
            <textarea
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              rows={3}
              aria-label="Describe the problem"
              placeholder="Describe what is wrong with the phone"
            />
            <div className="lv-box-foot">
              <span className="lv-article">
                Article: <b>{preset.articleTitle}</b>
              </span>
              <button className="pill pill-lime lv-run" onClick={() => void run(preset, query)} disabled={!query.trim()}>
                {status === "running" ? "Running…" : "Find the fix"}
              </button>
            </div>
          </div>

          <div className="lv-chips" role="list">
            {data.presets.map((p) => (
              <button
                key={p.id}
                role="listitem"
                className={`lv-chip${p.id === preset.id ? " on" : ""}`}
                onClick={() => pick(p)}
              >
                <b>{p.label}</b>
                <small>{p.hint}</small>
              </button>
            ))}
          </div>

          <div className="lv-trace">
            <div className="lv-trace-head">
              <span className={`lv-badge ${badge.cls}`}>
                <i /> {badge.text}
              </span>
              {done && <b className="lv-total">{fmt(done.ms)} ms</b>}
            </div>
            <ol className="lv-stages">
              {stages.map((f, i) => {
                const hit = f.stage === "cache" && f.detail?.hit === true;
                const note = stageNote(f);
                return (
                  <li
                    key={`${f.stage}-${i}`}
                    className={`lv-stage${usedModel(f) ? " is-llm" : ""}${hit ? " hit" : ""}`}
                  >
                    <span className="lv-dot" />
                    <b>{f.stage}</b>
                    <span className="lv-sum">{f.summary}</span>
                    <small>{fmt(f.ms)} ms</small>
                    {note && <span className="lv-sub">{note}</span>}
                  </li>
                );
              })}
              {status === "idle" && health !== "offline" && (
                <li className="lv-empty">
                  {health === "starting"
                    ? "The engine is loading its indexes. It answers in a moment."
                    : "Pick a complaint or write your own."}
                </li>
              )}
              {(status === "offline" || (status === "idle" && health === "offline")) && (
                <li className="lv-empty">
                  No engine at <code>{API_URL}</code>.{" "}
                  {LOCAL_API ? (
                    <>
                      Start it with <code>docker compose up</code> from the repo root, or{" "}
                      <code>uvicorn app.main:app</code> from <code>api/</code>.
                    </>
                  ) : (
                    "It may be asleep; this page keeps knocking and will light up when it answers."
                  )}
                </li>
              )}
            </ol>
          </div>
        </div>

        <div className="lv-phone">
          <Galaxy>
            <LiveScreen status={status} contexts={contexts} fallback={meta.fallback ?? null} noScenario={noScenario} />
          </Galaxy>
        </div>
      </div>
      <p className="st-fine lv-fine">
        Presets are real requests: the recorded fixtures and rows from the held-out eval sets, which the engine is
        never tuned on.
      </p>
    </section>
  );
}

/** Pink only when a model really ran: on the free tier enrich is rules-only on the answer's path. */
const usedModel = (f: Frame) => LLM_STAGES.has(f.stage) && Boolean(str(f.detail?.model));

/** Who produced the answer: the cache tier on a hit, else the model (or the no-LLM rules path). */
function answeredBy(meta: { model?: string | null; cache_tier?: string | null }): string {
  if (meta.cache_tier) return `${meta.cache_tier} cache hit, no model call`;
  if (!meta.model) return "answered";
  return meta.model === "rules" ? "rules only, no model" : meta.model;
}

function LiveScreen({
  status,
  contexts,
  fallback,
  noScenario,
}: {
  status: Status;
  contexts: PlanContext[];
  fallback: string | null;
  noScenario: boolean;
}) {
  if (status === "done" && contexts.length > 0) {
    return (
      <div className="sc sc-live">
        <div className="sc-app">
          <Mark className="sc-mark" />
          OneClick
        </div>
        {contexts.map((c) => (
          <div className="lv-goal" key={c.title}>
            <span className="plan-kicker">Your fix · {c.score.toFixed(2)}</span>
            <h3 className="sc-large">{c.title}</h3>
            {c.actions.map((a) => {
              const link = a.stepGroups[0]?.actionableDeeplink;
              return (
                <div className="plan-card" key={a.actionName}>
                  <div className="plan-card-top">
                    <span className="plan-card-name">{a.actionName}</span>
                    <span className={`sc-chip sc-chip-${a.category}`}>{a.category}</span>
                  </div>
                  <p className="plan-card-desc">{a.description}</p>
                  <ol className="plan-steps">
                    {a.stepGroups.flatMap((g) => g.steps).map((s) => (
                      <li key={s}>{s}</li>
                    ))}
                  </ol>
                  {link && <span className="plan-open">{link.message}</span>}
                </div>
              );
            })}
          </div>
        ))}
      </div>
    );
  }

  const [title, text] =
    status === "running"
      ? ["Finding a fix…", "Reading the article and checking every step against it."]
      : status === "offline"
        ? ["Engine offline", "The page is fine; the engine is not running."]
        : status === "done" && noScenario
          ? ["No recorded run", "The mock only replays recorded runs. The live engine answers this one."]
          : status === "done"
            ? [
                fallback === "no_siis_context" ? "No article" : "No grounded fix",
                "Nothing in the article supports a fix, so OneClick returns no steps rather than invent them." +
                  (fallback ? ` (${fallback})` : ""),
              ]
            : ["What's wrong?", "Pick a complaint on the left."];

  return (
    <div className="sc sc-live sc-live-empty">
      <span className={`lv-orb${status === "running" ? " busy" : ""}`}>
        <Mark />
      </span>
      <h3>{title}</h3>
      <p>{text}</p>
    </div>
  );
}
