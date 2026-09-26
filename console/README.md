# OneClick Console

Next.js 16 (App Router, Turbopack) + React 19 + Tailwind v4 + TypeScript + GSAP 3.

```bash
cd console
npm install
npm run dev          # http://localhost:3000
npm run lint         # eslint
npm run typecheck    # tsc --noEmit
npm run build
```

One page, `/`. It tells one recorded request section by section as the reader scrolls (the
complaint, both LLM calls with prompt in and response out, grounding with the step that gets thrown
out, the resolver, the phone opening Settings, the compiled response and its checks, the cache), then
hands over to **Try it live**, which streams the real API, and ends on what is measured today.

The live section calls `POST /v1/troubleshoot/stream` at `NEXT_PUBLIC_API_URL` (default
`http://127.0.0.1:8000`, not `localhost`: uvicorn binds IPv4 only and browsers may try IPv6 first).
If the API is switched to its mock replay (`settings.stream_mock`, off by default), every frame
carries `detail.mock` and the section shows a "Mock replay" badge instead of passing a replay off as live.
Its presets are real requests from `data/fixtures/`, `console/recordings/` and `eval/sets/`.

```bash
cd api && uvicorn app.main:app      # in one terminal
cd console && npm run dev           # in another
```

Or both at once, as built images: `docker compose up --build` from the repo root (below).

The section polls `GET /health` from the moment the page loads until the engine answers, so the
badge reads *Engine ready*, *Engine waking up* (503 while the API loads its indexes, or a hosted API
coming out of sleep) or *Engine offline* before anyone clicks.

## Running it with the API

`docker compose up --build` from the repo root builds [Dockerfile](Dockerfile) next to the API and
serves the site on port 3000 (`ONECLICK_CONSOLE_PORT` to change it). The build context is the repo
root because the page is prerendered from files outside `console/`: the fixtures' requests, the prompt files,
`api/app/config.py`, the catalog, the gold labels, `eval/sets/` and `docs/metrics.md`. The root
`.dockerignore` excludes `docs/` and `eval/` except for those two; add an exception there if the page
starts reading another file. `output: "standalone"` gives the image a self-contained server, and
because `turbopack.root` is the repo it lands at `.next/standalone/console/server.js`.

`NEXT_PUBLIC_API_URL` is compiled into the page, and the viewer's **browser** calls it, not the
container. So the compose default (`http://127.0.0.1:8000`) is right for anyone running the stack
on their own machine; to serve the site against an API elsewhere, build with
`ONECLICK_PUBLIC_API_URL=https://… docker compose build console`.

## Deploying the site on Vercel

The site is fully static, so Vercel serves it as it is. In the project settings:

- **Root Directory** `console`, with *Include files outside the root directory* on (the build reads
  the repo, see above).
- **Environment variable** `NEXT_PUBLIC_API_URL` = the API's public **https** address. A page served
  over https cannot call an `http://` API (mixed content), so it must be https, and it must be set
  before the build: changing it later needs a redeploy.

The API itself does not belong on Vercel: it keeps its cache in memory and in SQLite (every instance
would start cold and repeat queries would stop hitting), it answers a request and then finishes the
background variations call, and with the embedding model it needs about 440 MB of memory. It wants
one long-lived container: run `api/Dockerfile` on a container host.

## How it is wired today

The walkthrough sections replay one real run of the engine, not a hand-made one: `console/recordings/`
holds the touch-lag complaint's cold run (every stage frame, plus the background variations call),
the same words again (exact hit), a held-out paraphrase (semantic hit) and a two-problem complaint.
`python eval/tools/record_story.py` writes them from the shipping pipeline with the real keys, on a
throwaway cache; rebuild the site afterwards. `about.json` next to each run says when, at which
commit, with which model, and how many cold tries it took to get the primary model's answer (the
free tier sometimes answers with the fast model or not at all).

`data/fixtures/` is a different thing: the engine tests' hand-built contract, whose README marks its
timings and scores as illustrative. The page no longer reads it, except for the live presets'
request bodies.

## The story page

`app/page.tsx` is a server component. At build time it reads the LLM prompt files from
`api/app/llm/prompts/` (the highest `*.vN.md` of each) and the eval sets, then hands plain JSON to
`components/story/Story.tsx`. So the prompt panel always shows exactly what the engine sends; while
a prompt file is still a `TODO` stub the panel says "not written yet" rather than inventing text.
Every other number comes from the recorded run (`lib/story.ts`) or is counted from the eval sets
and read from `docs/metrics.md` (`lib/story.server.ts`).

Motion is GSAP: ScrollSmoother for the scroll, ScrollTrigger pins for the model, grounding and phone
sections, SplitText for the hero. Three traps cost time here, so avoid them:

- **Never put a CSS `transition` on `transform` or `opacity` of anything GSAP animates.** React dev
  mode mounts effects twice; GSAP reverts between the two, the CSS transition starts easing back,
  and the second run reads the half-finished value as the element's resting state.
- **Use `opacity`, not `autoAlpha`, for `.to()` tweens inside a scrubbed timeline** whose element
  sits in a parent hidden by an entrance animation. `autoAlpha` records a start of 0 from any
  element whose parent is `visibility: hidden`, so the element never comes back.
- **A hidden browser tab or pane pauses `requestAnimationFrame`,** which stops GSAP's clock and
  scroll events. Frozen animations in a background tab are not a bug in the page.

## Layout

| Path | What it is |
| --- | --- |
| `lib/trace.ts` | Types for the SSE stage events, plus which stages are LLM calls (`LLM_STAGES`). |
| `lib/plan.ts` | The official response shape (`ContextDeeplinkResponse`), as far as the page reads it. |
| `lib/checks.ts` | The graded output rules, a TypeScript mirror of `eval/evalkit/checks.py`. Keep them in step. |
| `lib/stream.ts` | The SSE client for the live section. |
| `app/story.css` | The story page's palette, type and every section's layout. |
| `lib/story.ts`, `lib/story.server.ts` | Story data: derived from `recordings/`, plus build-time reads of the prompt files, config.py, the eval sets and metrics.md. |
| `lib/gsap.ts` | GSAP with its plugins registered once, and the two breakpoints every section animates for. |
| `components/story/` | One component per story section, plus `Galaxy` (a CSS handset) and its One UI `Screens`. |

## Rules this UI follows

- **One colour, one meaning.** The story page uses a Samsung palette: lime is the punch colour
  (proven things and calls to action), pink is only ever time inside an LLM call, red is only ever
  refused, amber is disruptive. If a colour ever means two things, the demo is lying.
- **Every number on screen is real.** The timings, scores, coverage and counts come from the
  fixture, never from a literal typed into a component. Judges see these same numbers in the video.
- **`ms` on a stage event is that stage's own duration**, not elapsed time since the request
  started; `done.ms` is the total. Differencing consecutive events is wrong.
- **The dropped step gets its own beat.** A step the model proposed and the engine refused is the
  strongest claim the product makes, so the grounding section stops on it (the cited sentence, the
  failed match, the stamp) before throwing it out.
- **Every number says who measured it.** Resolver and cache figures from the mapping lane are
  labelled as such, and anything that needs the whole engine running says pending.

## Recording

Built for 1440×900 and checked at 1920×1080. `devIndicators` is off so the Next badge never
appears in a capture, and pane scrollbars are hidden for the same reason. Motion respects `prefers-reduced-motion`: the replay
skips its staging and every final value still renders.
