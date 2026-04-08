/**
 * sseStream.ts — SSE Stream Helper mit Sleep Detection + Liveness Timeout (#478)
 *
 * Wrapper um fetch + ReadableStream mit:
 * - Liveness Timeout: kein Event seit 45s → Stream als tot behandeln
 * - Sleep Detection: reader.read() dauert >60s → Connection Reset
 * - Permanent Error Detection: 401/403/404 → kein Retry-Versuch
 */

const LIVENESS_TIMEOUT_MS = 45_000;  // 45s ohne Event → tot
const SLEEP_GAP_MS        = 60_000;  // 60s+ Pause → Sleep

export type SSEEvent =
  | { type: "text"; text: string }
  | { type: "tool_call"; tool_call: string; tool_input?: Record<string, unknown>; tool_detail?: string }
  | { type: "tool_image"; tool_image: string; tool_name?: string }
  | { type: "context_info"; system_tokens: number; history_tokens: number; tool_tokens: number; history_messages: number; history_budget: number }
  | { type: "info"; info: string }
  | { type: "tool_warning"; tool_warning: string; tool_name: string }
  | { type: "done"; usage?: { input: number; output: number; cache_read?: number; cache_write?: number; rounds?: number }; is_fallback?: boolean; model?: string }
  | { type: "suggestions"; suggestions: string[] }
  | { type: "error"; error: string; session_reset?: boolean };

export interface SSEStreamOptions {
  url: string;
  body: Record<string, unknown>;
  signal?: AbortSignal;
  onEvent: (evt: SSEEvent) => void;
  onConnectionLost?: () => void;
}

/** Permanente HTTP-Fehler die kein Retry verdienen */
function isPermanentError(status: number): boolean {
  return status === 401 || status === 403 || status === 404;
}

/**
 * Startet einen SSE-Stream mit Liveness-Monitoring.
 * Throws bei Fehler. Resolved wenn Stream normal endet (evt.done).
 */
export async function sseStream(opts: SSEStreamOptions): Promise<void> {
  const token = localStorage.getItem("hydrahive_token") || "";
  const res = await fetch(opts.url, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify(opts.body),
    signal: opts.signal,
  });

  if (!res.ok) {
    const e = await res.json().catch(() => ({ detail: res.statusText }));
    const err = new Error(e.detail || `HTTP ${res.status}`);
    (err as any).status = res.status;
    (err as any).permanent = isPermanentError(res.status);
    throw err;
  }

  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  // Liveness: Timer der resettet wird bei jedem Event
  let livenessTimer: ReturnType<typeof setTimeout> | null = null;
  let dead = false;

  const resetLiveness = () => {
    if (livenessTimer) clearTimeout(livenessTimer);
    livenessTimer = setTimeout(() => {
      dead = true;
      opts.onConnectionLost?.();
      reader.cancel().catch(() => {});
    }, LIVENESS_TIMEOUT_MS);
  };

  resetLiveness();

  try {
    while (true) {
      const readStart = Date.now();
      const { done, value } = await reader.read();
      const readDuration = Date.now() - readStart;

      if (done || dead) break;

      // Sleep Detection: read() dauerte >60s → Connection war eingeschlafen
      if (readDuration > SLEEP_GAP_MS) {
        opts.onConnectionLost?.();
        break;
      }

      resetLiveness();
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() ?? "";

      for (const part of parts) {
        for (const line of part.split("\n")) {
          if (!line.startsWith("data: ")) continue;
          try {
            const raw = JSON.parse(line.slice(6));
            if (raw.text !== undefined) {
              opts.onEvent({ type: "text", text: raw.text });
            } else if (raw._context_info !== undefined) {
              const ci = raw._context_info;
              opts.onEvent({ type: "context_info", system_tokens: ci.system_tokens ?? 0, history_tokens: ci.history_tokens ?? 0, tool_tokens: ci.tool_tokens ?? 0, history_messages: ci.history_messages ?? 0, history_budget: ci.history_budget ?? 0 });
            } else if (raw.info !== undefined) {
              opts.onEvent({ type: "info", info: raw.info });
            } else if (raw.tool_warning !== undefined) {
              opts.onEvent({ type: "tool_warning", tool_warning: raw.tool_warning, tool_name: raw.tool_name ?? "" });
            } else if (raw.tool_image !== undefined) {
              opts.onEvent({ type: "tool_image", tool_image: raw.tool_image, tool_name: raw.tool_name });
            } else if (raw.tool_call !== undefined) {
              opts.onEvent({ type: "tool_call", tool_call: raw.tool_call, tool_input: raw.tool_input, tool_detail: raw.tool_detail });
            } else if (raw.done) {
              opts.onEvent({ type: "done", usage: raw.usage, is_fallback: raw.is_fallback, model: raw.model });
              // Suggestions may follow — stream continues until server closes
            } else if (raw.suggestions) {
              opts.onEvent({ type: "suggestions", suggestions: raw.suggestions });
            } else if (raw.error) {
              opts.onEvent({ type: "error", error: raw.error, session_reset: raw.session_reset });
              return;
            }
          } catch (parseErr) {
            if (parseErr instanceof Error && parseErr.message !== "Unexpected end of JSON input") throw parseErr;
          }
        }
      }
    }
  } finally {
    if (livenessTimer) clearTimeout(livenessTimer);
  }
}
