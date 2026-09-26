"use client";

import { useRef } from "react";
import { ScrollSmoother, gsap, useGSAP } from "@/lib/gsap";

export function Finale() {
  const root = useRef<HTMLElement>(null);

  useGSAP(
    () => {
      const mm = gsap.matchMedia();
      mm.add("(prefers-reduced-motion: no-preference)", () => {
        const $ = gsap.utils.selector(root);
        // The arch rises exactly as fast as the reader scrolls, from below the fold to its full
        // height at the very bottom of the page. ScrollSmoother supplies the smoothing, so the
        // scrub itself is direct.
        gsap
          .timeline({
            defaults: { ease: "none" },
            scrollTrigger: { trigger: root.current, start: "top bottom", end: "bottom bottom", scrub: true },
          })
          .fromTo($(".fin-arch"), { yPercent: 62, scaleX: 0.82 }, { yPercent: 0, scaleX: 1, duration: 1 }, 0)
          .fromTo($(".fin-title"), { yPercent: 50, opacity: 0 }, { yPercent: 0, opacity: 1, duration: 0.4 }, 0.5)
          .fromTo($(".fin-cta"), { y: 40, opacity: 0 }, { y: 0, opacity: 1, duration: 0.3 }, 0.7);
      });
    },
    { scope: root },
  );

  const toLive = (e: React.MouseEvent) => {
    const smoother = ScrollSmoother.get();
    if (!smoother) return;
    e.preventDefault();
    smoother.scrollTo("#live", true, "top top");
  };

  return (
    <section className="fin" data-nav="light" ref={root}>
      <div className="fin-arch">
        <div className="fin-copy">
          <h2 className="fin-title">
            One complaint.
            <br />
            One click.
          </h2>
          <div className="fin-cta">
            <a className="pill pill-navy" href="#live" onClick={toLive}>
              Try your own complaint
            </a>
          </div>
        </div>
      </div>
      <footer className="fin-foot">
        <span>OneClick · Samsung PRISM GenAI Hackathon 2026 · Theme 2</span>
        <span>
          The walkthrough replays one run of the engine, recorded with eval/tools/record_story.py: its
          timings, token counts and scores are that run&apos;s own. Try it live runs the engine itself.
        </span>
      </footer>
    </section>
  );
}
