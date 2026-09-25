"use client";

import { Fragment, useRef } from "react";
import { MEDIA, gsap, useGSAP } from "@/lib/gsap";
import type { PromptFile, StoryData } from "@/lib/story";

/**
 * Inside the model: the two LLM calls, prompt on the left and response on the right.
 *
 * Call A (variations) rewrites the complaint twelve ways for the semantic cache. It runs in the
 * background next to extraction, so it never delays an answer, and our own filter throws some of
 * the twelve away. Call B (extract, select mode) reads the article as numbered sentences and
 * answers with the ids of the sentences that are each action's steps; the engine then uses those
 * sentences word for word. As each action streams in, the sentences it cites light up on the left.
 *
 * The instruction text is read from api/app/llm/prompts/ at build time and the model names and
 * temperatures from api/app/config.py, so this shows exactly what the engine sends.
 */

// The shapes the two calls must return: the schemas in api/app/pipeline/enrich.py and extract.py.
const VARIATIONS_RETURN = `{ "variations": [str, ...] }   // 12 candidates`;

const EXTRACT_RETURN = `{ "goals": [{ "problem", "title", "topic", "domain",
             "actions": [{ "src_ids": ["S…"], "name", "description",
                           "screen_path", "intent_verb" }] }] }`;

const REASON: Record<string, (j: number, cap: number) => string> = {
  token_jaccard_vs_original: (j) => `filtered · too close to the original (overlap ${j.toFixed(2)})`,
  over_limit: (_, cap) => `filtered · over the cap of ${cap}`,
};

function Instructions({ prompt }: { prompt: PromptFile }) {
  return (
    <div className="blk blk-sys">
      <span className="blk-label">
        instructions · {prompt.file}
        {!prompt.body && <em className="blk-flag">not written yet</em>}
      </span>
      {prompt.body ? (
        <pre className="blk-pre">{prompt.body}</pre>
      ) : (
        <p className="blk-pending">Shown here word for word once it lands. Everything below is the real input.</p>
      )}
    </div>
  );
}

const q = (s: string) => <span className="t-str">&quot;{s}&quot;</span>;
const k = (s: string) => <span className="t-key">&quot;{s}&quot;</span>;
const p = (s: string) => <span className="t-pun">{s}</span>;

export function Prompt({ data }: { data: StoryData }) {
  const root = useRef<HTMLElement>(null);
  const { enrich, extract, article, llm } = data;
  // Select mode: the model answers with sentence ids per action, not with step text.
  const ids = (a: (typeof extract.actions)[number]) => [...new Set(a.steps.flatMap((s) => s.src))];
  const shown = extract.actions.slice(0, 6);
  const more = extract.actions.slice(6);
  const cited = new Set(extract.actions.flatMap(ids)).size;

  // Sentences grouped under their section headings, in article order.
  const sections = article.sections.map((s) => ({
    ...s,
    sentences: article.sentences.filter((x) => x.section === s.heading),
  }));

  useGSAP(
    () => {
      const mm = gsap.matchMedia();
      mm.add(MEDIA, (ctx) => {
        const wide = Boolean(ctx.conditions?.wide);
        const $ = gsap.utils.selector(root);
        const respBody = $(".win-resp .win-body")[0] as HTMLElement;
        const clip = $(".art-clip")[0] as HTMLElement;
        const list = $(".art-list")[0] as HTMLElement;

        // Keep the newest response line in view, like a terminal that follows its output.
        const follow = (line: HTMLElement) => () =>
          -Math.max(0, line.offsetTop + line.offsetHeight + 28 - respBody.clientHeight);
        // Centre a cited sentence in the article window.
        const centre = (sent: HTMLElement) => () =>
          -gsap.utils.clamp(0, list.scrollHeight - clip.clientHeight, sent.offsetTop - clip.clientHeight * 0.38);

        const count = (sel: string, to: number, from = 0) => {
          const el = $(sel)[0] as HTMLElement;
          const o = { v: from };
          return gsap.fromTo(
            o,
            { v: from },
            {
              v: to,
              ease: "none",
              onUpdate: () => {
                el.textContent = Math.round(o.v).toLocaleString("en-US");
              },
            },
          );
        };

        gsap.set($(".ln, .pb .blk"), { autoAlpha: 0 });
        gsap.set($(".pb, .rb"), { opacity: 0 });
        gsap.set($(".art-s"), { "--cite": 0 });
        gsap.set($(".ln-drop"), { "--strike": 0 });

        const tl = gsap.timeline({
          defaults: { ease: "power2.out", duration: 0.5 },
          scrollTrigger: wide
            ? {
                trigger: root.current,
                start: "top top",
                end: () => `+=${window.innerHeight * 4.6}`,
                pin: ".llm-pin",
                scrub: 0.8,
                invalidateOnRefresh: true,
              }
            : { trigger: root.current, start: "top 55%" },
        });
        if (!wide) tl.timeScale(2.4);

        // The windows arrive as the section scrolls in, already holding Call A's prompt, so the
        // pin never opens on an empty frame.
        gsap
          .timeline({
            scrollTrigger: { trigger: root.current, start: "top 70%", toggleActions: "play none none reverse" },
          })
          .from($(".win"), { y: 90, autoAlpha: 0, stagger: 0.15, duration: 1.1, ease: "expo.out" })
          .fromTo($(".pa .blk"), { autoAlpha: 0, y: 16 }, { autoAlpha: 1, y: 0, stagger: 0.18, duration: 0.7 }, 0.5);

        // ── Call A: enrich ───────────────────────────────────────────────────
        tl.set($(".llm-call-a"), { attr: { "data-on": "true" } }, 0.2)
          .set($(".rail-enrich"), { attr: { "data-state": "on" } }, 0.2)
          .add(count(".tok-in", enrich.tokensIn).duration(1.2), 0.8);

        const aLines = $(".ra .ln");
        const aStart = 2;
        aLines.forEach((line, i) => {
          const at = aStart + i * 0.34;
          tl.fromTo(line, { autoAlpha: 0, x: -10 }, { autoAlpha: 1, x: 0, duration: 0.4 }, at);
          tl.to($(".ra")[0], { y: follow(line), duration: 0.3 }, at);
        });
        const aEnd = aStart + aLines.length * 0.34;
        tl.add(count(".tok-out", enrich.tokensOut).duration(aEnd - aStart), aStart)
          .to($(".ra .ln-drop"), { "--strike": 1, duration: 0.6, stagger: 0.25 }, aEnd + 0.2)
          .fromTo($(".ra .ln-why"), { autoAlpha: 0, x: -8 }, { autoAlpha: 1, x: 0, stagger: 0.25 }, "<+0.2")
          .set($(".rail-enrich"), { attr: { "data-state": "done" } }, aEnd + 1.4);

        // ── Call B: extract ──────────────────────────────────────────────────
        const b = aEnd + 1.8;
        // Opacity rather than autoAlpha for anything inside the windows: they are hidden by their
        // entrance when this timeline first renders, and autoAlpha would record that as the start.
        tl.to($(".pa, .ra"), { opacity: 0, yPercent: -4, duration: 0.6 }, b)
          .set($(".llm-call-a"), { attr: { "data-on": "false" } }, b)
          .set($(".llm-call-b"), { attr: { "data-on": "true" } }, b)
          .set($(".rail-segment"), { attr: { "data-state": "done" } }, b)
          .set($(".rail-extract"), { attr: { "data-state": "on" } }, b)
          .set($(".win-file-a"), { opacity: 0 }, b)
          .set($(".win-file-b"), { opacity: 1 }, b)
          // yPercent, not y: `y` is what follows the newest line, and the two must not fight.
          .fromTo($(".pb, .rb"), { opacity: 0, yPercent: 4 }, { opacity: 1, yPercent: 0, duration: 0.6 }, b + 0.4)
          .to($(".pb .blk"), { autoAlpha: 1, stagger: 0.25 }, b + 0.6)
          .add(count(".tok-in", extract.tokensIn, enrich.tokensIn).duration(1), b + 0.6)
          // The model reads the whole article before it answers.
          .fromTo(list, { y: 0 }, { y: () => -(list.scrollHeight - clip.clientHeight), duration: 2.2, ease: "power1.inOut" }, b + 1.2)
          .to(list, { y: 0, duration: 0.8, ease: "power2.inOut" }, ">");

        const bLines = $(".rb .ln");
        const bStart = b + 4.4;
        bLines.forEach((line, i) => {
          const at = bStart + i * 0.42;
          tl.fromTo(line, { autoAlpha: 0, x: -10 }, { autoAlpha: 1, x: 0, duration: 0.4 }, at);
          tl.to($(".rb")[0], { y: follow(line), duration: 0.3 }, at);
          const sents = (line.dataset.src ?? "")
            .split(" ")
            .map((id) => $(`.art-s[data-id="${id}"]`)[0] as HTMLElement | undefined)
            .filter((x): x is HTMLElement => Boolean(x));
          if (sents.length) {
            tl.to(list, { y: centre(sents[0]), duration: 0.4, ease: "power2.inOut" }, at);
            tl.to(sents, { "--cite": 1, duration: 0.3, stagger: 0.05 }, at + 0.2);
          }
        });
        const bEnd = bStart + bLines.length * 0.42;
        tl.add(count(".tok-out", extract.tokensOut).duration(bEnd - b), b)
          .set($(".rail-extract"), { attr: { "data-state": "done" } }, bEnd + 0.3)
          .fromTo($(".llm-out"), { autoAlpha: 0, y: 20 }, { autoAlpha: 1, y: 0, duration: 0.8 }, bEnd + 0.3)
          .to({}, { duration: 1 });
      });
    },
    { scope: root },
  );

  // Variations run beside the pipeline, not in it: the rail marks them "background", not a time.
  const rail: { stage: string; label: string; ms?: number; llm?: boolean; state?: string }[] = [
    { stage: "cache", label: "cache", ms: data.cache.ms, state: "done" },
    { stage: "enrich", label: "variations", llm: true },
    { stage: "segment", label: "segment", ms: data.timing.stages.find((s) => s.stage === "segment")?.ms ?? 0 },
    { stage: "extract", label: "extract", ms: extract.ms, llm: true },
    { stage: "ground", label: "ground", ms: data.timing.stages.find((s) => s.stage === "ground")?.ms ?? 0 },
    { stage: "resolve", label: "resolve", ms: data.timing.stages.find((s) => s.stage === "resolve")?.ms ?? 0 },
    { stage: "compile", label: "compile", ms: data.timing.stages.find((s) => s.stage === "compile")?.ms ?? 0 },
  ];

  return (
    <section className="llm" id="prompt" data-nav="light" ref={root}>
      <div className="llm-pin">
        <header className="llm-head">
          <div>
            <p className="st-eyebrow">02 · Inside the model</p>
            <h2 className="st-h2 llm-title">What the model sees.</h2>
          </div>
          <div className="llm-calls">
            <div className="llm-call llm-call-a" data-on="false">
              <span className="llm-call-n">A</span>
              <span>
                <b>Variations</b>
                <small>In the background, never delays the answer</small>
              </span>
            </div>
            <div className="llm-call llm-call-b" data-on="false">
              <span className="llm-call-n">B</span>
              <span>
                <b>Extract</b>
                <small>Pick the article sentences that fix it</small>
              </span>
            </div>
          </div>
        </header>

        <div className="llm-grid">
          <div className="win win-prompt">
            <div className="win-bar">
              <span className="win-label">Prompt</span>
              <span className="win-files">
                <span className="win-file win-file-a">{data.prompts.variations.file}</span>
                <span className="win-file win-file-b">{data.prompts.extract.file}</span>
              </span>
              <span className="win-meta">
                <b className="tok-in">0</b> tokens in
              </span>
            </div>
            <div className="win-body">
              <div className="win-scroll pa">
                <Instructions prompt={data.prompts.variations} />
                <div className="blk">
                  <span className="blk-label">complaint</span>
                  <p className="blk-query">{data.query}</p>
                </div>
                <div className="blk">
                  <span className="blk-label">return</span>
                  <pre className="blk-pre blk-schema">{VARIATIONS_RETURN}</pre>
                </div>
              </div>

              <div className="win-scroll pb">
                <Instructions prompt={data.prompts.extract} />
                <div className="blk blk-article">
                  <span className="blk-label">
                    article · {article.sentences.length} numbered sentences · for “{enrich.intent.title}”
                  </span>
                  <div className="art-clip">
                    <div className="art-list">
                      {sections.map((s) => (
                        <div className={`art-sec${s.relevant ? "" : " art-sec-off"}`} key={s.id}>
                          <span className="art-h">
                            {s.heading}
                            {!s.relevant && <em>below relevance floor</em>}
                          </span>
                          {s.sentences.map((x) => (
                            <p className="art-s" data-id={x.id} key={x.id}>
                              <span className="art-id">{x.id}</span>
                              {x.text}
                            </p>
                          ))}
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
                <div className="blk">
                  <span className="blk-label">return</span>
                  <pre className="blk-pre blk-schema">{EXTRACT_RETURN}</pre>
                </div>
              </div>
            </div>
          </div>

          <div className="win win-resp">
            <div className="win-bar">
              <span className="win-label">Response</span>
              <span className="win-live">
                <i />
                <span className="win-model">
                  {llm.model} · temperature {llm.temperature}
                </span>
                <small className="win-fallback">
                  fallback {llm.fallback} · {llm.fallbackTemperature.toFixed(1)}
                </small>
              </span>
              <span className="win-meta">
                <b className="tok-out">0</b> tokens out
              </span>
            </div>
            <div className="win-body">
              <div className="win-scroll ra">
                <div className="ln">{p("{")}</div>
                <div className="ln i1">
                  {k("variations")}
                  {p(": [")}
                </div>
                {enrich.kept.map((v) => (
                  <div className="ln i2" key={v}>
                    {q(v)}
                    {p(",")}
                  </div>
                ))}
                {enrich.dropped.map((v) => (
                  <div className="ln i2 ln-drop" key={v.text}>
                    <span className="ln-struck">
                      {q(v.text)}
                      {p(",")}
                    </span>
                    <span className="ln-why">
                      {(REASON[v.reason] ?? ((j: number) => `filtered · ${v.reason} ${j}`))(
                        v.jaccard,
                        enrich.kept.length,
                      )}
                    </span>
                  </div>
                ))}
                <div className="ln i1">{p("]")}</div>
                <div className="ln">{p("}")}</div>
              </div>

              <div className="win-scroll rb">
                <div className="ln">
                  {p("{ ")}
                  {k("goals")}
                  {p(": [{ ")}
                  {k("title")}
                  {p(": ")}
                  {q(enrich.intent.title)}
                  {p(", ")}
                  {k("domain")}
                  {p(": ")}
                  {q(enrich.intent.domain)}
                  {p(",")}
                </div>
                <div className="ln i1">
                  {k("actions")}
                  {p(": [")}
                </div>
                {shown.map((a) => (
                  <div className="ln i2 ln-step" data-src={ids(a).join(" ")} key={a.name}>
                    {p("{ ")}
                    {k("src_ids")}
                    {p(": [")}
                    {ids(a).map((id, i) => (
                      <Fragment key={id}>
                        {i > 0 && p(", ")}
                        <span className="t-id">{id}</span>
                      </Fragment>
                    ))}
                    {p("], ")}
                    {k("name")}
                    {p(": ")}
                    {q(a.name)}
                    {p(" },")}
                  </div>
                ))}
                {more.length > 0 && (
                  <div className="ln i2 ln-more">
                    {p("// ")}+{more.length} more actions
                  </div>
                )}
                <div className="ln">{p("]}]}")}</div>
              </div>
            </div>
          </div>
        </div>

        <div className="llm-foot">
          <ol className="rail">
            {rail.map((r) => (
              <li
                key={r.stage}
                className={`rail-i rail-${r.stage}${r.llm ? " rail-llm" : ""}`}
                data-state={r.state ?? "off"}
              >
                <span className="rail-dot" />
                <b>{r.label}</b>
                <small>{r.ms === undefined ? "background" : `${r.ms.toLocaleString("en-US")} ms`}</small>
              </li>
            ))}
          </ol>
          <p className="llm-out">
            {extract.actions.length} actions citing {cited} sentences, split into steps in the article&apos;s own
            words. <b>Now we check the model&apos;s homework.</b>
          </p>
        </div>
        <p className="llm-note">
          {llm.model} raced against {llm.fastModel}. Token counts and timings are from the recorded demo run.
        </p>
      </div>
    </section>
  );
}
