import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { ArrowLeft, Send, Square, Bot, User, Network, Terminal, Radar, Sparkles, Smile, History, X, ChevronRight, Loader2, RefreshCw, RotateCcw, Plus, ImagePlus } from "lucide-react";
import { api, SessionPreview, SessionFull } from "@/lib/api";
import ReactMarkdown from "react-markdown";
import EmojiPicker, { type EmojiClickData, Theme } from "emoji-picker-react";
import { useTranslation } from "react-i18next";

interface Message {
  id: string;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  workers?: string[];
  tokenUsage?: { input: number; output: number; rounds?: number; cache_write?: number; cache_read?: number };
  model?: string;
  isFallback?: boolean;
}

let msgCounter = 0;
function mkMsg(role: Message["role"], content: string, workers?: string[]): Message {
  return { id: `msg-${++msgCounter}`, role, content, workers };
}

export function ChatPage() {
  const { t } = useTranslation();
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const SLASH_COMMANDS = [
    { cmd: "/help",     desc: t("slashCommands.help") },
    { cmd: "/clear",    desc: t("slashCommands.clear") },
    { cmd: "/status",   desc: t("slashCommands.status") },
    { cmd: "/retry",    desc: t("slashCommands.retry") },
    { cmd: "/model",    desc: t("slashCommands.model") },
    { cmd: "/remember", desc: t("slashCommands.remember") },
  ];

  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [coachEnabled, setCoachEnabled] = useState(() => localStorage.getItem("hh_prompt_coach") === "1");
  const [coachFeedback, setCoachFeedback] = useState<{ ok: boolean; suggestion?: string; reason?: string } | null>(null);
  const [coachChecking, setCoachChecking] = useState(false);
  const [projectName, setProjectName] = useState(id ?? "");
  const [showSwarm, setShowSwarm] = useState(false);
  const [projectData, setProjectData] = useState<Record<string, unknown>>({});
  const [bossModel, setBossModel] = useState<{ model?: string; temperature?: number }>({});
  const [showSuggest, setShowSuggest] = useState(false);
  const [showEmoji, setShowEmoji] = useState(false);
  const [suggestIdx, setSuggestIdx] = useState(0);
  const [activeTool, setActiveTool] = useState<{ name: string; detail: string } | null>(null);
  const [streamingMsgId, setStreamingMsgId] = useState<string | null>(null);
  const [doneMsgId, setDoneMsgId] = useState<string | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const [historyList, setHistoryList] = useState<SessionPreview[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [viewSession, setViewSession] = useState<SessionFull | null>(null);

  const [pendingImages, setPendingImages] = useState<{data: string; media_type: string; preview: string}[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const elapsedTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [elapsed, setElapsed] = useState(0);

  function toolDetail(name: string, input: Record<string, unknown>): string {
    if (name === "read_system_file" || name === "file_read") return String(input.path ?? input.file_path ?? "");
    if (name === "write_system_file" || name === "file_write") return String(input.path ?? input.file_path ?? "");
    if (name === "shell_exec") return String(input.command ?? "").slice(0, 60);
    if (name === "web_search") return String(input.query ?? "");
    if (name === "http_request") return String(input.url ?? "");
    if (name === "ask_agent") return String(input.target ?? "");
    if (name === "delegate_agent") return String(input.target ?? "");
    if (name === "write_memory" || name === "read_memory") return String(input.filename ?? "");
    if (name === "write_handoff" || name === "read_handoff") return String(input.to_agent ?? input.handoff_id ?? "");
    return "";
  }

  const suggestions = input.startsWith("/") ? SLASH_COMMANDS.filter((c) => c.cmd.startsWith(input.split(" ")[0])) : [];

  useEffect(() => {
    if (!id) return;
    api.get<Record<string, unknown>>(`/projects/${id}`)
      .then((d) => {
        setProjectData(d);
        const cfg = d.config as { identity?: { name?: string }; agents?: { boss?: string }; chat?: { show_swarm?: boolean } } | undefined;
        if (cfg?.identity?.name) setProjectName(cfg.identity.name);
        if (cfg?.chat?.show_swarm) setShowSwarm(true);
        const bossId = cfg?.agents?.boss;
        if (bossId) {
          api.get<Record<string, unknown>>(`/agents/${bossId}`)
            .then((a) => {
              const llm = (a as any)?.config?.llm as { model?: string; temperature?: number } | undefined;
              if (llm) setBossModel(llm);
            })
            .catch(e => console.error("Failed to load boss agent config", e));
        }
      })
      .catch(e => console.error("Failed to load project config", e));
    api.sessionHistory(id)
      .then((d) => {
        const loaded = d.messages
          .filter((m: any) => m.role === "user" || m.role === "assistant" || m.role === "tool")
          .map((m: any) => {
            const msg = mkMsg(m.role as Message["role"], m.content);
            if (m.metadata?.input_tokens || m.metadata?.output_tokens) {
              msg.tokenUsage = { input: m.metadata.input_tokens || 0, output: m.metadata.output_tokens || 0, rounds: m.metadata.rounds, cache_write: m.metadata.cache_write_tokens || 0, cache_read: m.metadata.cache_read_tokens || 0 };
            }
            if (m.metadata?.model) msg.model = m.metadata.model;
            return msg;
          });
        if (loaded.length > 0) setMessages(loaded);
      })
      .catch(e => console.error("Failed to load session history", e));
  }, [id]);

  // Live-Sync: History alle 3s refreshen wenn NICHT selbst am Streamen (#337)
  useEffect(() => {
    if (!id || sending) return;
    const poll = setInterval(() => {
      api.sessionHistory(id).then((d) => {
        const loaded = d.messages
          .filter((m: any) => m.role === "user" || m.role === "assistant" || m.role === "tool")
          .map((m: any) => {
            const msg = mkMsg(m.role as Message["role"], m.content);
            if (m.metadata?.input_tokens || m.metadata?.output_tokens) {
              msg.tokenUsage = { input: m.metadata.input_tokens || 0, output: m.metadata.output_tokens || 0, rounds: m.metadata.rounds, cache_write: m.metadata.cache_write_tokens || 0, cache_read: m.metadata.cache_read_tokens || 0 };
            }
            return msg;
          });
        if (loaded.length > 0) {
          setMessages(prev => {
            // Nur updaten wenn sich die Anzahl geändert hat (neuer Content)
            if (loaded.length !== prev.length) return loaded;
            return prev;
          });
        }
      }).catch(() => {});
    }, 2000);
    return () => clearInterval(poll);
  }, [id, sending]);

  function openHistory() {
    setShowHistory(true);
    setViewSession(null);
    if (!id) return;
    setHistoryLoading(true);
    api.listSessions(id).then((d) => setHistoryList(d.sessions)).catch(e => console.error("Failed to list sessions", e)).finally(() => setHistoryLoading(false));
  }

  async function openSession(sessionId: string) {
    if (!id) return;
    try {
      const s = await api.getSessionById(id, sessionId);
      setViewSession(s);
    } catch {}
  }

  async function resumeSession(sessionId: string) {
    if (!id) return;
    try {
      const d = await api.resumeSession(id, sessionId);
      if (d.resumed) {
        const loaded = (d.messages || [])
          .filter((m: any) => m.role === "user" || m.role === "assistant")
          .map((m: any) => mkMsg(m.role, m.content));
        setMessages(loaded);
        setViewSession(null);
        setShowHistory(false);
      }
    } catch (e) {
      console.error("Failed to resume session", e);
    }
  }

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    setShowSuggest(suggestions.length > 0 && input.length > 0);
    setSuggestIdx(0);
  }, [input]);

  function sysMsg(content: string) {
    setMessages((ms) => [...ms, mkMsg("system", content)]);
  }

  function handleSlashCommand(cmd: string): boolean {
    const parts = cmd.trim().split(/\s+/);
    const base = parts[0].toLowerCase();

    if (base === "/help") {
      sysMsg("**" + t("slashCommands.help") + ":**\n\n" + SLASH_COMMANDS.map((c) => `\`${c.cmd}\` — ${c.desc}`).join("\n"));
      return true;
    }
    if (base === "/clear") {
      setMessages([]);
      sysMsg(t("slashCommands.clear") + ".");
      return true;
    }
    if (base === "/status") {
      const cfg = projectData.config as { agents?: { boss?: string; workers?: string[] } } | undefined;
      const boss = cfg?.agents?.boss ?? "—";
      const workers = cfg?.agents?.workers?.join(", ") || "—";
      const model = bossModel.model ?? t("chat.notConfigured");
      sysMsg(`**${t("chat.projectPanel")}:** ${projectName} (\`${id}\`)\n**Boss-Agent:** ${boss}\n**Worker:** ${workers}\n**LLM:** ${model}`);
      return true;
    }
    if (base === "/retry") {
      const lastUser = [...messages].reverse().find((m) => m.role === "user");
      if (!lastUser) {
        sysMsg(t("chat.noRetryMessage"));
        return true;
      }
      setInput(lastUser.content);
      setTimeout(() => textareaRef.current?.focus(), 0);
      return true;
    }
    if (base === "/model") {
      const model = bossModel.model ?? t("chat.notConfigured");
      const temp = bossModel.temperature ?? "—";
      sysMsg(`**${t("chat.currentModel")}:** \`${model}\`\n**${t("chat.temperatureLabel")}:** ${temp}`);
      return true;
    }
    if (base === "/remember") {
      const cfg = projectData.config as { agents?: { boss?: string } } | undefined;
      const bossId = cfg?.agents?.boss;
      if (!bossId) {
        sysMsg(t("chat.noBossAgent"));
        return true;
      }
      const filename = parts[1]
        ? parts[1].replace(/[^a-z0-9_-]/gi, "-").toLowerCase()
        : new Date().toISOString().slice(0, 10);
      const history = messages
        .filter((m) => m.role === "user" || m.role === "assistant")
        .slice(-30)
        .map((m) => `**${m.role === "user" ? "User" : "Agent"}:** ${m.content}`)
        .join("\n\n");
      if (!history) { sysMsg(t("chat.noHistory")); return true; }
      const content = `# Session: ${new Date().toLocaleString("de")}\n\n${history}`;
      const token = localStorage.getItem("hydrahive_token") || "";
      fetch(`/api/agents/${bossId}/memory`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ filename, content }),
      })
        .then((r) => r.ok ? sysMsg(t("chat.savedMemory", { filename, agent: bossId })) : sysMsg(t("chat.saveError")))
        .catch(() => sysMsg(t("chat.saveError")));
      return true;
    }
    sysMsg(t("chat.unknownCommand", { cmd: base }));
    return true;
  }

  async function stop() {
    if (abortRef.current) abortRef.current.abort();
    if (id) {
      const token = localStorage.getItem("hydrahive_token") || "";
      fetch(`/api/projects/${id}/interrupt`, { method: "POST", headers: { Authorization: `Bearer ${token}` } }).catch(e => console.error("Failed to interrupt project", e));
    }
  }

  async function send(overrideContent?: string) {
    const rawContent = overrideContent ?? input.trim();
    if (!rawContent || sending || !id) return;
    const content = rawContent;
    setInput("");
    setError("");
    setShowSuggest(false);
    setCoachFeedback(null);

    if (content.startsWith("/")) {
      handleSlashCommand(content);
      return;
    }
    // Companion-Event
    window.dispatchEvent(new CustomEvent("hh-chat-sent", { detail: { text: content } }));

    // Prompt-Coach (#169)
    if (!overrideContent && coachEnabled) {
      setCoachChecking(true);
      try {
        const check = await api.post<{ ok: boolean; suggestion?: string; reason?: string }>("/me/agent/coach", { content });
        if (!check.ok) {
          setCoachFeedback(check);
          setInput(content);
          setCoachChecking(false);
          return;
        }
      } catch { /* Coach-Fehler → durchlassen */ }
      setCoachChecking(false);
    }

    const userMsg = { ...mkMsg("user", content), _images: pendingImages.map(i => i.preview) };
    let currentAssistantMsg = mkMsg("assistant", "");
    let hadToolsSinceLastText = false;
    setMessages((ms) => [...ms, userMsg]);
    setSending(true);
    setElapsed(0);
    const controller = new AbortController();
    abortRef.current = controller;
    elapsedTimerRef.current = setInterval(() => setElapsed((s) => s + 1), 1000);

    try {
      const token = localStorage.getItem("hydrahive_token") || "";
      const res = await fetch(`/api/projects/${id}/message/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({
          content,
          ...(pendingImages.length > 0 ? { images: pendingImages.map(i => ({ data: i.data, media_type: i.media_type })) } : {}),
        }),
        signal: controller.signal,
      });
      setPendingImages([]);
      if (!res.ok) {
        const e = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(e.detail || `HTTP ${res.status}`);
      }

      setMessages((ms) => [...ms, currentAssistantMsg]);
      setStreamingMsgId(currentAssistantMsg.id);
      const reader = res.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      outer: while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split("\n\n");
        buffer = parts.pop() ?? "";
        for (const part of parts) {
          for (const line of part.split("\n")) {
            if (!line.startsWith("data: ")) continue;
            try {
              const evt = JSON.parse(line.slice(6));
              if (evt.text !== undefined) {
                setActiveTool(null);
                // #337: Nach Tool-Calls neue Assistant-Message starten
                if (hadToolsSinceLastText) {
                  currentAssistantMsg = mkMsg("assistant", "");
                  setMessages((ms) => [...ms, currentAssistantMsg]);
                  setStreamingMsgId(currentAssistantMsg.id);
                  hadToolsSinceLastText = false;
                }
                setMessages((ms) => ms.map((m) => m.id === currentAssistantMsg.id ? { ...m, content: m.content + evt.text } : m));
              } else if (evt.tool_call !== undefined) {
                setActiveTool({ name: evt.tool_call, detail: toolDetail(evt.tool_call, evt.tool_input ?? {}) });
                const toolMsg = mkMsg("tool" as Message["role"], `${evt.tool_call}|${evt.tool_detail || toolDetail(evt.tool_call, evt.tool_input ?? {})}`);
                setMessages((ms) => [...ms, toolMsg]);
                hadToolsSinceLastText = true;
              } else if (evt.done) {
                const updates: Partial<Message> = {};
                if (evt.usage && (evt.usage.input > 0 || evt.usage.output > 0))
                  updates.tokenUsage = evt.usage;
                if (evt.is_fallback)
                  Object.assign(updates, { model: evt.model, isFallback: true });
                if (Object.keys(updates).length > 0)
                  setMessages((ms) => ms.map((m) =>
                    m.id === currentAssistantMsg.id ? { ...m, ...updates } : m
                  ));
                break outer;
              } else if (evt.error) {
                if (evt.session_reset) setMessages([]);
                throw new Error(evt.error);
              }
            } catch (parseErr) {
              if (parseErr instanceof Error && parseErr.message !== "Unexpected end of JSON input") throw parseErr;
            }
          }
        }
      }
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") {
        // User aborted — keep partial response, no error
      } else {
        setError(e instanceof Error ? e.message : t("common.error"));
        setMessages((ms) => ms.filter((m) => m.id !== userMsg.id && m.id !== currentAssistantMsg.id));
        setInput(content);
      }
    } finally {
      setSending(false);
      abortRef.current = null;
      if (elapsedTimerRef.current) { clearInterval(elapsedTimerRef.current); elapsedTimerRef.current = null; }
      setElapsed(0);
      setActiveTool(null);
      if (streamingMsgId) setDoneMsgId(streamingMsgId);
      setStreamingMsgId(null);
      textareaRef.current?.focus();
    }
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (showSuggest && suggestions.length > 0) {
      if (e.key === "ArrowDown") { e.preventDefault(); setSuggestIdx((i) => (i + 1) % suggestions.length); return; }
      if (e.key === "ArrowUp") { e.preventDefault(); setSuggestIdx((i) => (i - 1 + suggestions.length) % suggestions.length); return; }
      if (e.key === "Tab" || (e.key === "Enter" && showSuggest)) {
        e.preventDefault();
        setInput(suggestions[suggestIdx].cmd + " ");
        setShowSuggest(false);
        return;
      }
      if (e.key === "Escape") { setShowSuggest(false); return; }
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  const statusPills = useMemo(() => {
    const model = bossModel.model ?? t("chat.noModel");
    return [
      { label: model, tone: "ok" },
      { label: showSwarm ? t("chat.swarmVisiblePill") : t("chat.swarmCompactPill"), tone: "default" },
    ];
  }, [bossModel.model, showSwarm, t]);

  return (
    <div className="flex flex-col h-full min-h-0 overflow-hidden">
      <div className="flex items-center justify-between gap-3 px-4 py-3 border-b flex-shrink-0">
        <div className="flex items-center gap-3 min-w-0">
          <button onClick={() => navigate("/projects")} className="p-1.5 rounded-md hover:bg-accent transition-colors">
            <ArrowLeft className="h-4 w-4" />
          </button>
          <div className="min-w-0">
            <h1 className="text-lg font-semibold truncate">{projectName}</h1>
          </div>
          <span className="status-pill status-pill-ok text-xs"><Radar className="h-3 w-3" />{t("chat.projectChatActive")}</span>
          {statusPills.map((pill) => (
            <span key={pill.label} className={`status-pill text-xs ${pill.tone === "ok" ? "status-pill-ok" : ""}`}>{pill.label}</span>
          ))}
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          <button onClick={() => setShowSwarm((s) => !s)} className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs transition ${showSwarm ? "bg-primary/10 text-primary" : "hover:bg-accent"}`}>
            <Network className="h-3.5 w-3.5" />
            {showSwarm ? t("chat.hideSwarm") : t("chat.showSwarm")}
          </button>
        </div>
      </div>

      <section className="section-card flex-1 min-h-0 overflow-hidden p-0">
        <div className="grid h-full min-h-0 gap-0 lg:grid-cols-[minmax(0,1.7fr)_22rem]">
          <div className="relative flex min-h-0 flex-col border-b lg:border-b-0 lg:border-r overflow-hidden">
            <div className="border-b bg-muted/20 px-5 py-4">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="metric-kicker">{t("chat.conversation")}</p>
                  <h2 className="mt-2 text-lg font-semibold tracking-tight">{t("chat.projectChannel")}</h2>
                </div>
                <div className="flex items-center gap-2">
                  <button onClick={openHistory} className="inline-flex items-center gap-1.5 rounded-2xl border px-3 py-1.5 text-xs hover:bg-accent transition-colors">
                    <History className="h-3.5 w-3.5" /> {t("chat.history")}
                  </button>
                  <span className="status-pill">
                    {messages.length !== 1
                      ? t("chat.messageCountPlural", { count: messages.length })
                      : t("chat.messageCount", { count: messages.length })}
                  </span>
                </div>
              </div>
            </div>

            {/* History Drawer */}
            {showHistory && (
              <div className="absolute inset-0 z-20 flex">
                <div className="absolute inset-0 bg-background/80 backdrop-blur-sm" onClick={() => { setShowHistory(false); setViewSession(null); }} />
                <div className="relative ml-auto flex h-full w-[calc(100%-1rem)] sm:max-w-md flex-col border-l bg-background shadow-xl">
                  <div className="flex items-center justify-between border-b px-5 py-4">
                    {viewSession ? (
                      <button onClick={() => setViewSession(null)} className="inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground">
                        <ArrowLeft className="h-4 w-4" /> {t("chat.history")}
                      </button>
                    ) : (
                      <h3 className="font-semibold">{t("chat.history")}</h3>
                    )}
                    <button onClick={() => { setShowHistory(false); setViewSession(null); }} className="rounded-lg p-1.5 hover:bg-muted" aria-label="Close history">
                      <X className="h-4 w-4" />
                    </button>
                  </div>

                  {!viewSession ? (
                    <div className="flex-1 overflow-y-auto p-4 space-y-2">
                      {historyLoading && <p className="text-sm text-muted-foreground">{t("chat.historyLoading")}</p>}
                      {!historyLoading && historyList.length === 0 && (
                        <p className="text-sm text-muted-foreground">{t("chat.historyEmpty")}</p>
                      )}
                      {historyList.map((s) => (
                        <div key={s.id} className="flex items-stretch rounded-xl border bg-muted/30 hover:bg-muted/60 transition-colors overflow-hidden">
                          <button onClick={() => openSession(s.id)}
                            className="flex-1 px-4 py-3 text-left">
                            <div className="flex items-center justify-between">
                              <span className="text-xs text-muted-foreground">
                                {new Date(s.started_at).toLocaleString()}
                              </span>
                              <div className="flex items-center gap-2">
                                <span className="status-pill text-xs">{s.message_count} Msg</span>
                                <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
                              </div>
                            </div>
                            {s.preview && <p className="mt-1.5 text-sm line-clamp-2 text-foreground/80">{s.preview}</p>}
                            {!s.ended_at && <span className="mt-1 inline-block text-xs text-green-500">● aktiv</span>}
                          </button>
                          <button onClick={() => resumeSession(s.id)}
                            title={t("chat.resume", { defaultValue: "Fortsetzen" })}
                            className="flex items-center px-3 text-primary hover:bg-primary/10 border-l transition-colors flex-shrink-0">
                            <RotateCcw className="h-3.5 w-3.5" />
                          </button>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="flex-1 overflow-y-auto p-4 space-y-3">
                      <div className="flex items-center justify-between rounded-xl border bg-amber-500/10 px-3 py-2 text-xs text-amber-600 dark:text-amber-400">
                        <span>{t("chat.historyReadOnly")}</span>
                        <button onClick={() => resumeSession(viewSession.id)}
                          className="flex items-center gap-1 px-2 py-1 rounded bg-primary text-primary-foreground hover:bg-primary/90 transition-colors text-xs">
                          <RotateCcw className="h-3 w-3" /> {t("chat.resume", { defaultValue: "Fortsetzen" })}
                        </button>
                      </div>
                      {viewSession.messages.filter(m => m.role === "user" || m.role === "assistant").map((m, i) => (
                        <div key={i} className={`flex gap-2 ${m.role === "user" ? "justify-end" : "justify-start"}`}>
                          {m.role === "assistant" && (
                            <div className="mt-0.5 flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-2xl bg-primary/10">
                              <Bot className="h-3.5 w-3.5 text-primary" />
                            </div>
                          )}
                          <div className={`max-w-[85%] rounded-2xl px-3 py-2 text-sm ${m.role === "user" ? "bg-primary text-primary-foreground" : "border bg-card prose prose-sm max-w-none dark:prose-invert"}`}>
                            {m.role === "user"
                              ? <span className="whitespace-pre-wrap">{m.content}</span>
                              : <ReactMarkdown>{m.content}</ReactMarkdown>}
                          </div>
                          {m.role === "user" && (
                            <div className="mt-0.5 flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-2xl bg-secondary">
                              <User className="h-3.5 w-3.5" />
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            )}

            <div className="flex-1 overflow-y-auto overflow-x-hidden px-5 py-5 space-y-4">
              {messages.length === 0 && (
                <div className="flex h-full flex-col items-center justify-center space-y-3 text-center text-muted-foreground">
                  <Bot className="h-10 w-10" />
                  <p className="text-sm">{t("chat.emptyChat")}</p>
                  <p className="text-xs opacity-60">{t("chat.slashTip")} <code className="rounded bg-muted px-1">/help</code> {t("chat.slashTip2")}</p>
                </div>
              )}
              {messages.map((msg) => {
                if (msg.role === "tool") {
                  // Mehrere aufeinanderfolgende Tool-Calls gruppieren
                  const msgIdx = messages.indexOf(msg);
                  const prevMsg = msgIdx > 0 ? messages[msgIdx - 1] : null;
                  // Wenn der vorherige auch ein Tool war, wurde er schon in der Gruppe gerendert
                  if (prevMsg?.role === "tool") return null;
                  // Alle folgenden Tools sammeln
                  const toolGroup: typeof messages = [msg];
                  for (let i = msgIdx + 1; i < messages.length && messages[i].role === "tool"; i++) {
                    toolGroup.push(messages[i]);
                  }
                  return (
                    <div key={msg.id} className="flex justify-center">
                      <div className="flex flex-wrap gap-1.5 max-w-[85%] justify-center">
                        {toolGroup.map(tm => {
                          const [toolName, ...detailParts] = tm.content.split("|");
                          const detail = detailParts.join("|");
                          return (
                            <span key={tm.id} title={detail || toolName}
                              className="inline-flex items-center gap-1 rounded-lg border border-primary/20 bg-primary/5 px-2 py-0.5 text-[10px] text-primary/70 font-mono cursor-default hover:bg-primary/10 transition-colors">
                              <Terminal className="h-2.5 w-2.5" />
                              {toolName}
                            </span>
                          );
                        })}
                      </div>
                    </div>
                  );
                }
                if (msg.role === "system") {
                  return (
                    <div key={msg.id} className="flex justify-center">
                      <div className="flex max-w-[85%] items-start gap-2 rounded-2xl border border-border/50 bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
                        <Terminal className="mt-0.5 h-3.5 w-3.5 flex-shrink-0 text-primary/60" />
                        <div className="prose prose-sm max-w-none dark:prose-invert prose-p:my-0.5 prose-headings:text-foreground">
                          <ReactMarkdown>{msg.content}</ReactMarkdown>
                        </div>
                      </div>
                    </div>
                  );
                }
                return (
                  <div key={msg.id} className={`flex gap-3 ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
                    {msg.role === "assistant" && (
                      <div className="mt-0.5 flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-2xl bg-primary/10">
                        <Bot className="h-4 w-4 text-primary" />
                      </div>
                    )}
                    <div className="flex max-w-[78%] flex-col gap-1">
                      <div className={`break-words rounded-2xl px-4 py-3 text-sm ${msg.role === "user" ? "bg-primary text-primary-foreground shadow-sm" : "border bg-card prose prose-sm max-w-none dark:prose-invert"}`}>
                        {msg.role === "user" ? (
                          <>
                            {(msg as any)._images && (msg as any)._images.length > 0 && (
                              <div className="flex gap-1 mb-1 flex-wrap">
                                {(msg as any)._images.map((src: string, i: number) => (
                                  <img key={i} src={src} alt="" className="h-20 rounded-md" />
                                ))}
                              </div>
                            )}
                            <span className="whitespace-pre-wrap">{msg.content}</span>
                          </>
                        ) : streamingMsgId === msg.id && !msg.content ? (
                          <div className="flex h-5 items-center gap-1">
                            <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground [animation-delay:0ms]" />
                            <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground [animation-delay:150ms]" />
                            <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground [animation-delay:300ms]" />
                          </div>
                        ) : (
                          <>
                            <ReactMarkdown>{msg.content}</ReactMarkdown>
                            {streamingMsgId === msg.id && activeTool && (activeTool.name === "ask_agent" || activeTool.name === "dispatch_task") ? (
                              <div className="mt-2 flex items-center gap-2 rounded-xl border border-primary/20 bg-primary/5 px-3 py-2 text-xs text-primary">
                                <Loader2 className="h-3.5 w-3.5 animate-spin flex-shrink-0" />
                                <span className="font-medium">{activeTool.name === "dispatch_task" ? t("chat.waitingDispatch") : t("chat.waitingAgent")}: <span className="font-mono">{activeTool.detail}</span></span>
                                {elapsed > 0 && <span className="ml-auto text-muted-foreground">{elapsed}s</span>}
                              </div>
                            ) : streamingMsgId === msg.id ? (
                              <span className="ml-0.5 inline-block h-4 w-2 animate-pulse rounded-sm bg-primary/70 align-text-bottom" />
                            ) : doneMsgId === msg.id ? (
                              <span className="ml-1 inline-block text-xs text-green-500 align-text-bottom">✓</span>
                            ) : null}
                          </>
                        )}
                      </div>
                      {showSwarm && msg.role === "assistant" && msg.workers && msg.workers.length > 0 && (
                        <div className="flex flex-wrap gap-1 px-1">
                          {msg.workers.map((w) => (
                            <span key={w} className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-1 text-xs text-muted-foreground"><Network className="h-2.5 w-2.5" />{w}</span>
                          ))}
                        </div>
                      )}
                      {msg.role === "assistant" && (msg.tokenUsage || msg.isFallback) && (
                        <div className="flex gap-1 px-1 flex-wrap">
                          {msg.isFallback && (
                            <span className="inline-flex items-center gap-1 rounded-full bg-orange-500/10 border border-orange-500/30 px-2 py-0.5 text-xs text-orange-500" title="Antwort kam von einem Fallback-Modell">
                              ⚡ Fallback: {msg.model}
                            </span>
                          )}
                          {msg.tokenUsage && (msg.tokenUsage.input > 0 || msg.tokenUsage.output > 0) && (
                            <span className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground" title="Verbrauchte Tokens dieser Antwort (inkl. aller Tool-Runden)">
                              ↑ {msg.tokenUsage.input.toLocaleString()} ↓ {msg.tokenUsage.output.toLocaleString()} Tokens
                              {msg.tokenUsage.rounds && msg.tokenUsage.rounds > 1 && (
                                <span className="opacity-60">· {msg.tokenUsage.rounds} Runden</span>
                              )}
                              {(msg.tokenUsage.cache_read ?? 0) > 0 && (
                                <span className="text-green-500">· {msg.tokenUsage.cache_read!.toLocaleString()} cached</span>
                              )}
                              {(msg.tokenUsage.cache_write ?? 0) > 0 && (
                                <span className="text-blue-400">· {msg.tokenUsage.cache_write!.toLocaleString()} cache-write</span>
                              )}
                            </span>
                          )}
                        </div>
                      )}
                    </div>
                    {msg.role === "user" && (
                      <div className="mt-0.5 flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-2xl bg-secondary">
                        <User className="h-4 w-4" />
                      </div>
                    )}
                  </div>
                );
              })}
              {sending && messages[messages.length - 1]?.role !== "assistant" && (
                <div className="flex justify-start gap-3">
                  <div className="mt-0.5 flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-2xl bg-primary/10">
                    <Bot className="h-4 w-4 text-primary" />
                  </div>
                  <div className="rounded-2xl border bg-card px-4 py-3">
                    <div className="flex h-5 items-center gap-1">
                      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground [animation-delay:0ms]" />
                      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground [animation-delay:150ms]" />
                      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground [animation-delay:300ms]" />
                    </div>
                  </div>
                </div>
              )}
              <div ref={bottomRef} />
            </div>

            {error && <div className="border-t bg-destructive/10 px-5 py-3 text-xs text-destructive">{error}</div>}

            <div className="border-t px-3 py-3 sm:px-5 sm:py-4 relative flex-shrink-0">
              {showSuggest && suggestions.length > 0 && (
                <div className="absolute bottom-full left-3 right-3 sm:left-5 sm:right-5 z-10 mb-2 overflow-hidden rounded-2xl border bg-card shadow-lg">
                  {suggestions.map((s, i) => (
                    <button
                      key={s.cmd}
                      onMouseDown={(e) => { e.preventDefault(); setInput(s.cmd + " "); setShowSuggest(false); textareaRef.current?.focus(); }}
                      className={`flex w-full items-center gap-3 px-4 py-3 text-left text-sm transition ${i === suggestIdx ? "bg-accent text-accent-foreground" : "hover:bg-accent/50"}`}
                    >
                      <span className="font-mono text-xs text-primary">{s.cmd}</span>
                      <span className="text-xs text-muted-foreground">{s.desc}</span>
                    </button>
                  ))}
                </div>
              )}
              <div className="relative rounded-2xl border bg-muted/20 p-2 sm:rounded-3xl sm:p-3">
                {/* Keyboard-Hints nur auf Desktop */}
                <div className="mb-2 hidden sm:flex flex-wrap items-center justify-between gap-2 px-1">
                  <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
                    <span className="status-pill">{t("chat.enterSend")}</span>
                    <span className="status-pill">{t("chat.shiftEnterBreak")}</span>
                    <span className="status-pill">{t("chat.slashCommands")}</span>
                  </div>
                  {sending && (
                    <span className="status-pill status-pill-ok flex items-center gap-1">
                      {activeTool && (activeTool.name === "ask_agent" || activeTool.name === "dispatch_task") ? (
                        <><Loader2 className="h-3 w-3 animate-spin" />{activeTool.name === "dispatch_task" ? t("chat.waitingDispatch") : t("chat.waitingAgent")}: {activeTool.detail}</>
                      ) : t("chat.streamingActive")}
                      {elapsed > 0 && ` (${elapsed}s)`}
                    </span>
                  )}
                </div>
                {/* Streaming-Status auf Mobile */}
                {sending && (
                  <div className="mb-1 flex sm:hidden">
                    <span className="status-pill status-pill-ok text-xs flex items-center gap-1">
                      {activeTool && (activeTool.name === "ask_agent" || activeTool.name === "dispatch_task") ? (
                        <><Loader2 className="h-3 w-3 animate-spin" />{activeTool.detail}</>
                      ) : t("chat.streamingActive")}
                      {elapsed > 0 && ` (${elapsed}s)`}
                    </span>
                  </div>
                )}
                {showEmoji && (
                  <>
                  <div className="fixed inset-0 z-40" onClick={() => setShowEmoji(false)} />
                  <div className="absolute bottom-24 right-4 z-50">
                    <EmojiPicker
                      theme={Theme.DARK}
                      onEmojiClick={(e: EmojiClickData) => {
                        setInput(prev => prev + e.emoji);
                        textareaRef.current?.focus();
                      }}
                      height={380}
                      width={320}
                    />
                  </div>
                  </>
                )}
                {/* Coach Feedback */}
                {coachFeedback && !coachFeedback.ok && (
                  <div className="rounded-xl border border-orange-500/30 bg-orange-500/5 px-4 py-3 mb-2 text-sm space-y-2">
                    <p className="text-orange-400 font-medium">{coachFeedback.reason || "Dein Prompt könnte klarer sein"}</p>
                    {coachFeedback.suggestion && (
                      <p className="text-muted-foreground text-xs bg-muted/30 rounded-lg p-2 font-mono">{coachFeedback.suggestion}</p>
                    )}
                    <div className="flex gap-2">
                      {coachFeedback.suggestion && (
                        <button onClick={() => { setInput(coachFeedback.suggestion!); setCoachFeedback(null); }}
                          className="rounded-lg bg-primary px-3 py-1.5 text-xs text-primary-foreground hover:bg-primary/90">
                          {t("common.apply") || "Übernehmen"}
                        </button>
                      )}
                      <button onClick={() => { setCoachFeedback(null); send(input); }}
                        className="rounded-lg border px-3 py-1.5 text-xs text-muted-foreground hover:bg-muted">
                        Trotzdem senden
                      </button>
                    </div>
                  </div>
                )}
                {/* Coach Toggle */}
                <div className="flex items-center gap-2 mb-1">
                  <label className="flex items-center gap-1.5 text-xs text-muted-foreground cursor-pointer select-none">
                    <input type="checkbox" checked={coachEnabled} onChange={e => {
                      setCoachEnabled(e.target.checked);
                      localStorage.setItem("hh_prompt_coach", e.target.checked ? "1" : "0");
                    }} className="rounded" />
                    Prompt-Coach {coachChecking && <RefreshCw className="h-3 w-3 animate-spin" />}
                  </label>
                </div>
                {pendingImages.length > 0 && (
                  <div className="flex gap-2 mb-2 flex-wrap">
                    {pendingImages.map((img, i) => (
                      <div key={i} className="relative group">
                        <img src={img.preview} alt="" className="h-16 w-16 object-cover rounded-lg border" />
                        <button onClick={() => setPendingImages(prev => prev.filter((_, j) => j !== i))}
                          className="absolute -top-1 -right-1 bg-destructive text-white rounded-full w-4 h-4 text-[10px] flex items-center justify-center opacity-0 group-hover:opacity-100 transition">
                          X
                        </button>
                      </div>
                    ))}
                  </div>
                )}
                <div className="flex items-end gap-2">
                  <textarea
                    ref={textareaRef}
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    onKeyDown={onKeyDown}
                    onBlur={() => setTimeout(() => setShowSuggest(false), 150)}
                    placeholder={t("chat.messagePlaceholder")}
                    rows={1}
                    className="min-h-[44px] flex-1 min-w-0 resize-none rounded-xl border bg-background px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary sm:min-h-[52px] sm:rounded-2xl sm:px-4 sm:py-3"
                    style={{ maxHeight: "140px", overflowY: "auto" }}
                  />
                  {/* Emoji-Button auf Mobile ausblenden um Platz zu sparen */}
                  <button onClick={() => setShowEmoji(v => !v)} type="button" className="hidden sm:flex h-[52px] w-[52px] flex-shrink-0 items-center justify-center rounded-2xl border bg-background transition hover:bg-muted" aria-label="Toggle emoji picker">
                    <Smile className="h-5 w-5 text-muted-foreground" />
                  </button>
                  <input ref={fileInputRef} type="file" accept="image/*" multiple className="hidden"
                    onChange={e => {
                      const files = Array.from(e.target.files || []);
                      files.forEach(f => {
                        const reader = new FileReader();
                        reader.onload = () => {
                          const base64 = (reader.result as string).split(",")[1];
                          const media_type = f.type || "image/png";
                          const preview = reader.result as string;
                          setPendingImages(prev => [...prev, { data: base64, media_type, preview }]);
                        };
                        reader.readAsDataURL(f);
                      });
                      e.target.value = "";
                    }} />
                  <button onClick={() => fileInputRef.current?.click()} type="button"
                    className="hidden sm:flex p-2 border rounded-md bg-background hover:bg-muted transition-colors flex-shrink-0"
                    aria-label="Bild hochladen">
                    <ImagePlus className={`h-4 w-4 ${pendingImages.length > 0 ? "text-primary" : "text-muted-foreground"}`} />
                  </button>
                  {sending ? (
                    <button onClick={stop} className="flex h-[44px] w-[44px] flex-shrink-0 items-center justify-center rounded-xl bg-destructive text-destructive-foreground transition hover:bg-destructive/90 sm:h-[52px] sm:w-[52px] sm:rounded-2xl" aria-label="Stop generation">
                      <Square className="h-4 w-4" />
                    </button>
                  ) : (
                    <button onClick={() => send()} disabled={!input.trim() || coachChecking} className="flex h-[44px] w-[44px] flex-shrink-0 items-center justify-center rounded-xl bg-primary text-primary-foreground transition hover:bg-primary/90 disabled:opacity-40 sm:h-[52px] sm:w-[52px] sm:rounded-2xl" aria-label="Send message">
                      <Send className="h-4 w-4" />
                    </button>
                  )}
                </div>
              </div>
            </div>
          </div>

          <aside className="border-t bg-muted/10 p-4 sm:p-5 lg:sticky lg:top-24 lg:self-start lg:max-h-[calc(100vh-8rem)] lg:overflow-y-auto lg:border-t-0 lg:border-l">
            <div className="space-y-4">
              <div className="app-panel app-panel-muted p-4">
                <p className="metric-kicker">{t("chat.livePanel")}</p>
                <div className="mt-3 space-y-3">
                  <div className="flex items-start gap-3">
                    <span className="rounded-2xl bg-primary/12 p-2 text-primary"><Sparkles className="h-4 w-4" /></span>
                    <div>
                      <p className="text-sm font-medium">{t("chat.streaming")}</p>
                      <p className="text-xs text-muted-foreground">{sending ? t("chat.streamingBuilding") : t("chat.streamingIdle")}</p>
                    </div>
                  </div>
                  <div className="rounded-2xl border bg-background/75 px-3 py-3">
                    <p className="text-xs uppercase tracking-[0.16em] text-muted-foreground">{t("chat.activeTool")}</p>
                    {activeTool ? (
                      <div className="mt-2 space-y-1">
                        <p className="text-sm font-medium text-primary">{activeTool.name}</p>
                        <p className="break-all text-xs text-muted-foreground">{activeTool.detail || t("chat.noToolDetail")}</p>
                      </div>
                    ) : (
                      <p className="mt-2 text-xs text-muted-foreground">{t("chat.noTool")}</p>
                    )}
                  </div>
                </div>
              </div>

              <div className="app-panel p-4">
                <p className="metric-kicker">{t("chat.projectPanel")}</p>
                <div className="mt-3 space-y-3 text-sm">
                  <div className="rounded-2xl bg-secondary/40 px-3 py-3">
                    <p className="text-xs uppercase tracking-[0.16em] text-muted-foreground">{t("chat.projectPanel")}</p>
                    <p className="mt-1 font-medium">{projectName}</p>
                    <p className="font-mono text-xs text-muted-foreground">{id}</p>
                  </div>
                  <div className="rounded-2xl bg-secondary/40 px-3 py-3">
                    <p className="text-xs uppercase tracking-[0.16em] text-muted-foreground">{t("chat.bossModel")}</p>
                    <p className="mt-1 break-all font-medium">{bossModel.model ?? t("chat.notConfigured")}</p>
                    <p className="text-xs text-muted-foreground">{t("chat.temperature")} {bossModel.temperature ?? "—"}</p>
                  </div>
                  <div className="rounded-2xl bg-secondary/40 px-3 py-3">
                    <p className="text-xs uppercase tracking-[0.16em] text-muted-foreground">{t("chat.history")}</p>
                    <p className="mt-1 font-medium">
                      {messages.length !== 1
                        ? t("chat.messageCountPlural", { count: messages.length })
                        : t("chat.messageCount", { count: messages.length })}
                    </p>
                    <p className="text-xs text-muted-foreground">{showSwarm ? t("chat.swarmVisible") : t("chat.swarmCompact")}</p>
                  </div>
                </div>
              </div>

              <div className="app-panel p-4">
                <p className="metric-kicker">{t("chat.shortcuts")}</p>
                <div className="mt-3 space-y-2">
                  {SLASH_COMMANDS.map((command) => (
                    <div key={command.cmd} className="rounded-2xl border bg-background/70 px-3 py-2">
                      <p className="font-mono text-xs text-primary">{command.cmd}</p>
                      <p className="mt-1 text-xs text-muted-foreground">{command.desc}</p>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </aside>
        </div>
      </section>
    </div>
  );
}
