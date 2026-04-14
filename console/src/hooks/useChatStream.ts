/**
 * useChatStream — Shared Chat-Stream Hook für alle Chat-Pages (#491)
 *
 * Konsolidiert die identische Stream-Logik aus ChatPage, AgentChatPage, MyAgentPage.
 * Enthält: Message-State, SSE-Streaming, Tool-Handling, Suggestions, Debug-Events.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { sseStream, type SSEEvent } from "@/lib/sseStream";
import { api } from "@/lib/api";
import { useTranslation } from "react-i18next";

// ── Types ─────────────────────────────────────────────────────────────────────

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  workers?: string[];
  tokenUsage?: { input: number; output: number; rounds?: number; cache_write?: number; cache_read?: number };
  model?: string;
  isFallback?: boolean;
  ts?: string;
  _images?: string[];
  /** Tool-Call-ID (tc.id vom Backend) für tool_result Matching */
  toolCallId?: string;
  /** Tool-Output nach Ausführung — wird via tool_result Event gesetzt */
  toolResult?: string;
}

export interface DebugEvent {
  ts: number;
  type: string;
  data: Record<string, unknown>;
}

export interface UseChatStreamOptions {
  /** SSE stream endpoint, e.g. "/api/agents/{id}/message/stream" */
  streamEndpoint: string;
  /** History endpoint, e.g. "/api/agents/{id}/session/history" */
  historyEndpoint: string;
  /** Extra body params per request (e.g. execution_mode) */
  extraBodyParams?: Record<string, unknown>;
  /** Custom slash command handler. Return true if handled. */
  onSlashCommand?: (cmd: string, args: string) => boolean;
  /** Called after stream completes with the full response text */
  onStreamComplete?: (response: string) => void;
  /** Called before sending a message (e.g. for companion events) */
  onBeforeSend?: (content: string) => void;
  /** Called when user aborts (e.g. for extra interrupt API calls) */
  onAbort?: () => void;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

let _msgCounter = 0;
export function mkMsg(role: ChatMessage["role"], content: string, workers?: string[]): ChatMessage {
  return { id: `msg-${++_msgCounter}`, role, content, workers, ts: new Date().toISOString() };
}

// ── Hook ──────────────────────────────────────────────────────────────────────

export function useChatStream(opts: UseChatStreamOptions) {
  const { t } = useTranslation();

  // Message state
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");

  // UI state
  const [showEmoji, setShowEmoji] = useState(false);
  const [showSuggest, setShowSuggest] = useState(false);
  const [suggestIdx, setSuggestIdx] = useState(0);
  const [followUpChips, setFollowUpChips] = useState<string[]>([]);
  const [lightboxSrc, setLightboxSrc] = useState<string | null>(null);
  const [pendingImages, setPendingImages] = useState<{ data: string; media_type: string; preview: string }[]>([]);

  // Stream state
  const [activeTool, setActiveTool] = useState<{ name: string; detail: string } | null>(null);
  // #641: CONFIRM-Round-Trip — Liste pending Tool-Bestätigungen, dedupliziert per tool_call_id
  const [pendingConfirms, setPendingConfirms] = useState<{
    tool_call_id: string;
    tool_name:    string;
    tool_input?:  Record<string, unknown>;
    risk?:        string;
    session_id?:  string;
  }[]>([]);
  const [streamingMsgId, setStreamingMsgId] = useState<string | null>(null);
  const [doneMsgId, setDoneMsgId] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);

  // Debug
  const [debugEvents, setDebugEvents] = useState<DebugEvent[]>([]);

  // Coach
  const [coachEnabled, setCoachEnabled] = useState(() => localStorage.getItem("hh_prompt_coach") === "1");
  const [coachFeedback, setCoachFeedback] = useState<{ ok: boolean; suggestion?: string; reason?: string } | null>(null);
  const [coachChecking, setCoachChecking] = useState(false);

  // History
  const [showHistory, setShowHistory] = useState(false);
  const [sessions, setSessions] = useState<any[]>([]);
  const [viewSession, setViewSession] = useState<{ id: string; messages: ChatMessage[]; startedAt: string } | null>(null);
  // Aktuelle aktive Session-ID (vom /history-Endpoint geliefert) — für Auto-Resume
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null);

  // Refs
  const bottomRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const elapsedTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const userScrolledUp = useRef(false);
  const isAutoScrolling = useRef(false);  // verhindert false-positive Scroll-Events
  const clearedAt = useRef(0);            // Timestamp letztes /clear → loadHistory ignorieren

  // ── Chat-Cache: Messages aus sessionStorage nach Mount laden ──────────────
  // Eigener useEffect — feuert NACH dem ersten Render wenn DOM fertig ist.
  // Dadurch funktioniert scrollIntoView korrekt (Container hat Dimensionen).
  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(`hh_chat_${opts.historyEndpoint}`);
      if (raw) {
        const cached = JSON.parse(raw) as ChatMessage[];
        if (cached.length > 0) setMessages(cached);
      }
    } catch { /* ignore */ }
  }, [opts.historyEndpoint]);

  // ── Load History ──────────────────────────────────────────────────────────

  const loadHistory = useCallback(() => {
    if (!opts.historyEndpoint) return;
    // Nach /clear 3s nicht neu laden — sonst füllt loadHistory sofort wieder auf
    if (Date.now() - clearedAt.current < 3000) return;
    api.get<{ session_id: string | null; messages: any[]; count: number }>(opts.historyEndpoint)
      .then(d => {
        if (Date.now() - clearedAt.current < 3000) return;  // nochmal prüfen nach async
        if (d.session_id) setCurrentSessionId(d.session_id);
        const loaded = d.messages
          .filter((m: any) => (m.role === "user" || m.role === "assistant" || m.role === "tool") && !(m.role === "assistant" && !m.content))
          .map((m: any) => {
            const msg = mkMsg(m.role as ChatMessage["role"], m.content);
            if (m.metadata?.input_tokens || m.metadata?.output_tokens) {
              msg.tokenUsage = {
                input: m.metadata.input_tokens || 0,
                output: m.metadata.output_tokens || 0,
                rounds: m.metadata.rounds,
                cache_write: m.metadata.cache_write_tokens || 0,
                cache_read: m.metadata.cache_read_tokens || 0,
              };
            }
            return msg;
          });
        if (loaded.length > 0) {
          setMessages(loaded);
          try { sessionStorage.setItem(`hh_chat_${opts.historyEndpoint}`, JSON.stringify(loaded.slice(-100))); }
          catch { /* quota */ }
        }
      })
      .catch(() => {});
  }, [opts.historyEndpoint]);

  // ── Scroll-Tracking: User scrollt hoch → kein auto-scroll ─────────────
  // Detection-Schwelle generös (140px) damit kleine Render-Hops während
  // Streaming nicht fälschlich als „User scrollt hoch" interpretiert werden.
  // Guard-Fenster gegen die programmgesteuerten Scroll-Events deutlich länger
  // als vorher (50ms → 300ms) — Browser feuert das Scroll-Event manchmal
  // mehrere Frames später, race-bedingt mit Streaming-Updates.
  useEffect(() => {
    const el = scrollContainerRef.current;
    if (!el) return;
    function onScroll() {
      if (!el || isAutoScrolling.current) return;
      const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 140;
      userScrolledUp.current = !atBottom;
    }
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, []);

  // ── Auto-scroll: bottomRef in View bringen ───────────────────────────────
  // Wir scrollen den bottomRef-Sentinel an statt scrollTop manuell zu setzen.
  // Vorteil: scrollIntoView funktioniert auch zuverlässig wenn neue Inhalte
  // nach dem Effect erst gerendert werden — der Browser kümmert sich um
  // Layout-Reflow + Scroll in einem Schritt. Plus: doppelte rAF damit der
  // Stream-Update wirklich gepainted ist bevor wir scrollen.
  //
  // Trigger: messages-Array (jede neue oder veränderte Nachricht) UND der
  // Content der letzten Message (Streaming-Token-Updates).
  const _lastMsgLen = messages.length > 0 ? messages[messages.length - 1].content?.length ?? 0 : 0;
  useEffect(() => {
    if (userScrolledUp.current) return;
    const el = scrollContainerRef.current;
    const sentinel = bottomRef.current;
    if (!el && !sentinel) return;
    isAutoScrolling.current = true;
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        if (sentinel) {
          sentinel.scrollIntoView({ block: "end", behavior: "auto" });
        } else if (el) {
          el.scrollTop = el.scrollHeight;
        }
        setTimeout(() => { isAutoScrolling.current = false; }, 300);
      });
    });
  }, [messages.length, _lastMsgLen, sending]);

  // ── Coach toggle ──────────────────────────────────────────────────────────

  const toggleCoach = useCallback((v: boolean) => {
    setCoachEnabled(v);
    localStorage.setItem("hh_prompt_coach", v ? "1" : "0");
  }, []);

  // ── Abort ─────────────────────────────────────────────────────────────────

  const abort = useCallback(() => {
    abortRef.current?.abort();
    opts.onAbort?.();
  }, [opts.onAbort]);

  // ── Send ──────────────────────────────────────────────────────────────────

  const send = useCallback(async (contentOverride?: string) => {
    const content = (contentOverride ?? input).trim();
    if (!content || sending) return;
    setInput("");
    setError("");
    setShowSuggest(false);
    setShowEmoji(false);
    setCoachFeedback(null);

    // Slash commands
    if (content.startsWith("/") && opts.onSlashCommand) {
      const [cmd, ...argParts] = content.split(" ");
      const handled = opts.onSlashCommand(cmd, argParts.join(" "));
      if (handled) return;
    }

    // Built-in slash commands
    if (content === "/clear") {
      clearedAt.current = Date.now();     // loadHistory für 3s sperren
      userScrolledUp.current = false;     // Scroll zurück nach unten
      setMessages([]);
      setCurrentSessionId(null);
      try { sessionStorage.removeItem(`hh_chat_${opts.historyEndpoint}`); } catch {}
      try { localStorage.removeItem(`hh_lastsess_${opts.historyEndpoint}`); } catch {}
      // Server-Session beenden (DELETE /agents/{id}/session oder /projects/{id}/session/end)
      const sessionEndpoint = opts.historyEndpoint.replace(/\/history$/, "");
      api.delete(sessionEndpoint).catch(() => {});
      return;
    }

    // Before-send callback (e.g. companion event)
    opts.onBeforeSend?.(content);

    // Coach check
    if (coachEnabled && !contentOverride) {
      setCoachChecking(true);
      try {
        const r = await api.post<{ ok: boolean; suggestion?: string; reason?: string }>("/me/agent/coach", { content });
        if (!r.ok && r.suggestion) {
          setCoachFeedback(r);
          setInput(content);
          setCoachChecking(false);
          return;
        }
      } catch { /* ignore */ }
      setCoachChecking(false);
    }

    // User sendet → zurück nach unten scrollen
    userScrolledUp.current = false;

    const userMsg: ChatMessage = { ...mkMsg("user", content), _images: pendingImages.map(i => i.preview) };
    let currentAsst = mkMsg("assistant", "");
    let asstAdded = false;
    let hadToolCalls = false;

    setMessages(ms => [...ms, userMsg]);
    setPendingImages([]);
    setSending(true);
    setFollowUpChips([]);
    setElapsed(0);
    setDebugEvents([]);

    const controller = new AbortController();
    abortRef.current = controller;
    elapsedTimerRef.current = setInterval(() => setElapsed(s => s + 1), 1000);

    try {
      await sseStream({
        url: opts.streamEndpoint,
        body: {
          content,
          ...(pendingImages.length > 0 ? { images: pendingImages.map(i => ({ data: i.data, media_type: i.media_type })) } : {}),
          ...(opts.extraBodyParams ?? {}),
        },
        signal: controller.signal,
        onConnectionLost: () => setError(t("common.connectionLost", { defaultValue: "Verbindung verloren — bitte nochmal senden" })),
        onEvent: (evt) => {
          // Debug-Events sammeln (alle außer text-chunks)
          if (evt.type !== "text") {
            setDebugEvents(prev => [...prev, { ts: Date.now(), type: evt.type, data: evt as unknown as Record<string, unknown> }]);
          }
          if (evt.type === "context_info" || evt.type === "info") {
            return; // Nur Debug-Panel
          }
          if (evt.type === "text") {
            setActiveTool(null);
            if (!asstAdded || hadToolCalls) {
              currentAsst = mkMsg("assistant", "");
              setMessages(ms => [...ms, currentAsst]);
              setStreamingMsgId(currentAsst.id);
              asstAdded = true;
              hadToolCalls = false;
            }
            setMessages(ms => ms.map(m =>
              m.id === currentAsst.id ? { ...m, content: m.content + evt.text } : m
            ));
          } else if (evt.type === "tool_image") {
            const imgMsg = mkMsg("tool", `__IMG__${evt.tool_name || "screenshot"}|${evt.tool_image}`);
            setMessages(ms => [...ms, imgMsg]);
          } else if (evt.type === "tool_warning") {
            const warnMsg = mkMsg("tool", `⚠️ ${evt.tool_name}|⚠️ WARNUNG: ${evt.tool_warning}`);
            setMessages(ms => [...ms, warnMsg]);
          } else if (evt.type === "tool_confirm_required") {
            // #641: Tool-Call wartet auf User-Bestätigung — Banner anzeigen.
            setPendingConfirms(prev => {
              if (prev.some(p => p.tool_call_id === evt.tool_call_id)) return prev;
              return [...prev, {
                tool_call_id: evt.tool_call_id,
                tool_name:    evt.tool_name,
                tool_input:   evt.tool_input,
                risk:         evt.risk,
                session_id:   evt.session_id,
              }];
            });
          } else if (evt.type === "tool_call") {
            setActiveTool({ name: evt.tool_call, detail: evt.tool_detail ?? evt.tool_call });
            const toolMsg: ChatMessage = {
              ...mkMsg("tool", `${evt.tool_call}|${evt.tool_detail || evt.tool_call}`),
              toolCallId: evt.tool_call_id,
            };
            setMessages(ms => [...ms, toolMsg]);
            hadToolCalls = true;
          } else if (evt.type === "tool_result") {
            // Tool-Output nachträglich in die passende Tool-Message eintragen
            const callId = evt.tool_call_id as string | undefined;
            const resultText = (evt.tool_result as string) || "";
            // #641: zugehöriger Confirm (falls noch pending) ist hiermit aufgelöst
            if (callId) {
              setPendingConfirms(prev => prev.filter(p => p.tool_call_id !== callId));
            }
            setMessages(ms => {
              if (callId) {
                // Exaktes Match via tool_call_id
                return ms.map(m => m.toolCallId === callId ? { ...m, toolResult: resultText } : m);
              }
              // Fallback: letzte Tool-Message ohne Result updaten
              const lastIdx = [...ms].reverse().findIndex(m => m.role === "tool" && !m.content.startsWith("__IMG__") && m.toolResult === undefined);
              if (lastIdx < 0) return ms;
              const realIdx = ms.length - 1 - lastIdx;
              return ms.map((m, i) => i === realIdx ? { ...m, toolResult: resultText } : m);
            });
          } else if (evt.type === "done") {
            const updates: Partial<ChatMessage> = {};
            if (evt.usage && (evt.usage.input > 0 || evt.usage.output > 0))
              updates.tokenUsage = evt.usage;
            if (evt.is_fallback)
              Object.assign(updates, { model: evt.model, isFallback: true });
            if (Object.keys(updates).length > 0)
              setMessages(ms => ms.map(m => m.id === currentAsst.id ? { ...m, ...updates } : m));
            // #641: Stream-Ende — alle noch pendingen Confirms sind nicht mehr aktuell
            setPendingConfirms([]);
          } else if (evt.type === "suggestions") {
            setFollowUpChips(evt.suggestions);
          } else if (evt.type === "error") {
            if (evt.session_reset) setMessages([]);
            // #641: Bei Error sind pending Confirms obsolet
            setPendingConfirms([]);
            throw new Error(evt.error);
          }
        },
      });
      opts.onStreamComplete?.(currentAsst.content ?? "");
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") {
        // User aborted
      } else {
        setError(e instanceof Error ? e.message : t("common.error"));
        setMessages(ms => ms.filter(m => m.id !== userMsg.id && m.id !== currentAsst.id));
        setInput(content);
      }
    } finally {
      setSending(false);
      // Chat-Cache nach Streaming aktualisieren
      setMessages(ms => {
        try { sessionStorage.setItem(`hh_chat_${opts.historyEndpoint}`, JSON.stringify(ms.slice(-100))); }
        catch { /* quota */ }
        return ms;
      });
      abortRef.current = null;
      if (elapsedTimerRef.current) { clearInterval(elapsedTimerRef.current); elapsedTimerRef.current = null; }
      setElapsed(0);
      setActiveTool(null);
      if (streamingMsgId) setDoneMsgId(streamingMsgId);
      setStreamingMsgId(null);
      textareaRef.current?.focus();
    }
  }, [input, sending, pendingImages, coachEnabled, opts, t]);

  // ── Image Upload ──────────────────────────────────────────────────────────

  const handleImageUpload = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files) return;
    Array.from(files).slice(0, 5 - pendingImages.length).forEach(file => {
      const reader = new FileReader();
      reader.onload = (ev) => {
        const dataUrl = ev.target?.result as string;
        const [header] = dataUrl.split(",");
        const mediaType = header?.match(/data:(.*?);/)?.[1] || "image/png";
        const base64 = dataUrl.split(",")[1] || "";
        setPendingImages(prev => [...prev, { data: base64, media_type: mediaType, preview: dataUrl }]);
      };
      reader.readAsDataURL(file);
    });
    e.target.value = "";
  }, [pendingImages.length]);

  // #641: optimistic-remove vom Banner nach erfolgreichem Approve/Deny POST
  const removePendingConfirm = useCallback((toolCallId: string) => {
    setPendingConfirms(prev => prev.filter(p => p.tool_call_id !== toolCallId));
  }, []);

  return {
    // State
    messages, setMessages,
    input, setInput,
    sending,
    error, setError,
    // #641: Tool-Bestätigungen
    pendingConfirms,
    removePendingConfirm,
    // UI
    showEmoji, setShowEmoji,
    showSuggest, setShowSuggest,
    suggestIdx, setSuggestIdx,
    followUpChips, setFollowUpChips,
    lightboxSrc, setLightboxSrc,
    pendingImages, setPendingImages,
    // Stream
    activeTool,
    streamingMsgId,
    doneMsgId,
    elapsed,
    debugEvents,
    // Coach
    coachEnabled, toggleCoach,
    coachFeedback, setCoachFeedback,
    coachChecking,
    // History
    showHistory, setShowHistory,
    sessions, setSessions,
    viewSession, setViewSession,
    currentSessionId, setCurrentSessionId,
    // Refs
    bottomRef, scrollContainerRef, textareaRef, fileInputRef,
    // Actions
    send, abort, loadHistory,
    handleImageUpload,
  };
}
