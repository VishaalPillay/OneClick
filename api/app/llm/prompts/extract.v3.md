# Stage 2 — Extraction prompt (v3: select sentences, exact screens, compact JSON)

You pick troubleshooting actions for one customer's complaint from a Samsung support article. The
article is split into numbered sentences. You do **not** write the steps: for each action you list
the ids of the article sentences that are its steps, and the engine uses those sentences word for
word. Return JSON that matches the schema, written compactly on one line: no indentation and no
line breaks.

## Rules

- Only choose sentences that tell the reader to do something (tap, go to, press, turn on, contact...)
  and that help with this complaint. Skip background, explanations and unrelated sections.
- If the article does not address the complaint at all, return an empty `goals` list. Never add
  anything that is not in the article.
- `goals`: one per distinct problem in the complaint, 1 to 3. Split only separate problems ("the
  screen is black **and** the battery drains"); symptoms of one problem are one goal.
  - `problem`: one English sentence describing that problem (fix typos, translate other languages).
  - `title`: 2 or 3 words, sentence case, naming the problem ("Touchscreen input lag").
  - `topic`: 2 to 4 words in Title Case naming the problem area, not ending in "Troubleshooting"
    ("Touchscreen Issues").
  - `domain`: Battery, Display, Camera, Performance or Other.
- `actions`: the separate fixes, at most {{max_actions}} per goal (keep the ones that matter most for
  this complaint), each with:
  - `ids`: every sentence id the action needs, in the order to follow them, from the one that
    starts it to the one that finishes it. A restart is "Press and hold the Power button, then tap
    Restart" **and** "Tap Restart again to confirm", never the confirmation alone.
  - `name`: 2 to 5 words, Title Case, starting with a verb ("Turn Off Full Screen Gestures").
  - `desc`: "It will" plus 3 to 5 words ("It will switch to navigation buttons").
  - `path`: the exact Settings screen or setting the steps end on, as
    `Settings > Menu > Setting` with the article's own names. Go all the way down to the setting the
    steps change: "go to Settings, tap Display, then tap the switch next to Touch sensitivity" is
    `Settings > Display > Touch sensitivity`, not `Settings > Display`. Use "" only when the steps
    do not open the Settings app at all (a physical step, another app, a service centre).
  - `verb`: enable, disable, set, open, check, restart, reset, visit or none.
- Cover every fix the article gives for this problem, not only the first few: physical checks
  (damage, liquid, charger, accessories), settings changes, app fixes, restart, safe mode, software
  update, factory reset and contacting support. Leave out only what is about a different problem.
  Order does not matter.

## Complaint

{{query}}

## Article (numbered sentences, grouped by section)

{{sentences}}
