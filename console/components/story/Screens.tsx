import type { PlanContext } from "@/lib/plan";
import { Wallpaper } from "@/components/story/Galaxy";
import { Mark } from "@/components/story/Logo";

/**
 * The One UI screens the story's phone shows. Static markup only: the sections animate them by
 * class name, so the same screen can be scrubbed by the scroll on desktop and played once on a
 * phone-width window.
 */

const CATEGORY_LABEL: Record<string, string> = { auto: "One tap", manual: "By hand", critical: "Critical" };

function Chip({ category }: { category: string }) {
  return <span className={`sc-chip sc-chip-${category}`}>{CATEGORY_LABEL[category] ?? category}</span>;
}

/** Hero: the complaint going in, the plan coming out. */
export function AskScreen({
  query,
  articleTitle,
  plan,
  steps,
}: {
  query: string;
  articleTitle: string;
  plan: PlanContext;
  steps: number;
}) {
  return (
    <div className="sc sc-ask">
      <div className="ask-lock">
        <Wallpaper />
        <span className="ask-lock-time">12:45</span>
      </div>
      <div className="sc-app">
        <Mark className="sc-mark" />
        OneClick
      </div>
      <h3 className="sc-large">What&apos;s wrong with your phone?</h3>

      <div className="ask-bubble">
        <span className="ask-typed">{query}</span>
      </div>

      <div className="ask-status">
        <span className="ask-spark" />
        <span>
          Reading <b>{articleTitle}</b>
        </span>
      </div>

      <div className="ask-result">
        <div className="ask-res-head">
          <span className="ask-res-kicker">Your fix</span>
          <b>{plan.title}</b>
          <span className="ask-res-meta">
            {plan.actions.length} actions · {steps} steps
          </span>
        </div>
        {plan.actions.slice(0, 3).map((a) => (
          <div className="ask-row" key={a.actionName}>
            <span className="ask-row-name">{a.actionName}</span>
            {a.category === "auto" ? <span className="ask-open">Open</span> : <Chip category={a.category} />}
          </div>
        ))}
      </div>
    </div>
  );
}

/** The plan, as the user receives it. `featured` is the action whose link the phone section taps. */
export function PlanScreen({ plan, featured }: { plan: PlanContext; featured: string }) {
  return (
    <div className="sc sc-plan">
      <div className="plan-scroll">
        <div className="sc-app">
          <Mark className="sc-mark" />
          OneClick
        </div>
        <div className="plan-head">
          <span className="plan-kicker">Your fix</span>
          <h3 className="sc-large">{plan.title}</h3>
          <p className="plan-goal">{plan.goal}</p>
        </div>

        {plan.actions.map((a, i) => {
          const group = a.stepGroups[0];
          const steps = a.stepGroups.flatMap((g) => g.steps);
          return (
            <div
              key={a.actionName}
              className={`plan-card${a.actionName === featured ? " plan-card-featured" : ""}`}
              data-index={i}
            >
              <div className="plan-card-top">
                <span className="plan-card-name">{a.actionName}</span>
                <Chip category={a.category} />
              </div>
              <p className="plan-card-desc">{a.description}</p>
              <ol className="plan-steps">
                {steps.map((s) => (
                  <li key={s}>{s}</li>
                ))}
              </ol>
              {group?.actionableDeeplink && (
                <span className="plan-open">
                  <span className="plan-open-label">{group.actionableDeeplink.message}</span>
                </span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/**
 * Where the catalog link lands: Settings › Display, with the target switch. The rows around it
 * are the real neighbours on a Galaxy's Display screen, so the jump reads as the actual app.
 */
export function SettingsScreen({ setting, value }: { setting: string; value?: string }) {
  return (
    <div className="sc sc-set">
      <div className="set-bar">
        <svg viewBox="0 0 24 24" className="set-back" aria-hidden>
          <path d="M15 5 8 12l7 7" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
        </svg>
        <h3>Display</h3>
      </div>

      <div className="set-group">
        <div className="set-row">
          <div>
            <b>Brightness</b>
            <span className="set-slider">
              <span />
            </span>
          </div>
        </div>
        <div className="set-row">
          <b>Eye comfort shield</b>
          <span className="set-toggle" />
        </div>
      </div>

      <div className="set-group">
        <div className="set-row">
          <b>Screen mode</b>
        </div>
        <div className="set-row">
          <b>Font size and style</b>
        </div>
        <div className="set-row">
          <b>Navigation bar</b>
        </div>
      </div>

      <div className="set-group">
        <div className="set-row">
          <b>Accidental touch protection</b>
          <span className="set-toggle set-toggle-on" />
        </div>
        <div className="set-row set-target">
          <div>
            <b>{setting}</b>
            <small>Increase the touch sensitivity of the screen for use with screen protectors.</small>
          </div>
          <span className="set-toggle set-toggle-target">
            <i />
          </span>
        </div>
      </div>

      <div className="set-toast">
        <span className="set-toast-check">
          <svg viewBox="0 0 24 24" aria-hidden>
            <path d="m6 12.5 4 4 8-9" fill="none" stroke="currentColor" strokeWidth="2.8" strokeLinecap="round" />
          </svg>
        </span>
        <span>
          <b>Verifiable</b> {setting} = {value ?? "True"}
        </span>
      </div>
    </div>
  );
}
