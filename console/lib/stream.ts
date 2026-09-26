/**
 * Client for POST /v1/troubleshoot/stream.
 *
 * The route is a POST, so the browser's EventSource cannot be used: the body is read as a stream
 * and split on blank lines (data/fixtures/README.md). Wire format per frame:
 * `event: <stage>\ndata: {"stage","ms","summary","detail"}\n\n`.
 */

import type { StageEvent } from "@/lib/trace";

// 127.0.0.1, not localhost: uvicorn binds IPv4 only by default, and browsers may try `localhost`
// as IPv6 first and give up on a refused connection.
export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

/** Whether the viewer can reach the engine, from GET /health (503 while it loads its indexes). */
export type Health = "unknown" | "ready" | "starting" | "offline";

export async function checkHealth(signal?: AbortSignal): Promise<Health> {
  try {
    const res = await fetch(`${API_URL}/health`, { cache: "no-store", signal });
    return res.ok ? "ready" : res.status === 503 ? "starting" : "offline";
  } catch {
    return "offline";
  }
}

export interface StreamRequest {
  query: string;
  siis_response: unknown;
}

/**
 * Stream one request, calling `onEvent` for every frame as it arrives.
 *
 * `mock` asks the mock replay for a cache hit (`exact` / `semantic`). The live engine ignores it
 * and lets its own cache decide, so a preset can always send it without faking a result.
 */
export async function streamTroubleshoot(
  body: StreamRequest,
  onEvent: (ev: StageEvent<Record<string, unknown>>) => void,
  { mock, signal }: { mock?: "exact" | "semantic"; signal?: AbortSignal } = {},
): Promise<void> {
  const url = `${API_URL}/v1/troubleshoot/stream${mock ? `?mock=${mock}` : ""}`;
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);

  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += value;
    for (let i = buf.indexOf("\n\n"); i >= 0; i = buf.indexOf("\n\n")) {
      const frame = buf.slice(0, i);
      buf = buf.slice(i + 2);
      const data = frame.split("\n").find((l) => l.startsWith("data: "));
      if (data) onEvent(JSON.parse(data.slice(6)));
    }
  }
}
