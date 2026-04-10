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

  // Refs
  const bottomRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const elapsedTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const userScrolledUp = useRef(false);

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
    api.get<{ session_id: string | null; messages: any[]; count: number }>(opts.historyEndpoint)
      .then(d => {
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
  useEffect(() => {
    const el = scrollContainerRef.current;
    if (!el) return;
    function onScroll() {
      if (!el) return;
      const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
      userScrolledUp.current = !atBottom;
    }
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, []);

  // ── Auto-scroll: nur wenn User am Ende ist ───────────────────────────────
  useEffect(() => {
    if (!userScrolledUp.current) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages, sending]);

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
      setMessages([]);
      try { sessionStorage.removeItem(`hh_chat_${opts.historyEndpoint}`); } catch {}
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
          } else if (evt.type === "tool_call") {
            setActiveTool({ name: evt.tool_call, detail: evt.tool_detail ?? evt.tool_call });
            const toolMsg = mkMsg("tool", `${evt.tool_call}|${evt.tool_detail || evt.tool_call}`);
            setMessages(ms => [...ms, toolMsg]);
            hadToolCalls = true;
          } else if (evt.type === "done") {
            const updates: Partial<ChatMessage> = {};
            if (evt.usage && (evt.usage.input > 0 || evt.usage.output > 0))
              updates.tokenUsage = evt.usage;
            if (evt.is_fallback)
              Object.assign(updates, { model: evt.model, isFallback: true });
            if (Object.keys(updates).length > 0)
              setMessages(ms => ms.map(m => m.id === currentAsst.id ? { ...m, ...updates } : m));
          } else if (evt.type === "suggestions") {
            setFollowUpChips(evt.suggestions);
          } else if (evt.type === "error") {
            if (evt.session_reset) setMessages([]);
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

  return {
    // State
    messages, setMessages,
    input, setInput,
    sending,
    error, setError,
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
    // Refs
    bottomRef, scrollContainerRef, textareaRef, fileInputRef,
    // Actions
    send, abort, loadHistory,
    handleImageUpload,
  };
}
