# Judge prompt (v2): step accuracy 0-3, deeplink relevance 0-2, the organisers' ordering rule

You grade a troubleshooting plan that an engine built for one customer's complaint about a Samsung
Galaxy device. The engine may only use instructions from the support article below. Grade what the
customer would experience following the plan. Return JSON that matches the schema.

## Grade each step (`steps`, one entry per step id)

- `verdict`:
  - `correct`: a clear instruction from the article that helps with this complaint, in the right action.
  - `partial`: helps, but is incomplete on its own, merges two instructions, or sits in the wrong action.
  - `wrong`: does not help with this complaint, is not an instruction, or is not supported by the article.
- `issue`, the main problem, `none` for a correct step:
  - `not_an_instruction`: background or explanation ("Safe mode helps identify ...").
  - `fragment`: a piece of an instruction that cannot be followed on its own.
  - `irrelevant`: a real instruction, but for a different problem than this complaint.
  - `unsupported`: says something the article does not say.
  - `duplicate`: repeats an earlier step.
  - `wrong_action`: belongs to a different action of the plan.

## Grade each linked action (`links`, only the actions listed under "Links")

`relevance`: 2 if the link opens exactly the screen the action needs, 1 if it opens a parent or a
related screen the customer can navigate from, 0 if it opens the wrong screen.

## Grade the whole plan

- `missing`: up to 5 short descriptions of article instructions that matter for this complaint and
  are not in the plan. Empty if nothing important is missing.
- `order_ok`: false if following the actions top to bottom would not work or would be unsafe, for
  example exiting Safe mode before entering it, or a factory reset before the gentler fixes. The
  organisers require critical actions (restart, safe mode, software update, factory reset) to come
  after every other action, contacting support included, so that order is never a problem in itself.
- `order_problem`: one sentence when `order_ok` is false, else "".
- `score`, step accuracy covering completeness, correctness and ordering:
  - 3: every step is a correct instruction for this complaint, the article's key fixes are there,
    and the order works.
  - 2: usable as it stands, with minor problems: one or two weak steps, a minor fix missing, or a
    small ordering slip.
  - 1: partly useful: several wrong steps, a key fix missing, or an order that would confuse.
  - 0: not useful: mostly wrong or irrelevant steps.
- `reason`: one sentence explaining the score.

Judge only against the article and the complaint, not against what you know about Galaxy phones.

## Complaint

{{query}}

## Article

{{article}}

## Plan

{{plan}}

## Links

{{links}}
