import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ExternalThread,
  useAui,
  type AppendMessage,
  type ExternalThreadMessage,
  type ThreadAssistantMessagePart,
} from "@assistant-ui/react";
import { sseStream, type SSEEvent } from "@/lib/sseStream";
import { api, type SessionFull, type SessionPreview } from "@/lib/api";

type TokenUsage = {
  input?: number;
  output?: number;
  cache_read?: number;
  cache_write?: number;
  rounds?: number;
};

type HydrahiveMetadata = {
  tokenUsage?: TokenUsage;
  model?: string;
  isFallback?: boolean;
};

export type PendingToolConfirm = {
  tool_call_id: string;
  tool_name: string;
  tool_input?: Record<string, unknown>;
  risk?: string;
  session_id?: string;
};

export type PendingImage = {
  data: string;
  media_type: string;
  preview: string;
};

export type CoachFeedback = {
  ok: boolean;
  content?: string;
  suggestion?: string;
  reason?: string;
};

export type ChatV2Target = {
  kind: "project" | "agent" | "me";
  id: string;
  label: string;
  streamEndpoint: string;
  historyEndpoint: string;
  extraBodyParams?: Record<string, unknown>;
};

type HistoryResponse = {
  session_id: string | null;
  messages: Array<{
    role: "user" | "assistant" | "tool" | "system";
    content: string;
    metadata?: {
      input_tokens?: number;
      output_tokens?: number;
      rounds?: number;
      cache_read_tokens?: number;
      cache_write_tokens?: number;
      model?: string;
    };
  }>;
};

type AgentInfoResponse = {
  agent_id: string;
};

let idSeq = 0;
const nextId = (prefix: string) => `${prefix}-${Date.now()}-${++idSeq}`;

function textPart(text: string) {
  return { type: "text" as const, text };
}

function metadata(custom: Record<string, unknown> = {}) {
  return { custom };
}

function assistantMetadata(custom: Record<string, unknown> = {}) {
  return {
    unstable_state: null,
    unstable_annotations: [],
    unstable_data: [],
    steps: [],
    custom,
  };
}

function userMessage(text: string, images: PendingImage[] = []): ExternalThreadMessage {
  return {
    id: nextId("user"),
    role: "user",
    content: [
      ...(text ? [textPart(text)] : []),
      ...images.map((image) => ({
        type: "image" as const,
        image: image.preview,
        filename: "upload",
        mimeType: image.media_type,
      })),
    ],
    attachments: [],
    createdAt: new Date(),
    metadata: metadata(),
  };
}

function assistantMessage(text = "", custom: HydrahiveMetadata = {}, running = false): ExternalThreadMessage {
  return {
    id: nextId("assistant"),
    role: "assistant",
    content: text ? [textPart(text)] : [],
    createdAt: new Date(),
    status: running ? { type: "running" } : { type: "complete", reason: "stop" },
    metadata: assistantMetadata(custom as Record<string, unknown>),
  };
}

function systemMessage(text: string): ExternalThreadMessage {
  return {
    id: nextId("system"),
    role: "system",
    content: [textPart(text)],
    createdAt: new Date(),
    metadata: metadata(),
  };
}

function historyToMessage(raw: HistoryResponse["messages"][number]): ExternalThreadMessage | null {
  if (raw.role === "tool") {
    return assistantMessage("", {
      model: "tool",
    });
  }
  const tokenUsage = raw.metadata ? {
    input: raw.metadata.input_tokens ?? 0,
    output: raw.metadata.output_tokens ?? 0,
    rounds: raw.metadata.rounds,
    cache_read: raw.metadata.cache_read_tokens ?? 0,
    cache_write: raw.metadata.cache_write_tokens ?? 0,
  } : undefined;
  if (raw.role === "user") return userMessage(raw.content);
  if (raw.role === "assistant") {
    return assistantMessage(raw.content, {
      tokenUsage,
      model: raw.metadata?.model,
    });
  }
  if (raw.role === "system") return systemMessage(raw.content);
  return null;
}

function sessionMessageToThreadMessage(raw: SessionFull["messages"][number]): ExternalThreadMessage | null {
  if (raw.role === "tool") return null;
  return historyToMessage({
    role: raw.role,
    content: raw.content,
  });
}

function resumedMessageToThreadMessage(raw: { role: string; content: string }): ExternalThreadMessage | null {
  if (raw.role !== "user" && raw.role !== "assistant" && raw.role !== "system") return null;
  return historyToMessage({
    role: raw.role,
    content: raw.content,
  });
}

function appendAssistantText(message: ExternalThreadMessage, chunk: string): ExternalThreadMessage {
  if (message.role !== "assistant") return message;
  const content: ThreadAssistantMessagePart[] = [...message.content];
  const last = content[content.length - 1];
  if (last?.type === "text") {
    content[content.length - 1] = { ...last, text: last.text + chunk };
  } else {
    content.push(textPart(chunk));
  }
  return { ...message, content };
}

function appendDataPart(message: ExternalThreadMessage, name: string, data: unknown): ExternalThreadMessage {
  if (message.role !== "assistant") return message;
  return {
    ...message,
    content: [
      ...message.content,
      { type: "data", name, data },
    ] satisfies ThreadAssistantMessagePart[],
  };
}

function setAssistantDone(message: ExternalThreadMessage, evt: Extract<SSEEvent, { type: "done" }>): ExternalThreadMessage {
  if (message.role !== "assistant") return message;
  return {
    ...message,
    status: { type: "complete", reason: "stop" },
    metadata: assistantMetadata({
      ...(message.metadata.custom ?? {}),
      tokenUsage: evt.usage,
      model: evt.model,
      isFallback: evt.is_fallback,
    }),
  };
}

function appendMessageText(message: AppendMessage): string {
  return message.content
    .filter((part) => part.type === "text")
    .map((part) => part.text)
    .join("\n\n")
    .trim();
}

export function useHydraHiveRuntime(target: ChatV2Target) {
  const [messages, setMessages] = useState<ExternalThreadMessage[]>([]);
  const [isRunning, setIsRunning] = useState(false);
  const [error, setError] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [pendingConfirms, setPendingConfirms] = useState<PendingToolConfirm[]>([]);
  const [confirmingIds, setConfirmingIds] = useState<Set<string>>(() => new Set());
  const [pendingImages, setPendingImages] = useState<PendingImage[]>([]);
  const [followUpChips, setFollowUpChips] = useState<string[]>([]);
  const [coachEnabled, setCoachEnabled] = useState(() => localStorage.getItem("hh_prompt_coach") === "1");
  const [coachFeedback, setCoachFeedback] = useState<CoachFeedback | null>(null);
  const [coachChecking, setCoachChecking] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [sessions, setSessions] = useState<SessionPreview[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [viewSession, setViewSession] = useState<{ id: string; startedAt: string; messages: ExternalThreadMessage[] } | null>(null);
  const [agentSessionId, setAgentSessionId] = useState<string | null>(target.kind === "agent" ? target.id : null);
  const abortRef = useRef<AbortController | null>(null);
  const activeAssistantIdRef = useRef<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setError("");
    setPendingConfirms([]);
    api.get<HistoryResponse>(target.historyEndpoint)
      .then((history) => {
        if (cancelled) return;
        setSessionId(history.session_id);
        const loaded = history.messages
          .map(historyToMessage)
          .filter((msg): msg is ExternalThreadMessage => msg !== null);
        setMessages(loaded);
      })
      .catch(() => {
        if (!cancelled) setMessages([]);
      });
    return () => { cancelled = true; };
  }, [target.historyEndpoint]);

  useEffect(() => {
    if (target.kind !== "me") {
      setAgentSessionId(target.kind === "agent" ? target.id : null);
      return;
    }
    let cancelled = false;
    api.myAgent()
      .then((info: AgentInfoResponse) => {
        if (!cancelled) setAgentSessionId(info.agent_id);
      })
      .catch(() => {
        if (!cancelled) setAgentSessionId(null);
      });
    return () => { cancelled = true; };
  }, [target.id, target.kind]);

  const ensureAssistant = useCallback(() => {
    let id = activeAssistantIdRef.current;
    if (id) return id;
    const msg = assistantMessage("", {}, true);
    id = msg.id;
    activeAssistantIdRef.current = id;
    setMessages((prev) => [...prev, msg]);
    return id;
  }, []);

  const updateAssistant = useCallback((updater: (msg: ExternalThreadMessage) => ExternalThreadMessage) => {
    const id = ensureAssistant();
    setMessages((prev) => prev.map((msg) => msg.id === id ? updater(msg) : msg));
  }, [ensureAssistant]);

  const runSend = useCallback(async (content: string, skipCoach = false) => {
    const images = pendingImages;
    if ((!content && images.length === 0) || isRunning) return;
    setError("");
    setCoachFeedback(null);

    if (coachEnabled && !skipCoach && content) {
      setCoachChecking(true);
      try {
        const feedback = await api.post<CoachFeedback>("/me/agent/coach", { content });
        if (!feedback.ok && feedback.suggestion) {
          setCoachFeedback({ ...feedback, content });
          return;
        }
      } catch {
        // Coach ist optional; ein Fehler darf den Chat nicht blockieren.
      } finally {
        setCoachChecking(false);
      }
    }

    const controller = new AbortController();
    abortRef.current = controller;
    activeAssistantIdRef.current = null;
    setMessages((prev) => [...prev, userMessage(content, images)]);
    setPendingImages([]);
    setFollowUpChips([]);
    setIsRunning(true);

    try {
      await sseStream({
        url: target.streamEndpoint,
        body: {
          content,
          ...(images.length > 0 ? {
            images: images.map((image) => ({
              data: image.data,
              media_type: image.media_type,
            })),
          } : {}),
          ...(target.extraBodyParams ?? {}),
        },
        signal: controller.signal,
        onConnectionLost: () => setError("Verbindung verloren — bitte erneut senden."),
        onEvent: (evt) => {
          if (evt.type === "text") {
            updateAssistant((msg) => appendAssistantText(msg, evt.text));
          } else if (evt.type === "tool_call") {
            updateAssistant((msg) => appendDataPart(msg, "tool_call", evt));
          } else if (evt.type === "tool_result") {
            updateAssistant((msg) => appendDataPart(msg, "tool_result", evt));
          } else if (evt.type === "tool_image") {
            updateAssistant((msg) => appendDataPart(msg, "tool_image", evt));
          } else if (evt.type === "tool_warning") {
            updateAssistant((msg) => appendDataPart(msg, "tool_warning", evt));
          } else if (evt.type === "tool_confirm_required") {
            setPendingConfirms((prev) => {
              if (prev.some((item) => item.tool_call_id === evt.tool_call_id)) return prev;
              return [...prev, {
                tool_call_id: evt.tool_call_id,
                tool_name: evt.tool_name,
                tool_input: evt.tool_input,
                risk: evt.risk,
                session_id: evt.session_id,
              }];
            });
            updateAssistant((msg) => appendDataPart(msg, "tool_confirm_required", evt));
          } else if (evt.type === "context_info" || evt.type === "info") {
            updateAssistant((msg) => appendDataPart(msg, evt.type, evt));
          } else if (evt.type === "done") {
            setPendingConfirms([]);
            updateAssistant((msg) => setAssistantDone(msg, evt));
          } else if (evt.type === "error") {
            setPendingConfirms([]);
            throw new Error(evt.error);
          } else if (evt.type === "suggestions") {
            setFollowUpChips(evt.suggestions);
          }
        },
      });
    } catch (err) {
      if (!(err instanceof DOMException && err.name === "AbortError")) {
        setError(err instanceof Error ? err.message : "Unbekannter Fehler");
        updateAssistant((msg) => ({
          ...msg,
          status: { type: "incomplete", reason: "error", error: String(err) },
        }));
      }
    } finally {
      setIsRunning(false);
      abortRef.current = null;
      activeAssistantIdRef.current = null;
    }
  }, [coachEnabled, isRunning, pendingImages, target.extraBodyParams, target.streamEndpoint, updateAssistant]);

  const send = useCallback(async (message: AppendMessage) => {
    await runSend(appendMessageText(message));
  }, [runSend]);

  const sendText = useCallback(async (content: string, skipCoach = false) => {
    await runSend(content.trim(), skipCoach);
  }, [runSend]);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const addImages = useCallback((files: FileList | File[]) => {
    const list = Array.from(files)
      .filter((file) => file.type.startsWith("image/"))
      .slice(0, Math.max(0, 5 - pendingImages.length));
    if (list.length === 0) return;

    list.forEach((file) => {
      const reader = new FileReader();
      reader.onload = (event) => {
        const dataUrl = String(event.target?.result || "");
        const [, base64 = ""] = dataUrl.split(",");
        if (!base64) return;
        setPendingImages((prev) => {
          if (prev.length >= 5) return prev;
          return [...prev, {
            data: base64,
            media_type: file.type || "image/png",
            preview: dataUrl,
          }];
        });
      };
      reader.onerror = () => setError(`Bild konnte nicht gelesen werden: ${file.name}`);
      reader.readAsDataURL(file);
    });
  }, [pendingImages.length]);

  const removeImage = useCallback((index: number) => {
    setPendingImages((prev) => prev.filter((_, i) => i !== index));
  }, []);

  const clearFollowUpChips = useCallback(() => {
    setFollowUpChips([]);
  }, []);

  const toggleCoach = useCallback((enabled: boolean) => {
    setCoachEnabled(enabled);
    localStorage.setItem("hh_prompt_coach", enabled ? "1" : "0");
  }, []);

  const clearCoachFeedback = useCallback(() => {
    setCoachFeedback(null);
  }, []);

  const loadSessions = useCallback(async () => {
    setHistoryLoading(true);
    try {
      if (target.kind === "project") {
        const response = await api.listProjectSessions(target.id, 30);
        setSessions(response.sessions);
      } else if (agentSessionId) {
        const response = await api.listSessions(agentSessionId, 30);
        setSessions(response.sessions);
      } else {
        setSessions([]);
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(`Session-Liste konnte nicht geladen werden: ${msg}`);
    } finally {
      setHistoryLoading(false);
    }
  }, [agentSessionId, target.id, target.kind]);

  const toggleHistory = useCallback(() => {
    setShowHistory((current) => {
      const next = !current;
      if (next) void loadSessions();
      return next;
    });
    setViewSession(null);
  }, [loadSessions]);

  const openSession = useCallback(async (sessionIdToOpen: string) => {
    setHistoryLoading(true);
    try {
      const session = target.kind === "project"
        ? await api.get<SessionFull>(`/projects/${target.id}/sessions/${sessionIdToOpen}`)
        : agentSessionId
          ? await api.getSessionById(agentSessionId, sessionIdToOpen)
          : null;
      if (!session) return;
      const loaded = session.messages
        .filter((msg) => !(msg.role === "assistant" && !msg.content))
        .map(sessionMessageToThreadMessage)
        .filter((msg): msg is ExternalThreadMessage => msg !== null);
      setViewSession({
        id: session.id,
        startedAt: session.started_at,
        messages: loaded,
      });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(`Session konnte nicht geladen werden: ${msg}`);
    } finally {
      setHistoryLoading(false);
    }
  }, [agentSessionId, target.id, target.kind]);

  const resumeSession = useCallback(async (sessionIdToResume: string) => {
    setHistoryLoading(true);
    try {
      const response = target.kind === "project"
        ? await api.resumeProjectSession(target.id, sessionIdToResume)
        : agentSessionId
          ? await api.resumeSession(agentSessionId, sessionIdToResume)
          : null;
      if (!response) return;
      const loaded = response.messages
        .filter((msg) => !(msg.role === "assistant" && !msg.content))
        .map(resumedMessageToThreadMessage)
        .filter((msg): msg is ExternalThreadMessage => msg !== null);
      setMessages(loaded);
      setSessionId(response.id);
      setViewSession(null);
      setShowHistory(false);
      try {
        localStorage.setItem(`hh_lastsess_${target.historyEndpoint}`, response.id);
      } catch {
        // ignore quota/private mode
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(`Session konnte nicht fortgesetzt werden: ${msg}`);
    } finally {
      setHistoryLoading(false);
    }
  }, [agentSessionId, target.historyEndpoint, target.id, target.kind]);

  const closeSessionView = useCallback(() => {
    setViewSession(null);
  }, []);

  const closeHistory = useCallback(() => {
    setViewSession(null);
    setShowHistory(false);
  }, []);

  const confirmTool = useCallback(async (toolCallId: string, decision: "approve" | "deny") => {
    const pending = pendingConfirms.find((item) => item.tool_call_id === toolCallId);
    const sid = pending?.session_id || sessionId;
    if (!sid) {
      setError("Tool-Bestätigung fehlgeschlagen: Session fehlt.");
      return;
    }

    setConfirmingIds((prev) => new Set(prev).add(toolCallId));
    try {
      if (target.kind === "project") {
        await api.confirmToolCall(target.id, sid, toolCallId, decision);
      } else {
        await api.confirmToolCallAgent(target.id, sid, toolCallId, decision);
      }
      setPendingConfirms((prev) => prev.filter((item) => item.tool_call_id !== toolCallId));
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(`Tool-Bestätigung fehlgeschlagen: ${msg}`);
    } finally {
      setConfirmingIds((prev) => {
        const next = new Set(prev);
        next.delete(toolCallId);
        return next;
      });
    }
  }, [pendingConfirms, sessionId, target.id, target.kind]);

  const thread = ExternalThread({
    messages,
    isRunning,
    onNew: send,
    onCancel: cancel,
  });
  const aui = useAui({ thread }, { parent: null });

  return useMemo(() => ({
    aui,
    messages,
    isRunning,
    error,
    sessionId,
    pendingConfirms,
    confirmingIds,
    pendingImages,
    followUpChips,
    coachEnabled,
    coachFeedback,
    coachChecking,
    showHistory,
    sessions,
    historyLoading,
    viewSession,
    addImages,
    removeImage,
    clearFollowUpChips,
    toggleCoach,
    clearCoachFeedback,
    loadSessions,
    toggleHistory,
    openSession,
    resumeSession,
    closeSessionView,
    closeHistory,
    send,
    sendText,
    cancel,
    confirmTool,
  }), [aui, messages, isRunning, error, sessionId, pendingConfirms, confirmingIds, pendingImages, followUpChips, coachEnabled, coachFeedback, coachChecking, showHistory, sessions, historyLoading, viewSession, addImages, removeImage, clearFollowUpChips, toggleCoach, clearCoachFeedback, loadSessions, toggleHistory, openSession, resumeSession, closeSessionView, closeHistory, send, sendText, cancel, confirmTool]);
}

export function buildChatV2Target(kind: string, id: string): ChatV2Target {
  if (kind === "agent") {
    return {
      kind: "agent",
      id,
      label: `Agent ${id}`,
      streamEndpoint: `/api/agents/${id}/message/stream`,
      historyEndpoint: `/api/agents/${id}/session/history`,
    };
  }
  if (kind === "me") {
    return {
      kind: "me",
      id: "me",
      label: "Mein Agent",
      streamEndpoint: "/api/me/agent/message/stream",
      historyEndpoint: "/api/me/agent/session/history",
    };
  }
  return {
    kind: "project",
    id,
    label: `Projekt ${id}`,
    streamEndpoint: `/api/projects/${id}/message/stream`,
    historyEndpoint: `/api/projects/${id}/session/history`,
  };
}
