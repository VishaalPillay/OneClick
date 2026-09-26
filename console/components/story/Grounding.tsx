"use client";

import { useRef } from "react";
import { MEDIA, ScrollTrigger, gsap, useGSAP } from "@/lib/gsap";
import type { StoryAction, StoryData } from "@/lib/story";

interface Receipt {
  src: string;
  action: string;
  text: string;
  score: number;
}

/**
 * Four kept steps to show receipts for: the first four sentences the model cited, in the order
 * it cited them, each paired with the most specific step that cites it.
 */
function receipts(actions: StoryAction[], dropped: string, limit = 4): Receipt[] {
  const best = new Map<string, Receipt>();
  for (const a of actions) {
    for (const s of a.steps) {
      const src = s.src[0];
      if (s.text === dropped || !src) continue;
      const have = best.get(src);
      if (!have && best.size >= limit) continue;
      if (!have || s.text.length > have.text.length) {
        best.set(src, { src, action: a.name, text: s.text, score: s.score });
      }
    }
  }
  return [...best.values()];
}

const idNum = (id: string) => Number(id.slice(1));

export function Grounding({ data }: { data: StoryData }) {
  const root = useRef<HTMLElement>(null);
  const { ground, article } = data;
  // A run that threw a step out shows it; select mode rarely does (the model can only cite the
  // article's own sentences), so otherwise the card shows the kept step that came closest to the bar.
  const clean = ground.dropped === null;
  const drop = ground.dropped ?? { ...ground.lowest, reason: "" };
  const pairs = receipts(data.extract.actions, drop.text);
  const cited = new Set([...pairs.map((p) => p.src), ...drop.src]);
  const sentences = article.sentences.filter((s) => cited.has(s.id)).sort((a, b) => idNum(a.id) - idNum(b.id));
  const noSharedTerm = drop.reason.includes("no_shared_term");

  useGSAP(
    () => {
      const $ = gsap.utils.selector(root);
      const stage = $(".gr-stage")[0] as HTMLElement;

      // Each receipt is a curve from the step's right edge to its sentence's left edge. Layout
      // offsets, not bounding boxes, so the cards' own entrance transforms never bend the lines.
      const draw = () => {
        $(".gr-path").forEach((path) => {
          const step = $(`.gr-step[data-i="${path.dataset.i}"]`)[0] as HTMLElement | undefined;
          const sent = step && ($(`.gr-sent[data-id="${step.dataset.src}"]`)[0] as HTMLElement | undefined);
          if (!step || !sent) return;
          const x1 = step.offsetLeft + step.offsetWidth;
          const y1 = step.offsetTop + step.offsetHeight / 2;
          const x2 = sent.offsetLeft;
          const y2 = sent.offsetTop + sent.offsetHeight / 2;
          const dx = (x2 - x1) * 0.55;
          path.setAttribute("d", `M${x1},${y1} C${x1 + dx},${y1} ${x2 - dx},${y2} ${x2},${y2}`);
        });
        const svg = $(".gr-lines")[0] as unknown as SVGSVGElement;
        svg.setAttribute("viewBox", `0 0 ${stage.offsetWidth} ${stage.offsetHeight}`);
      };
      draw();
      ScrollTrigger.addEventListener("refresh", draw);

      const mm = gsap.matchMedia();
      mm.add(MEDIA, (ctx) => {
        const wide = Boolean(ctx.conditions?.wide);
        const kept = $(".gr-kept")[0] as HTMLElement;
        const tally = { v: 0 };
        kept.textContent = "0";

        gsap.set($(".gr-path"), { strokeDasharray: 1, strokeDashoffset: 1 });
        gsap.set($(".gr-ok, .gr-stamp, .gr-out, .gr-verdict-row > span"), { autoAlpha: 0 });
        gsap.set($(".gr-meter-fill"), { scaleX: 0 });
        gsap.set($(".gr-rogue-text"), { "--strike": 0 });

        const tl = gsap.timeline({
          defaults: { ease: "power2.out", duration: 0.5 },
          scrollTrigger: wide
            ? {
                trigger: root.current,
                start: "top top",
                end: () => `+=${window.innerHeight * 3.4}`,
                pin: ".gr-pin",
                scrub: 0.8,
                invalidateOnRefresh: true,
              }
            : { trigger: root.current, start: "top 55%" },
        });
        if (!wide) tl.timeScale(2);

        // Cards arrive as the section scrolls in; the pinned scrub then checks them one by one.
        gsap
          .timeline({
            defaults: { stagger: 0.12, duration: 1, ease: "expo.out" },
            scrollTrigger: { trigger: root.current, start: "top 65%", toggleActions: "play none none reverse" },
          })
          .from($(".gr-step"), { x: -80, autoAlpha: 0 }, 0)
          .from($(".gr-sent"), { x: 80, autoAlpha: 0 }, 0.1);

        const per = ground.kept / pairs.length;
        pairs.forEach((p, i) => {
          const at = 0.3 + i * 1.1;
          const step = $(`.gr-step[data-i="${i}"]`);
          const sent = $(`.gr-sent[data-id="${p.src}"]`);
          tl.set(step, { attr: { "data-state": "on" } }, at)
            .to($(`.gr-path[data-i="${i}"]`), { strokeDashoffset: 0, duration: 0.7, ease: "power2.inOut" }, at)
            .set(sent, { attr: { "data-state": "on" } }, at + 0.55)
            .fromTo(step[0].querySelector(".gr-ok"), { autoAlpha: 0, scale: 0.6 }, { autoAlpha: 1, scale: 1, ease: "back.out(2.4)" }, at + 0.6)
            .to(
              tally,
              {
                v: Math.round(per * (i + 1)),
                duration: 0.6,
                onUpdate: () => {
                  kept.textContent = String(Math.round(tally.v));
                },
              },
              at + 0.6,
            );
        });

        // The step the model made up: it cites a real sentence, the sentence says no such thing.
        const r = 0.3 + pairs.length * 1.1 + 0.4;
        const rogue = $(".gr-rogue");
        const target = $(`.gr-sent[data-id="${drop.src[0]}"]`);
        tl.set(rogue, { attr: { "data-state": "on" } }, r)
          .to($(".gr-path-rogue"), { strokeDashoffset: 0, duration: 0.8, ease: "power2.inOut" }, r)
          .set(target, { attr: { "data-state": clean ? "on" : "bad" } }, r + 0.7)
          .to($(".gr-meter-fill"), { scaleX: drop.score, duration: 0.9, ease: "power3.out" }, r + 0.8)
          .to($(".gr-verdict-row > span"), { autoAlpha: 1, stagger: 0.25 }, r + 1.1)
          .fromTo(
            $(".gr-stamp"),
            { autoAlpha: 0, scale: 2.2, rotate: -18 },
            { autoAlpha: 1, scale: 1, rotate: -8, duration: 0.45, ease: "back.out(1.6)" },
            r + 1.9,
          );
        if (!clean) {
          tl.to($(".gr-rogue-text"), { "--strike": 1, duration: 0.4 }, r + 2.1)
            .to($(".gr-path-rogue"), { opacity: 0, duration: 0.4 }, r + 2.6)
            .set(target, { attr: { "data-state": "on" } }, r + 2.6)
            // Opacity, not autoAlpha: the card is hidden by its entrance when this timeline first
            // renders, and autoAlpha would take that as its starting value.
            .to(rogue, { y: 200, rotate: 7, opacity: 0, duration: 1.1, ease: "power2.in" }, r + 2.7);
        }
        tl.to($(".gr-out"), { autoAlpha: 1, duration: 0.5 }, r + (clean ? 2.3 : 3.2)).to({}, { duration: 0.8 });

        return () => {
          kept.textContent = String(ground.kept);
        };
      });

      return () => ScrollTrigger.removeEventListener("refresh", draw);
    },
    { scope: root },
  );

  return (
    <section className="gr" id="grounding" data-nav="dark" ref={root}>
      <div className="gr-pin">
        <header className="gr-head">
          <div>
            <p className="st-eyebrow">03 · Grounding</p>
            <h2 className="st-h2">
              Every step shows
              <br />
              its receipt.
            </h2>
          </div>
          <div className="gr-stat">
            <div className="gr-num">
              <b className="gr-kept">{ground.kept}</b>
              <span>/{ground.proposed}</span>
            </div>
            <p>
              steps proven against the article.
              <br />
              <span className="gr-out" data-clean={clean ? "true" : undefined}>
                {clean
                  ? "None thrown out: the model can only point at the article's sentences."
                  : `${ground.proposed - ground.kept} thrown out.`}
              </span>
            </p>
          </div>
        </header>

        <div className="gr-stage">
          <div className="gr-steps">
            {pairs.map((p, i) => (
              <div className="gr-step" data-i={i} data-src={p.src} data-state="off" key={p.src}>
                <span className="gr-act">{p.action}</span>
                <p>{p.text}</p>
                <span className="gr-cite">{p.src}</span>
                <span className="gr-ok">
                  <svg viewBox="0 0 24 24" aria-hidden>
                    <path d="m6 12.5 4 4 8-9" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
                  </svg>
                  {p.score.toFixed(2)}
                </span>
              </div>
            ))}
            <div
              className="gr-step gr-rogue"
              data-i="rogue"
              data-src={drop.src[0]}
              data-state="off"
              data-clean={clean ? "true" : undefined}
            >
              <span className="gr-act">
                {drop.action} · {clean ? "the closest call in this run" : "also proposed by the model"}
              </span>
              <p className="gr-rogue-text">{drop.text}</p>
              <span className="gr-cite">{drop.src[0]}</span>
              <div className="gr-verdict">
                <div className="gr-meter">
                  <span className="gr-meter-fill" />
                  <span className="gr-meter-bar" style={{ left: `${ground.threshold * 100}%` }} />
                </div>
                <div className="gr-verdict-row">
                  <span>
                    meaning match <b>{drop.score.toFixed(2)}</b>, needs {ground.threshold.toFixed(2)}
                  </span>
                  {noSharedTerm && <span>shared words: none</span>}
                </div>
              </div>
              <span className="gr-stamp">{clean ? "Kept" : "Dropped"}</span>
            </div>
          </div>

          <svg className="gr-lines" aria-hidden>
            {pairs.map((p, i) => (
              <path className="gr-path" data-i={i} pathLength={1} key={p.src} />
            ))}
            <path className="gr-path gr-path-rogue" data-i="rogue" pathLength={1} />
          </svg>

          <div className="gr-src">
            {sentences.map((s) => (
              <div className="gr-sent" data-id={s.id} data-state="off" key={s.id}>
                <span className="gr-sid">{s.id}</span>
                <p>{s.text}</p>
              </div>
            ))}
          </div>
        </div>

        <div className="gr-foot">
          <p className="gr-rule">
            A step stays only if it <b>means what its sentence says</b> and <b>shares a real word</b> with it.
            Fail either and it never reaches your phone.
          </p>
          <p className="st-fine">
            Steps and match scores from one recorded run of the engine ({data.recording.model}).
          </p>
        </div>
      </div>
    </section>
  );
}
