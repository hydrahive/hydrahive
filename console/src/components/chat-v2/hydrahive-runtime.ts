import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ExternalThread,
  useAui,
  type AppendMessage,
  type ExternalThreadMessage,
  type ThreadAssistantMessagePart,
} from "@assistant-ui/react";
import { sseStream, type SSEEvent } from "@/lib/sseStream";
import { api } from "@/lib/api";

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

function userMessage(text: string): ExternalThreadMessage {
  return {
    id: nextId("user"),
    role: "user",
    content: [textPart(text)],
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

  const send = useCallback(async (message: AppendMessage) => {
    const content = appendMessageText(message);
    if (!content || isRunning) return;
    setError("");
    const controller = new AbortController();
    abortRef.current = controller;
    activeAssistantIdRef.current = null;
    setMessages((prev) => [...prev, userMessage(content)]);
    setIsRunning(true);

    try {
      await sseStream({
        url: target.streamEndpoint,
        body: {
          content,
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
  }, [isRunning, target.extraBodyParams, target.streamEndpoint, updateAssistant]);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
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
    send,
    cancel,
    confirmTool,
  }), [aui, messages, isRunning, error, sessionId, pendingConfirms, confirmingIds, send, cancel, confirmTool]);
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
