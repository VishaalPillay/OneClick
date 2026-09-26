"use client";

import { useRef } from "react";
import { gsap, useGSAP } from "@/lib/gsap";
import type { StoryData } from "@/lib/story";
import { LLM_STAGES } from "@/lib/trace";

const fmt = (ms: number) => (ms >= 100 ? Math.round(ms).toLocaleString("en-US") : ms.toFixed(1));

export function Memory({ data }: { data: StoryData }) {
  const root = useRef<HTMLElement>(null);
  const { timing, semanticHit, proof } = data;
  const speedup = Math.floor(timing.total / timing.semantic);
  const [cracked, battery] = [
    proof.nearMisses.find((n) => n.differsIn === "symptom"),
    proof.nearMisses.find((n) => n.differsIn === "component"),
  ];

  const rows = [
    { key: "cold", label: "First time", sub: "cold · one model call", ms: timing.total },
    { key: "semantic", label: "Reworded", sub: "semantic hit", ms: timing.semantic },
    { key: "exact", label: "Same words", sub: "exact hit", ms: timing.exact },
  ];

  useGSAP(
    () => {
      const mm = gsap.matchMedia();
      mm.add("(prefers-reduced-motion: no-preference)", () => {
        const $ = gsap.utils.selector(root);
        gsap.from($(".mm-head > *"), {
          y: 60,
          autoAlpha: 0,
          stagger: 0.12,
          duration: 1.2,
          ease: "expo.out",
          scrollTrigger: { trigger: $(".mm-head")[0], start: "top 80%" },
        });

        const bars = gsap.timeline({
          defaults: { ease: "expo.out" },
          scrollTrigger: { trigger: $(".mm-bars")[0], start: "top 72%" },
        });
        bars.from($(".mm-row"), { autoAlpha: 0, y: 40, stagger: 0.12, duration: 0.9 });
        $(".mm-bar").forEach((bar, i) => {
          bars.from(bar, { scaleX: 0, transformOrigin: "left center", duration: i === 0 ? 2.2 : 0.8 }, 0.3 + i * 0.25);
        });
        $(".mm-val").forEach((el, i) => {
          const to = Number(el.dataset.to);
          const o = { v: 0 };
          bars.to(
            o,
            {
              v: to,
              duration: i === 0 ? 2.2 : 0.8,
              ease: "power3.out",
              onUpdate: () => {
                el.textContent = fmt(o.v);
              },
            },
            0.3 + i * 0.25,
          );
        });
        bars.from($(".mm-big"), { scale: 0.7, autoAlpha: 0, duration: 1.2, ease: "back.out(1.4)" }, 1.8);

        gsap.from($(".mm-card"), {
          y: 100,
          rotate: (i: number) => (i ? -2.5 : 2.5),
          autoAlpha: 0,
          stagger: 0.15,
          duration: 1.2,
          ease: "expo.out",
          scrollTrigger: { trigger: $(".mm-cards")[0], start: "top 78%" },
        });
      });
    },
    { scope: root },
  );

  return (
    <section className="mm" id="memory" data-nav="light" ref={root}>
      <div className="mm-inner">
        <header className="mm-head">
          <p className="st-eyebrow">07 · Memory</p>
          <h2 className="st-h2">
            Ask again.
            <br />
            <span className="mm-soft">It doesn&apos;t think twice.</span>
          </h2>
        </header>

        <div className="mm-bars">
          {rows.map((r) => (
            <div className={`mm-row mm-row-${r.key}`} key={r.key}>
              <div className="mm-lab">
                <b>{r.label}</b>
                <small>{r.sub}</small>
              </div>
              <div className="mm-track">
                {r.key === "cold" ? (
                  <div className="mm-bar mm-bar-cold">
                    {timing.stages.map((s) => (
                      <i
                        key={s.stage}
                        className={LLM_STAGES.has(s.stage) ? "is-llm" : ""}
                        style={{ flexGrow: s.ms }}
                        title={`${s.stage} ${fmt(s.ms)} ms`}
                      />
                    ))}
                  </div>
                ) : (
                  <div className="mm-bar" style={{ width: `max(6px, ${(r.ms / timing.total) * 100}%)` }} />
                )}
              </div>
              <div className="mm-num">
                <b className="mm-val" data-to={r.ms}>
                  {fmt(r.ms)}
                </b>
                <small>ms</small>
              </div>
            </div>
          ))}
          <div className="mm-legend">
            <span>
              <i className="is-llm" /> time inside the model
            </span>
            <span>
              <i /> everything else
            </span>
            <span className="st-fine">Timings and similarity from one recorded run</span>
          </div>
        </div>

        <div className="mm-big">
          <b>{speedup}×</b>
          <span>faster on a reworded question. A hit skips the model call entirely, and the pink is gone.</span>
        </div>

        <div className="mm-cards">
          <article className="mm-card mm-card-hit">
            <span className="tag tag-lime">Reused</span>
            <h3>Reworded, still recognised</h3>
            <blockquote>&ldquo;{semanticHit.query}&rdquo;</blockquote>
            <p className="mm-match">
              matches <span>&ldquo;{semanticHit.matched}&rdquo;</span>
            </p>
            <div className="mm-chips">
              <span>
                similarity <b>{semanticHit.similarity.toFixed(2)}</b> ≥ {semanticHit.threshold.toFixed(2)}
              </span>
              <span>same article</span>
              <span>same slots</span>
            </div>
          </article>

          <article className="mm-card mm-card-miss">
            <span className="tag tag-red">Never reused</span>
            <h3>Close in words. A different problem.</h3>
            {[cracked, battery].filter(Boolean).map((n) => (
              <div className="mm-near" key={n!.query}>
                <blockquote>&ldquo;{n!.query}&rdquo;</blockquote>
                <span className="mm-diff">
                  {n!.differsIn} <b>{n!.slots[n!.differsIn] ?? "none"}</b> · yours: {data.slots[n!.differsIn] ?? "none"}
                </span>
              </div>
            ))}
            <p className="mm-rule">
              Before anything is reused, the slot guard compares what broke and how. Our held-out set has{" "}
              {proof.sets.nearMiss} near misses like these, and every one must miss.
            </p>
          </article>
        </div>
      </div>
    </section>
  );
}
