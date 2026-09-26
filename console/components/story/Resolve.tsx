"use client";

import { useRef } from "react";
import { gsap, useGSAP } from "@/lib/gsap";
import type { StoryData } from "@/lib/story";

export function Resolve({ data }: { data: StoryData }) {
  const root = useRef<HTMLElement>(null);
  const f = data.resolve.featured;
  const { counts } = data.resolve;
  const dummy = data.plan.actions.find((a) => a.actionName.toLowerCase().includes("factory"));

  useGSAP(
    () => {
      const mm = gsap.matchMedia();
      mm.add("(prefers-reduced-motion: no-preference)", () => {
        const $ = gsap.utils.selector(root);
        gsap.from($(".rs-head > *"), {
          y: 60,
          autoAlpha: 0,
          stagger: 0.12,
          duration: 1.2,
          ease: "expo.out",
          scrollTrigger: { trigger: $(".rs-head")[0], start: "top 80%" },
        });

        const flow = gsap.timeline({
          defaults: { ease: "expo.out", duration: 1 },
          scrollTrigger: { trigger: $(".rs-flow")[0], start: "top 75%" },
        });
        flow
          .from($(".rs-card"), { y: 90, rotate: 2.5, autoAlpha: 0, stagger: 0.18 })
          .from($(".rs-arrow"), { scaleX: 0, transformOrigin: "left center", stagger: 0.18, duration: 0.6 }, 0.3)
          .from($(".rs-path > span"), { y: 16, autoAlpha: 0, stagger: 0.08, duration: 0.6 }, 0.4)
          .from($(".rs-bar i"), { scaleX: 0, transformOrigin: "left center", stagger: 0.12, duration: 1.2 }, 0.6)
          .fromTo($(".rs-cand-win"), { "--win": 0 }, { "--win": 1, duration: 0.5 }, 1.5)
          .from($(".rs-hit > *"), { y: 20, autoAlpha: 0, stagger: 0.07, duration: 0.7 }, 1.4)
          .fromTo(
            $(".rs-val svg path"),
            { strokeDasharray: 30, strokeDashoffset: 30 },
            { strokeDashoffset: 0, duration: 0.6 },
            2,
          );

        gsap.from($(".tier"), {
          y: 120,
          rotate: (i: number) => (i % 2 ? -3 : 3),
          autoAlpha: 0,
          stagger: 0.12,
          duration: 1.3,
          ease: "expo.out",
          scrollTrigger: { trigger: $(".rs-tiers")[0], start: "top 80%" },
        });
        $(".tier-n").forEach((el) => {
          const to = Number(el.dataset.to);
          const o = { v: 0 };
          gsap.to(o, {
            v: to,
            duration: 1.4,
            ease: "power3.out",
            scrollTrigger: { trigger: el, start: "top 85%" },
            onUpdate: () => {
              el.textContent = String(Math.round(o.v));
            },
          });
        });
      });
    },
    { scope: root },
  );

  const path = f.path.split(" > ");

  return (
    <section className="rs" id="resolve" data-nav="light" ref={root}>
      <div className="rs-inner">
        <header className="rs-head">
          <div>
            <p className="st-eyebrow">04 · Resolve</p>
            <h2 className="st-h2">
              Straight to the
              <br />
              right screen.
            </h2>
          </div>
          <p className="st-lead rs-lead">
            Each step names a screen. OneClick finds it in a map of real Settings pages and copies the
            catalog&apos;s link exactly as written. <b>The model never writes a link.</b>
          </p>
        </header>

        <div className="rs-flow">
          <div className="rs-card rs-q">
            <span className="rs-label">The step</span>
            <p className="rs-step">&ldquo;{f.step}&rdquo;</p>
            <div className="rs-path">
              {path.map((seg) => (
                <span key={seg}>{seg}</span>
              ))}
            </div>
            <span className="rs-verb">
              intent · <b>{f.verb}</b>
            </span>
          </div>

          <span className="rs-arrow" aria-hidden />

          <div className="rs-card rs-rank">
            <span className="rs-label">Screen candidates · BM25 + dense, fused, then name and polarity</span>
            {f.candidates.map((c, i) => (
              <div className={`rs-cand${i === 0 ? " rs-cand-win" : ""}`} key={`${c.id}-${i}`}>
                <span className="rs-cand-name">
                  {c.path.split(" > ").pop()} <small className="rs-cand-id">{c.id}</small>
                </span>
                <span className="rs-bar">
                  <i style={{ width: `${(c.score / f.candidates[0].score) * 100}%` }} />
                </span>
                <b>{c.score.toFixed(2)}</b>
              </div>
            ))}
          </div>

          <span className="rs-arrow" aria-hidden />

          <div className="rs-card rs-hit">
            <span className="rs-label">Catalog entry</span>
            <b className="rs-id">{f.entryId}</b>
            <p className="rs-msg">{f.message}</p>
            <code className="rs-uri">{f.uri}</code>
            <div className="rs-val">
              <svg viewBox="0 0 24 24" aria-hidden>
                <path d="m6 12.5 4 4 8-9" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
              </svg>
              <span>
                Checks <b>{f.validation.key}</b>
                {f.validation.condition && ` ${f.validation.condition === "equal" ? "=" : f.validation.condition} `}
                {f.validation.value && <b>{f.validation.value}</b>}
              </span>
            </div>
            <span className="rs-type">{f.originalType} · an enable toggle, so it can prove itself</span>
          </div>
        </div>
        <p className="st-fine rs-fine">Candidates and scores from one recorded run of the resolver.</p>

        <div className="rs-tiers">
          <div className="tier tier-lime">
            <b className="tier-n" data-to={counts.catalog}>
              {counts.catalog}
            </b>
            <h3>One tap</h3>
            <p>A catalog link, copied character for character, that opens the exact screen.</p>
          </div>
          <div className="tier tier-blue">
            <b className="tier-n" data-to={counts.dummy}>
              {counts.dummy}
            </b>
            <h3>Known screen, no link</h3>
            <p>
              {dummy ? `${dummy.actionName}: ` : ""}the screen is real, the catalog has no entry for it, so it
              can only get the placeholder.
            </p>
          </div>
          <div className="tier tier-ink">
            <b className="tier-n" data-to={counts.manual}>
              {counts.manual}
            </b>
            <h3>By hand</h3>
            <p>Physical steps, and disruptive ones like a restart that OneClick will never fire for you.</p>
          </div>
          <div className="tier tier-gap">
            <span className="tier-bang" aria-hidden>
              !
            </span>
            <h3>Gaps stay gaps</h3>
            <p>
              The catalog has no entry for Software update, Safe mode or Factory data reset. Those steps stay
              manual. That is the right answer, not a bug.
            </p>
          </div>
        </div>
      </div>
    </section>
  );
}
