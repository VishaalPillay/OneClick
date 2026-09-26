/**
 * The graded output rules, run over a response body at build time.
 *
 * A TypeScript mirror of eval/evalkit/checks.py (the gate replica's checker), so the page and the
 * gates judge a plan the same way. Keep the two in step: if a rule changes there, change it here.
 */

import type { ResponseBody } from "@/lib/plan";

// FAQ Q10 form, strict: the sentence ends with a period (still an open question with the organisers).
const GOAL = /^Follow these steps to perform this (.+?) (Troubleshooting|Configuration)\.$/;

const URL_PATTERNS: Record<string, RegExp> = {
  scheme: /\b[a-z][a-z0-9+.-]{1,20}:\/\//i,
  www: /\bwww\./i,
  domain: /\b[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*\.(?:com|net|org|gov|edu|info|io|ly|html?|php|aspx?|jsp)\b/i,
  email: /[a-z0-9._%+-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+/i,
  markdown_link: /\[[^\]\n]*\]\([^)\n]*\)/,
  markdown_image: /!\[/,
  html_link: /<\s*(?:a|img|link|iframe)\b|\bhref\s*=|\bsrc\s*=/i,
};
const CATALOG_URI = /^bixby:\/\/[A-Za-z0-9_./-]+$/;
const DEEPLINK_FIELD = /\.(?:actionableDeeplink|validationDeeplink)\.deeplink$/;

const words = (s: string) => s.split(/\s+/).filter(Boolean);

export interface CatalogEntry {
  id?: string;
  deeplink: string;
  description?: string;
  message?: string;
  originalType?: string;
}

export interface Check {
  rule: string;
  detail: string;
  pass: number;
  total: number;
  /** A rule about something that must be absent: shown as "none found", not as a ratio. */
  absence?: boolean;
}

/** Every string in the body with its JSON path, for the zero-URL rule. */
function strings(value: unknown, path: string, out: [string, string][]) {
  if (typeof value === "string") out.push([path, value]);
  else if (Array.isArray(value)) value.forEach((v, i) => strings(v, `${path}[${i}]`, out));
  else if (value && typeof value === "object") {
    for (const [k, v] of Object.entries(value)) strings(v, `${path}.${k}`, out);
  }
  return out;
}

export function checkBody(body: ResponseBody, catalog: CatalogEntry[]): Check[] {
  const goals = body.contexts;
  const actions = goals.flatMap((g) => g.actions);
  const steps = actions.flatMap((a) => a.stepGroups.flatMap((s) => s.steps));
  const links = actions.flatMap((a) => a.stepGroups.map((s) => s.actionableDeeplink).filter((d) => d !== null));
  const byUri = new Map(catalog.map((c) => [c.deeplink, c]));

  const leaks = strings({ contexts: body.contexts }, "", []).filter(([path, text]) => {
    if (DEEPLINK_FIELD.test(path) && CATALOG_URI.test(text)) return false;
    return Object.values(URL_PATTERNS).some((rx) => rx.test(text));
  });

  const descOk = actions.filter((a) => {
    const n = words(a.description).length;
    return a.description.startsWith("It will") && n >= 5 && n <= 7;
  }).length;

  const verbatim = links.filter((d) => {
    const entry = byUri.get(d.deeplink);
    return Boolean(entry && entry.originalType === d.originalType);
  }).length;

  return [
    {
      rule: "Goal sentence",
      detail: "“Follow these steps to perform this {Topic} Troubleshooting.”",
      pass: goals.filter((g) => GOAL.test(g.goal)).length,
      total: goals.length,
    },
    {
      rule: "Title",
      detail: "Two or three words",
      pass: goals.filter((g) => words(g.title).length >= 2 && words(g.title).length <= 3).length,
      total: goals.length,
    },
    {
      rule: "Description",
      detail: "“It will” plus five to seven words in all",
      pass: descOk,
      total: actions.length,
    },
    {
      rule: "Steps",
      detail: "Imperative, one sentence, trailing period, no numbering",
      pass: steps.filter((s) => s.trim().endsWith(".") && !/^\s*(\d+[.):-]|step\s*\d)/i.test(s)).length,
      total: steps.length,
    },
    {
      rule: "Catalog links",
      detail: "Copied from the catalog exactly, never written by the model",
      pass: verbatim,
      total: links.length,
    },
    {
      rule: "Zero URLs",
      detail: "No link, domain or email in any string outside a deeplink field",
      pass: leaks.length === 0 ? 1 : 0,
      total: 1,
      absence: true,
    },
  ];
}
