"use client";

import { useRef } from "react";
import { gsap, useGSAP } from "@/lib/gsap";
import type { StoryData } from "@/lib/story";

/**
 * Measured, not claimed. The engine's numbers are read from docs/metrics.md (eval/report.py) when
 * the site is built, so this section can only say what the committed report says; the rest is the
 * eval sets' design, the catalog's own shape and the answer key. Anything the report has not
 * measured shows as pending, never estimated.
 */

const R = 42;
const RING = 2 * Math.PI * R;

// The report prints one decimal; keep it, but drop a trailing ".0".
const pctText = (x: number | null) => (x === null ? "pending" : `${Number(x.toFixed(1))}%`);
const msText = (x: number | null) => (x === null ? "pending" : `${Math.round(x).toLocaleString("en-US")} ms`);

function Ring({ value, label, sub, tone }: { value: number | null; label: string; sub: string; tone: string }) {
  const v = (value ?? 0) / 100;
  return (
    <div className="pf-ring">
      <svg viewBox="0 0 100 100" aria-hidden>
        <circle cx="50" cy="50" r={R} className="pf-ring-track" />
        <circle
          cx="50"
          cy="50"
          r={R}
          className={`pf-ring-fill ${tone}`}
          strokeDasharray={RING}
          strokeDashoffset={RING * (1 - v)}
          data-offset={RING * (1 - v)}
          transform="rotate(-90 50 50)"
        />
      </svg>
      <b>{pctText(value)}</b>
      <span>{label}</span>
      <small>{sub}</small>
    </div>
  );
}

export function Proof({ data }: { data: StoryData }) {
  const root = useRef<HTMLElement>(null);
  const { proof } = data;
  const { sets, gold, catalog, overlap, metrics } = proof;
  const { resolver, cache, latency } = metrics;
  const source = metrics.commit ? `docs/metrics.md at ${metrics.commit}` : "docs/metrics.md";
  const e2e = [
    [
      "Cold answer, p50 / p95",
      latency.coldP50 === null || latency.coldP95 === null
        ? "pending"
        : `${(latency.coldP50 / 1000).toFixed(1)} / ${(latency.coldP95 / 1000).toFixed(1)} s`,
    ],
    ["Step accuracy, judged", metrics.stepAccuracy === null ? "pending" : `${metrics.stepAccuracy.toFixed(2)} / 3`],
    ["Schema-valid responses", pctText(metrics.schemaValid)],
    ["URLs leaked", metrics.urlLeaks === null ? "pending" : String(metrics.urlLeaks)],
  ] as const;
  const measured = e2e.some(([, v]) => v !== "pending");
  const owners = Object.entries(gold.owners)
    .map(([o, n]) => `${n} by ${o}`)
    .join(", ");
  const setRows = [
    ["Paraphrases", sets.paraphrases],
    ["Near misses", sets.nearMiss],
    ["Unseen domains", sets.unseen],
    ["Adversarial", sets.adversarial],
  ] as const;
  const setMax = Math.max(...setRows.map(([, n]) => n));
  const share = catalog.entries ? catalog.verifiable / catalog.entries : 0;

  useGSAP(
    () => {
      const mm = gsap.matchMedia();
      mm.add("(prefers-reduced-motion: no-preference)", () => {
        const $ = gsap.utils.selector(root);
        gsap.from($(".pf-head > *"), {
          y: 60,
          autoAlpha: 0,
          stagger: 0.12,
          duration: 1.2,
          ease: "expo.out",
          scrollTrigger: { trigger: $(".pf-head")[0], start: "top 80%" },
        });

        $(".pf-card").forEach((card) => {
          const tl = gsap.timeline({
            defaults: { ease: "expo.out" },
            scrollTrigger: { trigger: card, start: "top 82%" },
          });
          tl.from(card, { y: 90, autoAlpha: 0, duration: 1.1 });
          // Each card holds one kind of chart; animate whichever it has.
          const parts: [string, gsap.TweenVars][] = [
            [".pf-hbar i", { scaleX: 0, transformOrigin: "left center", stagger: 0.15, duration: 1.4 }],
            [".pf-vbar i", { scaleY: 0, transformOrigin: "50% 100%", stagger: 0.1, duration: 1.2 }],
            [".pf-stack i", { scaleX: 0, transformOrigin: "left center", stagger: 0.12, duration: 1 }],
            [".pf-pending li", { x: -24, autoAlpha: 0, stagger: 0.08, duration: 0.8 }],
          ];
          parts.forEach(([sel, vars]) => {
            const els = card.querySelectorAll(sel);
            if (els.length) tl.from(els, vars, 0.3);
          });
          card.querySelectorAll<SVGCircleElement>(".pf-ring-fill, .pf-donut-fill").forEach((c) => {
            tl.fromTo(
              c,
              { strokeDashoffset: Number(c.getAttribute("stroke-dasharray")) },
              { strokeDashoffset: Number(c.dataset.offset), duration: 1.6, ease: "power3.out" },
              0.35,
            );
          });
        });
      });
    },
    { scope: root },
  );

  return (
    <section className="pf" id="proof" data-nav="light" ref={root}>
      <div className="pf-inner">
        <header className="pf-head">
          <p className="st-eyebrow">09 · Proof</p>
          <h2 className="st-h2">Measured, not claimed.</h2>
          <p className="st-lead">
            Every engine number here is read from the committed evaluation report ({source}
            {metrics.date ? `, ${metrics.date}` : ""}) when the site is built. Anything it has not measured says
            pending.
          </p>
        </header>

        <div className="pf-grid">
          <article className="pf-card pf-span-7">
            <div className="pf-top">
              <h3>Near misses look more alike than paraphrases</h3>
              <span className="tag tag-lime">Test design</span>
            </div>
            <div className="pf-chart">
              <div className="pf-grid-lines" aria-hidden>
                {[0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6].map((t) => (
                  <span key={t} style={{ left: `${(t / 0.6) * 100}%` }}>
                    {t.toFixed(1)}
                  </span>
                ))}
              </div>
              <div className="pf-hbar">
                <span>Paraphrases · {sets.paraphrases}</span>
                <i className="blue" style={{ width: `${(overlap.paraphrase / 0.6) * 100}%` }}>
                  <b>{overlap.paraphrase.toFixed(2)}</b>
                </i>
              </div>
              <div className="pf-hbar">
                <span>Near misses · {sets.nearMiss}</span>
                <i className="red" style={{ width: `${(overlap.nearMiss / 0.6) * 100}%` }}>
                  <b>{overlap.nearMiss.toFixed(2)}</b>
                </i>
              </div>
            </div>
            <p className="pf-note">
              Mean word overlap with the original complaint. The near misses were written to overlap more than
              the paraphrases, so similarity alone can&apos;t tell them apart. Only the slot guard can.
            </p>
          </article>

          <article className="pf-card pf-span-5">
            <div className="pf-top">
              <h3>Right screen, first try</h3>
              <span className="tag tag-blue">Resolver</span>
            </div>
            <div className="pf-rings">
              <Ring value={resolver.top1} label="Screen Graph resolver" sub="precision@1" tone="lime" />
              <Ring value={resolver.bm25Top1} label="plain keyword search" sub="precision@1" tone="blue" />
            </div>
            <p className="pf-note">
              On the {resolver.n ?? gold.catalog} hand-labelled steps that have a real catalog answer. A wrong or
              unsafe link on {pctText(resolver.wrongLink)} of all {gold.labelled} labelled steps. Both lanes labelled
              them; the mapping lane tuned on its own half.
              {resolver.llmTop1 !== null &&
                ` Handing the whole catalog to an LLM instead: ${pctText(resolver.llmTop1)}, at ${msText(
                  resolver.llmP95,
                )} a step (p95).`}
            </p>
          </article>

          <article className="pf-card pf-span-4">
            <div className="pf-top">
              <h3>The cache, measured</h3>
              <span className="tag tag-lime">Cache</span>
            </div>
            <ul className="pf-stats">
              <li>
                <b>{pctText(cache.paraphraseHit)}</b>
                <span>reworded questions recognised</span>
                <small>p95 {msText(latency.paraphraseP95)}</small>
              </li>
              <li>
                <b>{pctText(cache.falseHit)}</b>
                <span>near misses served a wrong plan</span>
                <small>of {cache.nearMisses ?? sets.nearMiss}</small>
              </li>
              <li>
                <b>{cache.threshold.toFixed(2)}</b>
                <span>similarity a hit needs, plus matching slots and article</span>
                <small>threshold</small>
              </li>
            </ul>
            <p className="pf-note">
              Our {sets.paraphrases} held-out paraphrases and {sets.nearMiss} near misses, over HTTP. A repeat
              answers in {msText(latency.exactP95)} at p95.
            </p>
          </article>

          <article className="pf-card pf-span-4">
            <div className="pf-top">
              <h3>Held-out test sets</h3>
              <span className="tag tag-ink">Eval</span>
            </div>
            <div className="pf-vbars">
              {setRows.map(([label, n]) => (
                <div className="pf-vbar" key={label}>
                  <b>{n}</b>
                  <i style={{ height: `${Math.max(4, (n / setMax) * 100)}%` }} />
                  <span>{label}</span>
                </div>
              ))}
            </div>
            <p className="pf-note">
              {setRows.reduce((s, [, n]) => s + n, 0)} queries the engine is never tuned on.
            </p>
          </article>

          <article className="pf-card pf-span-4">
            <div className="pf-top">
              <h3>Links that can prove themselves</h3>
              <span className="tag tag-blue">Catalog</span>
            </div>
            <div className="pf-donut">
              <svg viewBox="0 0 100 100" aria-hidden>
                <circle cx="50" cy="50" r={R} className="pf-ring-track" />
                <circle
                  cx="50"
                  cy="50"
                  r={R}
                  className="pf-donut-fill"
                  strokeDasharray={RING}
                  strokeDashoffset={RING * (1 - share)}
                  data-offset={RING * (1 - share)}
                  transform="rotate(-90 50 50)"
                />
              </svg>
              <div>
                <b>{catalog.verifiable}</b>
                <span>of {catalog.entries}</span>
              </div>
            </div>
            <p className="pf-note">
              Only enable toggles carry a full check. Every other link can only show that a screen opened.
            </p>
          </article>

          <article className="pf-card pf-span-4">
            <div className="pf-top">
              <h3>Answer key</h3>
              <span className="tag tag-amber">Gold</span>
            </div>
            <div className="pf-gold">
              <b>{gold.labelled}</b>
              <span>{gold.labelled >= gold.target ? "steps labelled" : `/ ${gold.target} steps labelled`}</span>
            </div>
            <div className="pf-stack">
              <i className="lime" style={{ flexGrow: gold.catalog }} />
              <i className="blue" style={{ flexGrow: gold.dummy }} />
              <i className="grey" style={{ flexGrow: gold.manual }} />
              <i className="empty" style={{ flexGrow: Math.max(0, gold.target - gold.labelled) }} />
            </div>
            <div className="pf-stack-legend">
              <span>
                <i className="lime" /> {gold.catalog} catalog
              </span>
              <span>
                <i className="blue" /> {gold.dummy} placeholder
              </span>
              <span>
                <i className="grey" /> {gold.manual} manual
              </span>
            </div>
            <p className="pf-note">The answer key for deeplink precision: {owners}.</p>
          </article>

          <article className={`pf-card pf-span-8${measured ? "" : " pf-card-pending"}`}>
            <div className="pf-top">
              <h3>End to end, over HTTP</h3>
              <span className={`tag ${measured ? "tag-lime" : "tag-ghost"}`}>
                {measured ? "Live engine" : "Pending"}
              </span>
            </div>
            <ul className="pf-pending">
              {e2e.map(([label, value]) => (
                <li key={label}>
                  <span>{label}</span>
                  <b className={value === "pending" ? undefined : "is-measured"}>{value}</b>
                </li>
              ))}
            </ul>
            {metrics.judgedPlans !== null && (
              <p className="pf-note">Step accuracy: an independent model judged {metrics.judgedPlans} plans, 0 to 3.</p>
            )}
          </article>
        </div>
      </div>
    </section>
  );
}
