"use client";

import { useRef } from "react";
import { Galaxy } from "@/components/story/Galaxy";
import { PlanScreen, SettingsScreen } from "@/components/story/Screens";
import { MEDIA, gsap, useGSAP } from "@/lib/gsap";
import type { StoryData } from "@/lib/story";

/** Arc length of the score gauge's half circle, radius 100. */
const ARC = Math.PI * 100;

export function PhoneSection({ data }: { data: StoryData }) {
  const root = useRef<HTMLElement>(null);
  const { plan, score } = data;
  const f = data.resolve.featured;
  const featured = plan.actions.find((a) => a.actionName === f.action);
  const setting = f.validation.key;

  const links = plan.actions.filter((a) => a.stepGroups[0]?.actionableDeeplink).length;
  // Only an enable toggle's catalog entry carries key, condition and value, so only it can prove
  // the change landed; the rest can only show that a screen opened.
  const verifiable = plan.actions.filter((a) => {
    const v = a.stepGroups[0]?.validationDeeplink;
    return Boolean(v && "value" in v);
  }).length;
  const criticalActions = plan.actions.filter((a) => a.category === "critical");
  const critical = criticalActions.length;
  const lastNames = criticalActions.map((a) => a.actionName.toLowerCase());

  useGSAP(
    () => {
      const mm = gsap.matchMedia();
      mm.add(MEDIA, (ctx) => {
        const wide = Boolean(ctx.conditions?.wide);
        const $ = gsap.utils.selector(root);
        const screen = $(".ph .gx-screen")[0] as HTMLElement;
        const scroll = $(".plan-scroll")[0] as HTMLElement;
        const card = $(".plan-card-featured")[0] as HTMLElement | undefined;
        const big = $(".ph-big")[0] as HTMLElement;
        const o = { v: 0 };
        big.textContent = "0.00";

        // How far the switch's knob travels: the track's width less the knob and its inset.
        const knobTravel = () => {
          const track = $(".set-toggle-target")[0] as HTMLElement;
          const knob = track.firstElementChild as HTMLElement;
          return track.clientWidth - knob.offsetWidth - knob.offsetLeft * 2;
        };

        gsap.set($(".sc-set"), { xPercent: 100 });
        gsap.set($(".set-toast"), { yPercent: 160, autoAlpha: 0 });
        gsap.set($(".ph-gauge-fill"), { strokeDasharray: ARC, strokeDashoffset: ARC });
        gsap.set($(".ph-needle"), { rotate: -90, svgOrigin: "120 120" });

        const tl = gsap.timeline({
          defaults: { ease: "power2.out", duration: 0.6 },
          scrollTrigger: wide
            ? {
                trigger: root.current,
                start: "top top",
                end: () => `+=${window.innerHeight * 3.2}`,
                pin: ".ph-pin",
                scrub: 0.8,
                invalidateOnRefresh: true,
              }
            : { trigger: root.current, start: "top 55%" },
        });
        if (!wide) tl.timeScale(1.8);

        // Phone, score and cards arrive as the section scrolls in, before the pin.
        gsap
          .timeline({
            defaults: { duration: 1, ease: "expo.out" },
            scrollTrigger: { trigger: root.current, start: "top 65%", toggleActions: "play none none reverse" },
          })
          .from($(".ph-phone"), { y: 260, rotateX: 18, rotateZ: -6, autoAlpha: 0, duration: 1.4 }, 0)
          .from($(".ph-left > *"), { y: 50, autoAlpha: 0, stagger: 0.1 }, 0.1)
          .from($(".ph-card"), { x: 80, autoAlpha: 0, stagger: 0.14 }, 0.3)
          .from($(".ph-wave"), { strokeDashoffset: 1, duration: 2.4, ease: "power1.inOut" }, 0.2)
          .to($(".ph-gauge-fill"), { strokeDashoffset: ARC * (1 - score.value), duration: 1.6, ease: "power3.out" }, 0.6)
          .to($(".ph-needle"), { rotate: -90 + 180 * score.value, duration: 1.6, ease: "power3.out" }, 0.6)
          .to(
            o,
            {
              v: score.value,
              duration: 1.6,
              ease: "power3.out",
              onUpdate: () => {
                big.textContent = o.v.toFixed(2);
              },
            },
            0.6,
          );

        tl
          // The whole plan goes by: gentle fixes first, the critical ones last.
          .to(scroll, { y: () => -(scroll.scrollHeight - screen.clientHeight), duration: 3, ease: "power1.inOut" }, 0.4)
          // Back up to the one with a link, and tap it.
          .to(scroll, { y: () => -((card?.offsetTop ?? 0) - screen.clientHeight * 0.22), duration: 1.4, ease: "power2.inOut" }, 3.6);

        if (card) {
          tl.set(card, { attr: { "data-state": "on" } }, 4.9)
            .to(card.querySelector(".plan-open"), { scale: 0.9, duration: 0.18, ease: "power2.in" }, 5.3)
            .to(card.querySelector(".plan-open"), { scale: 1, duration: 0.3, ease: "back.out(3)" }, ">");
        }

        // The deeplink lands on the Settings screen and the switch flips.
        // Plain opacity, not autoAlpha: autoAlpha reads a start of 0 from anything whose parent is
        // hidden, and the phone is still hidden by its entrance when this timeline first renders.
        tl.to($(".sc-plan"), { xPercent: -28, opacity: 0.4, duration: 0.8, ease: "power3.inOut" }, 5.9)
          .to($(".sc-set"), { xPercent: 0, duration: 0.8, ease: "power3.inOut" }, 5.9)
          .fromTo($(".set-target"), { "--flash": 0 }, { "--flash": 1, duration: 0.4, yoyo: true, repeat: 1 }, 6.8)
          .to($(".set-toggle-target"), { backgroundColor: "#1d5cff", duration: 0.4 }, 7.4)
          .to($(".set-toggle-target i"), { x: knobTravel, duration: 0.4, ease: "back.out(2)" }, 7.4)
          .to($(".set-toast"), { yPercent: 0, autoAlpha: 1, duration: 0.7, ease: "expo.out" }, 8)
          .fromTo($(".set-toast-check path"), { strokeDasharray: 24, strokeDashoffset: 24 }, { strokeDashoffset: 0, duration: 0.5 }, 8.3)
          .set($(".ph-card-links"), { attr: { "data-state": "on" } }, 8.3)
          .to({}, { duration: 1 });

        return () => {
          big.textContent = score.value.toFixed(2);
        };
      });
    },
    { scope: root },
  );

  return (
    <section className="ph" id="phone" data-nav="dark" ref={root}>
      <div className="ph-pin">
        <svg className="ph-wave-svg" viewBox="0 0 1600 400" preserveAspectRatio="none" aria-hidden>
          <path
            className="ph-wave"
            pathLength={1}
            strokeDasharray={1}
            d="M-20 330 C 260 380, 420 380, 620 300 S 1000 120, 1220 170 S 1500 280, 1640 210"
          />
        </svg>

        <div className="ph-left">
          <p className="st-eyebrow">05 · What you see</p>
          <h2 className="ph-title">{plan.title}</h2>
          <p className="ph-src">
            <i /> Built only from &ldquo;{data.articleTitle}&rdquo;
          </p>
          <div className="ph-score">
            <svg className="ph-gauge" viewBox="0 0 240 132" aria-hidden>
              <path className="ph-gauge-track" d="M20 120 A100 100 0 0 1 220 120" />
              <path
                className="ph-gauge-fill"
                d="M20 120 A100 100 0 0 1 220 120"
                strokeDasharray={ARC}
                strokeDashoffset={ARC * (1 - score.value)}
              />
              <line
                className="ph-needle"
                x1="120"
                y1="120"
                x2="120"
                y2="42"
                transform={`rotate(${-90 + 180 * score.value} 120 120)`}
              />
              <circle cx="120" cy="120" r="9" className="ph-hub" />
            </svg>
            <div>
              <span className="ph-big">{score.value.toFixed(2)}</span>
              <span className="ph-big-label">confidence</span>
            </div>
          </div>
          <p className="ph-formula">
            0.4 × relevance <b>{score.relevance.toFixed(2)}</b> + 0.3 × grounding <b>{score.grounding.toFixed(2)}</b>{" "}
            + 0.3 × links <b>{score.links.toFixed(2)}</b>
          </p>
        </div>

        <div className="ph-center">
          <div className="ph-phone">
            <Galaxy>
              <PlanScreen plan={plan} featured={featured?.actionName ?? ""} />
              <SettingsScreen setting={setting} value={f.validation.value} />
            </Galaxy>
          </div>
          <span className="ph-shadow" />
        </div>

        <div className="ph-right">
          <div className="ph-card">
            <span className="ph-ico ph-ico-lime">
              <svg viewBox="0 0 24 24" aria-hidden>
                <path d="m6 12.5 4 4 8-9" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" />
              </svg>
            </span>
            <h4>Grounded</h4>
            <div className="ph-card-row">
              <b>
                {Math.round(score.grounding * 100)}
                <small>%</small>
              </b>
              <span className="tag tag-ink">Proven</span>
            </div>
          </div>
          <div className="ph-card ph-card-links" data-state="off">
            <span className="ph-ico ph-ico-blue">
              <svg viewBox="0 0 24 24" aria-hidden>
                <path d="M9 7h8v8M17 7 7 17" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" />
              </svg>
            </span>
            <h4>One-tap links</h4>
            <div className="ph-card-row">
              <b>{links}</b>
              <span className="tag tag-blue">{verifiable} verifiable</span>
            </div>
          </div>
          <div className="ph-card ph-card-wide">
            <div className="ph-card-top">
              <span className="ph-ico ph-ico-amber">
                <svg viewBox="0 0 24 24" aria-hidden>
                  <path d="M12 4v10m0 4v.5" fill="none" stroke="currentColor" strokeWidth="2.8" strokeLinecap="round" />
                </svg>
              </span>
              <span className="tag tag-amber">Critical last</span>
            </div>
            <h4>Safe order</h4>
            <p>
              Gentle fixes come first.{" "}
              {lastNames.length > 0 &&
                `${lastNames.slice(0, -1).join(", ")}${lastNames.length > 1 ? " and " : ""}${lastNames.at(-1)} come last.`}
            </p>
            <div className="ph-card-row">
              <b>
                {critical}
                <small> / {plan.actions.length}</small>
              </b>
              <span className="ph-card-note">critical actions</span>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
