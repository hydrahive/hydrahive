import { useEffect, useRef, useState, useMemo, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { ArrowLeft, Send, Square, Bot, User, Terminal, Smile, Clock, X, Plus, RotateCcw, RefreshCw, ImagePlus } from "lucide-react";
import EmojiPicker, { type EmojiClickData, Theme } from "emoji-picker-react";
import { api, type SessionPreview } from "@/lib/api";
import ReactMarkdown from "react-markdown";
import { useTranslation } from "react-i18next";

interface Message {
  id:         string;
  role:       "user" | "assistant" | "system" | "tool";
  content:    string;
  tokenUsage?: { input: number; output: number; rounds?: number; cache_write?: number; cache_read?: number };
  model?: string;
  isFallback?: boolean;
}

let _msgCounter = 0;
function mkMsg(role: Message["role"], content: string): Message {
  return { id: `msg-${++_msgCounter}`, role, content };
}

export function AgentChatPage() {
  const { t } = useTranslation();
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const SLASH_COMMANDS = [
    { cmd: "/help",     desc: t("slashCommands.help") },
    { cmd: "/clear",    desc: t("slashCommands.clear") },
    { cmd: "/compact",  desc: t("slashCommands.compact") },
    { cmd: "/model",    desc: t("slashCommands.model") },
    { cmd: "/retry",    desc: t("slashCommands.retry") },
    { cmd: "/remember", desc: t("slashCommands.remember") },
    { cmd: "/history",  desc: t("slashCommands.history") },
  ];

  const [messages,    setMessages]    = useState<Message[]>([]);
  const [input,       setInput]       = useState("");
  const [sending,     setSending]     = useState(false);
  const [error,       setError]       = useState("");
  const [coachEnabled, setCoachEnabled] = useState(() => localStorage.getItem("hh_prompt_coach") === "1");
  const [coachFeedback, setCoachFeedback] = useState<{ ok: boolean; suggestion?: string; reason?: string } | null>(null);
  const [coachChecking, setCoachChecking] = useState(false);
  const [agentName,   setAgentName]   = useState(id ?? "");
  const [agentModel,  setAgentModel]  = useState<{ model?: string; temperature?: number }>({});
  const [showSuggest,  setShowSuggest]  = useState(false);
  const [showEmoji,    setShowEmoji]    = useState(false);
  const [suggestIdx,   setSuggestIdx]   = useState(0);
  const [showHistory,  setShowHistory]  = useState(false);
  const [sessions,     setSessions]     = useState<SessionPreview[]>([]);
  const [viewSession,  setViewSession]  = useState<{ id: string; messages: Message[]; startedAt: string } | null>(null);
  const [unrestricted] = useState(false);
  const [pendingImages, setPendingImages] = useState<{data: string; media_type: string; preview: string}[]>([]);
  const [lightboxSrc, setLightboxSrc] = useState<string | null>(null);
  const fileInputRef    = useRef<HTMLInputElement>(null);

  const bottomRef       = useRef<HTMLDivElement>(null);
  const textareaRef     = useRef<HTMLTextAreaElement>(null);
  const abortRef        = useRef<AbortController | null>(null);
  const elapsedTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [elapsed, setElapsed] = useState(0);

  const suggestions = input.startsWith("/")
    ? SLASH_COMMANDS.filter(c => c.cmd.startsWith(input.split(" ")[0]))
    : [];

  useEffect(() => {
    if (!id) return;
    api.get<Record<string, unknown>>(`/agents/${id}`)
      .then(a => {
        const cfg = (a as any)?.config;
        if (cfg?.identity) setAgentName(cfg.identity);
        if (cfg?.llm) setAgentModel(cfg.llm);
      })
      .catch(e => console.error("Failed to load agent config", e));
    api.get<{ session_id: string | null; messages: { role: string; content: string }[]; count: number }>(
      `/agents/${id}/session/history`
    )
      .then(d => {
        const loaded = d.messages
          .filter((m: any) => m.role === "user" || m.role === "assistant" || m.role === "tool")
          .map((m: any) => {
            const msg = mkMsg(m.role as Message["role"], m.content);
            if (m.metadata?.input_tokens || m.metadata?.output_tokens) {
              msg.tokenUsage = { input: m.metadata.input_tokens || 0, output: m.metadata.output_tokens || 0, rounds: m.metadata.rounds, cache_write: m.metadata.cache_write_tokens || 0, cache_read: m.metadata.cache_read_tokens || 0 };
            }
            return msg;
          });
        if (loaded.length > 0) setMessages(loaded);
      })
      .catch(e => console.error("Failed to load agent session history", e));
  }, [id]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    setShowSuggest(suggestions.length > 0 && input.length > 0);
    setSuggestIdx(0);
  }, [input]);

  useEffect(() => {
    if (!showHistory || !id) return;
    api.listSessions(id, 30).then(d => setSessions(d.sessions)).catch(e => console.error("Failed to list agent sessions", e));
  }, [showHistory, id]);

  async function openSession(sid: string) {
    if (!id) return;
    try {
      const d = await api.getSessionById(id, sid);
      const msgs = d.messages
        .filter(m => m.role === "user" || m.role === "assistant" || m.role === "tool")
        .map(m => mkMsg(m.role as "user" | "assistant", m.content));
      setViewSession({ id: d.id, messages: msgs, startedAt: d.started_at });
      setShowHistory(false);
    } catch {}
  }

  async function resumeSession(sid: string) {
    if (!id) return;
    try {
      const d = await api.resumeSession(id, sid);
      const msgs = d.messages
        .filter(m => m.role === "user" || m.role === "assistant" || m.role === "tool")
        .map(m => mkMsg(m.role as "user" | "assistant", m.content));
      setMessages(msgs);
      setViewSession(null);
      setShowHistory(false);
    } catch {}
  }

  function sysMsg(content: string) {
    setMessages(ms => [...ms, mkMsg("system", content)]);
  }

  function handleSlashCommand(cmd: string): boolean {
    const base = cmd.trim().split(/\s+/)[0].toLowerCase();

    if (base === "/help") {
      sysMsg("**" + t("slashCommands.help") + ":**\n\n" +
        SLASH_COMMANDS.map(c => `\`${c.cmd}\` — ${c.desc}`).join("\n"));
      return true;
    }
    if (base === "/clear") {
      setMessages([]);
      api.delete(`/agents/${id}/session`).catch(e => console.error("Failed to clear agent session", e));
      sysMsg(t("slashCommands.clear") + ".");
      return true;
    }
    if (base === "/model") {
      const model = agentModel.model ?? t("agentChat.notConfigured" as any) ?? "nicht konfiguriert";
      const temp  = agentModel.temperature ?? "—";
      sysMsg(`**Aktuelles Modell:** \`${model}\`\n**Temperatur:** ${temp}`);
      return true;
    }
    if (base === "/retry") {
      const lastUser = [...messages].reverse().find(m => m.role === "user");
      if (!lastUser) { sysMsg("Keine vorherige Nachricht zum Wiederholen."); return true; }
      setInput(lastUser.content);
      setTimeout(() => textareaRef.current?.focus(), 0);
      return true;
    }
    if (base === "/compact") {
      sysMsg("Kompaktiere Konversation...");
      api.post<{ compacted: boolean; original_count?: number; summary?: string; reason?: string }>(`/agents/${id}/session/compact`, {})
        .then((d) => {
          if (d.compacted) {
            setMessages([
              { id: `compact-sys-${Date.now()}`,  role: "system",    content: `Konversation kompaktiert (${d.original_count} Nachrichten → Zusammenfassung).` },
              { id: `compact-usr-${Date.now()}`,  role: "user",      content: `[Zusammenfassung der bisherigen Konversation (${d.original_count} Nachrichten)]\n\n${d.summary ?? ""}` },
              { id: `compact-ast-${Date.now()}`,  role: "assistant", content: "Verstanden. Ich habe die Zusammenfassung der bisherigen Konversation gelesen und kann nahtlos weiterarbeiten." },
            ]);
          } else {
            sysMsg(`Kompaktierung fehlgeschlagen: ${d.reason}`);
          }
        })
        .catch((e: Error) => sysMsg(`Fehler: ${e.message}`));
      return true;
    }
    if (base === "/remember") {
      const parts = cmd.trim().split(/\s+/);
      const filename = parts[1]
        ? parts[1].replace(/[^a-z0-9_-]/gi, "-").toLowerCase()
        : new Date().toISOString().slice(0, 10);
      const history = messages
        .filter(m => m.role === "user" || m.role === "assistant" || m.role === "tool")
        .slice(-30)
        .map(m => `**${m.role === "user" ? "User" : "Agent"}:** ${m.content}`)
        .join("\n\n");
      if (!history) { sysMsg("Kein Chat-Verlauf zum Speichern."); return true; }
      const content = `# Session: ${new Date().toLocaleString("de")}\n\n${history}`;
      api.post(`/agents/${id}/memory`, { filename, content })
        .then(() => sysMsg(`Gespeichert als \`${filename}.md\` im Gedächtnis.`))
        .catch((e: Error) => sysMsg(`Fehler: ${e.message}`));
      return true;
    }
    if (base === "/history") {
      setViewSession(null);
      setShowHistory(true);
      return true;
    }
    sysMsg(`Unbekannter Command: \`${base}\`. Tippe \`/help\`.`);
    return true;
  }

  async function stop() {
    if (abortRef.current) abortRef.current.abort();
    if (id) {
      const token = localStorage.getItem("hydrahive_token") || "";
      fetch(`/api/agents/${id}/interrupt`, { method: "POST", headers: { Authorization: `Bearer ${token}` } }).catch(e => console.error("Failed to interrupt agent", e));
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

    const userMsg      = mkMsg("user", content);
    // Bilder als Preview im User-Message anzeigen
    if (pendingImages.length > 0) {
      (userMsg as any)._images = pendingImages.map(i => i.preview);
    }
    let currentAsst = mkMsg("assistant", "");
    let hadToolCalls = false;
    setMessages(ms => [...ms, userMsg]);
    setPendingImages([]);
    setSending(true);
    setElapsed(0);
    const controller = new AbortController();
    abortRef.current = controller;
    elapsedTimerRef.current = setInterval(() => setElapsed((s) => s + 1), 1000);

    try {
      const token = localStorage.getItem("hydrahive_token") || "";
      const res = await fetch(`/api/agents/${id}/message/stream`, {
        method:  "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body:    JSON.stringify({
          content,
          ...(unrestricted ? { execution_mode: "unrestricted" } : {}),
          ...(pendingImages.length > 0 ? { images: pendingImages.map(i => ({ data: i.data, media_type: i.media_type })) } : {}),
        }),
        signal:  controller.signal,
      });
      if (!res.ok) {
        const e = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(e.detail || `HTTP ${res.status}`);
      }

      setMessages(ms => [...ms, currentAsst]);
      const reader  = res.body!.getReader();
      const decoder = new TextDecoder();
      let buffer    = "";

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
                if (hadToolCalls) { currentAsst = mkMsg("assistant", ""); setMessages(ms => [...ms, currentAsst]); hadToolCalls = false; }
                setMessages(ms => ms.map(m =>
                  m.id === currentAsst.id ? { ...m, content: m.content + evt.text } : m
                ));
              } else if (evt.tool_image !== undefined) {
                // #414: Bild aus Tool-Result (z.B. browser_screenshot)
                const imgMsg = mkMsg("tool" as Message["role"], `__IMG__${evt.tool_name || "screenshot"}|${evt.tool_image}`);
                setMessages(ms => [...ms, imgMsg]);
              } else if (evt.tool_call !== undefined) {
                const toolMsg = mkMsg("tool" as Message["role"], `${evt.tool_call}|${evt.tool_detail || evt.tool_call}`);
                setMessages(ms => [...ms, toolMsg]);
                hadToolCalls = true;
              } else if (evt.done) {
                const updates: Partial<Message> = {};
                if (evt.usage && (evt.usage.input > 0 || evt.usage.output > 0))
                  updates.tokenUsage = evt.usage;
                if (evt.is_fallback)
                  Object.assign(updates, { model: evt.model, isFallback: true });
                if (Object.keys(updates).length > 0)
                  setMessages(ms => ms.map(m => m.id === currentAsst.id ? { ...m, ...updates } : m));
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
        setMessages(ms => ms.filter(m => m.id !== userMsg.id && m.id !== currentAsst.id));
        setInput(content);
      }
    } finally {
      setSending(false);
      abortRef.current = null;
      if (elapsedTimerRef.current) { clearInterval(elapsedTimerRef.current); elapsedTimerRef.current = null; }
      setElapsed(0);
      textareaRef.current?.focus();
    }
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (showSuggest && suggestions.length > 0) {
      if (e.key === "ArrowDown") { e.preventDefault(); setSuggestIdx(i => (i + 1) % suggestions.length); return; }
      if (e.key === "ArrowUp")   { e.preventDefault(); setSuggestIdx(i => (i - 1 + suggestions.length) % suggestions.length); return; }
      if (e.key === "Tab" || (e.key === "Enter" && showSuggest)) {
        e.preventDefault();
        setInput(suggestions[suggestIdx].cmd + " ");
        setShowSuggest(false);
        return;
      }
      if (e.key === "Escape") { setShowSuggest(false); return; }
    }
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
  }

  return (
    <div className="flex flex-col h-full min-h-0 overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3 border-b flex-shrink-0">
        <button onClick={() => navigate("/agents")}
          className="p-1.5 rounded-md hover:bg-accent transition-colors"
          aria-label="Back to agents">
          <ArrowLeft className="h-4 w-4" />
        </button>
        <div className="w-7 h-7 rounded-full bg-primary/10 flex items-center justify-center flex-shrink-0">
          <Bot className="h-4 w-4 text-primary" />
        </div>
        <div className="flex-1 min-w-0">
          <h1 className="text-sm font-semibold truncate">{agentName}</h1>
          <p className="text-xs text-muted-foreground font-mono">{agentModel.model ?? id}</p>
        </div>
        <button onClick={() => { setShowHistory(h => !h); setViewSession(null); }}
          className={`p-1.5 rounded-md transition-colors ${showHistory ? "bg-accent text-accent-foreground" : "hover:bg-accent text-muted-foreground"}`}
          title="Chat-Verlauf"
          aria-label="Toggle chat history">
          <Clock className="h-4 w-4" />
        </button>
      </div>

      {/* History Panel */}
      {showHistory && (
        <div className="flex-1 overflow-y-auto border-b bg-muted/20">
          <div className="flex items-center justify-between px-4 py-2 border-b">
            <span className="text-xs font-medium text-muted-foreground">{t("chat.pastSessions")}</span>
            <button onClick={() => setShowHistory(false)} className="p-1 rounded hover:bg-accent" aria-label="Close history">
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
          {sessions.length === 0 ? (
            <p className="text-xs text-muted-foreground text-center py-8">{t("chat.noPastSessions")}</p>
          ) : (
            <div className="divide-y">
              {sessions.map(s => (
                <div key={s.id} className="flex items-stretch hover:bg-accent/50 transition-colors">
                  <button onClick={() => openSession(s.id)}
                    className="flex-1 text-left px-4 py-3">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs font-medium">{new Date(s.started_at).toLocaleString("de")}</span>
                      <span className="text-xs text-muted-foreground">{s.message_count} Nachr.</span>
                    </div>
                    {s.preview && (
                      <p className="text-xs text-muted-foreground truncate mt-0.5">{s.preview}</p>
                    )}
                  </button>
                  <button onClick={() => resumeSession(s.id)}
                    title="Chat fortsetzen"
                    aria-label="Resume session"
                    className="flex items-center gap-1 px-3 text-xs text-primary hover:bg-primary/10 border-l transition-colors flex-shrink-0">
                    <RotateCcw className="h-3.5 w-3.5" />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* View past session banner */}
      {viewSession && (
        <div className="flex items-center justify-between px-4 py-2 bg-amber-500/10 border-b text-xs flex-shrink-0 gap-2">
          <span className="text-amber-600 dark:text-amber-400 font-medium truncate min-w-0">
            {t("chat.pastSession")} — {new Date(viewSession.startedAt).toLocaleString("de")}
          </span>
          <div className="flex gap-2 flex-shrink-0">
            <button onClick={() => { setViewSession(null); setShowHistory(true); }}
              className="flex items-center gap-1 px-2 py-1 rounded hover:bg-accent transition-colors text-muted-foreground">
              <ArrowLeft className="h-3 w-3" /> {t("chat.back")}
            </button>
            <button onClick={() => viewSession && resumeSession(viewSession.id)}
              className="flex items-center gap-1 px-2 py-1 rounded bg-primary text-primary-foreground hover:bg-primary/90 transition-colors">
              <RotateCcw className="h-3 w-3" /> {t("chat.resume")}
            </button>
            <button onClick={() => { setViewSession(null); api.delete(`/agents/${id}/session`).catch(e => console.error("Failed to delete agent session", e)); setMessages([]); }}
              className="flex items-center gap-1 px-2 py-1 rounded border hover:bg-accent transition-colors text-muted-foreground">
              <Plus className="h-3 w-3" /> {t("chat.newChat")}
            </button>
          </div>
        </div>
      )}

      {/* #384: useMemo für Message-Liste */}
      <div className={`flex-1 overflow-y-auto overflow-x-hidden p-3 sm:p-4 space-y-4 ${showHistory ? "hidden" : ""}`}>
        {(viewSession ? viewSession.messages : messages).length === 0 && !viewSession && (
          <div className="flex flex-col items-center justify-center h-full text-center space-y-3 text-muted-foreground">
            <Bot className="h-10 w-10" />
            <p className="text-sm">{t("agentChat.emptyChat", { name: agentName })}</p>
            <p className="text-xs opacity-60">{t("agentChat.slashTip")} <code className="bg-muted px-1 rounded">/help</code> {t("agentChat.slashTip2")}</p>
          </div>
        )}
        {(viewSession ? viewSession.messages : messages).map((msg) => {
          if (msg.role === "tool") {
            const allMsgs = viewSession ? viewSession.messages : messages;
            const msgIdx = allMsgs.indexOf(msg);
            if (msgIdx > 0 && allMsgs[msgIdx - 1]?.role === "tool") return null;
            const toolGroup: typeof allMsgs = [msg];
            for (let i = msgIdx + 1; i < allMsgs.length && allMsgs[i].role === "tool"; i++) toolGroup.push(allMsgs[i]);
            const badges = toolGroup.filter(tm => !tm.content.startsWith("__IMG__"));
            const images = toolGroup.filter(tm => tm.content.startsWith("__IMG__"));
            return (
              <div key={msg.id} className="flex flex-col items-center gap-2">
                {badges.length > 0 && (
                  <div className="flex flex-wrap gap-1.5 max-w-[85%] justify-center">
                    {badges.map(tm => {
                      const [toolName, ...dp] = tm.content.split("|");
                      return (
                        <span key={tm.id} title={dp.join("|") || toolName}
                          className="inline-flex items-center gap-1 rounded-lg border border-primary/20 bg-primary/5 px-2 py-0.5 text-[10px] text-primary/70 font-mono cursor-default hover:bg-primary/10 transition-colors">
                          <Terminal className="h-2.5 w-2.5" />
                          {toolName}
                        </span>
                      );
                    })}
                  </div>
                )}
                {images.map(tm => {
                  const [label, ...srcParts] = tm.content.replace("__IMG__", "").split("|");
                  const src = srcParts.join("|");
                  return (
                    <div key={tm.id} className="max-w-[75%] rounded-lg border bg-card p-2">
                      <img src={src} alt={label} className="rounded-md max-h-[400px] w-auto cursor-pointer hover:opacity-80 transition-opacity" onClick={() => setLightboxSrc(src)} />
                      <div className="text-[10px] text-muted-foreground mt-1 text-center font-mono">{label}</div>
                    </div>
                  );
                })}
              </div>
            );
          }
          if (msg.role === "system") {
            return (
              <div key={msg.id} className="flex justify-center">
                <div className="flex items-start gap-2 max-w-[85%] bg-muted/40 border border-border/50 rounded-lg px-3 py-2 text-xs text-muted-foreground">
                  <Terminal className="h-3.5 w-3.5 mt-0.5 flex-shrink-0 text-primary/60" />
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
                <div className="w-7 h-7 rounded-full bg-primary/10 flex items-center justify-center flex-shrink-0 mt-0.5">
                  <Bot className="h-4 w-4 text-primary" />
                </div>
              )}
              <div className="flex flex-col gap-1 max-w-[75%]">
                <div className={`rounded-lg px-3 py-2 text-sm break-words ${
                  msg.role === "user"
                    ? "bg-primary text-primary-foreground"
                    : "bg-card border prose prose-sm max-w-none dark:prose-invert"
                }`}>
                  {msg.role === "user" ? (
                    <>
                      {(msg as any)._images && (
                        <div className="flex gap-1 mb-1 flex-wrap">
                          {(msg as any)._images.map((src: string, i: number) => (
                            <img key={i} src={src} alt="" className="h-20 rounded-md" />
                          ))}
                        </div>
                      )}
                      <span className="whitespace-pre-wrap">{msg.content}</span>
                    </>
                  ) : (
                    <ReactMarkdown>{msg.content}</ReactMarkdown>
                  )}
                  {msg.role === "assistant" && (msg.tokenUsage || msg.isFallback) && (
                    <div className="flex gap-1 px-1 pt-1 flex-wrap">
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
              </div>
              {msg.role === "user" && (
                <div className="w-7 h-7 rounded-full bg-secondary flex items-center justify-center flex-shrink-0 mt-0.5">
                  <User className="h-4 w-4" />
                </div>
              )}
            </div>
          );
        })}
        {sending && messages[messages.length - 1]?.role !== "assistant" && (
          <div className="flex gap-3 justify-start">
            <div className="w-7 h-7 rounded-full bg-primary/10 flex items-center justify-center flex-shrink-0 mt-0.5">
              <Bot className="h-4 w-4 text-primary" />
            </div>
            <div className="bg-card border rounded-lg px-3 py-2">
              <div className="flex gap-1 items-center h-5">
                <span className="w-1.5 h-1.5 rounded-full bg-muted-foreground animate-bounce [animation-delay:0ms]" />
                <span className="w-1.5 h-1.5 rounded-full bg-muted-foreground animate-bounce [animation-delay:150ms]" />
                <span className="w-1.5 h-1.5 rounded-full bg-muted-foreground animate-bounce [animation-delay:300ms]" />
              </div>
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {error && !viewSession && (
        <div className="px-4 py-2 text-xs text-destructive bg-destructive/10 border-t flex-shrink-0">
          {error}
        </div>
      )}

      {!viewSession && !showHistory && <div className="px-3 py-3 sm:px-4 border-t flex-shrink-0 relative">
        {showSuggest && suggestions.length > 0 && (
          <div className="absolute bottom-full left-3 right-3 sm:left-4 sm:right-4 mb-1 bg-card border rounded-md shadow-lg overflow-hidden z-10">
            {suggestions.map((s, i) => (
              <button key={s.cmd}
                onMouseDown={e => { e.preventDefault(); setInput(s.cmd + " "); setShowSuggest(false); textareaRef.current?.focus(); }}
                className={`w-full flex items-center gap-3 px-3 py-2 text-sm text-left transition-colors ${
                  i === suggestIdx ? "bg-accent text-accent-foreground" : "hover:bg-accent/50"
                }`}>
                <span className="font-mono text-primary text-xs">{s.cmd}</span>
                <span className="text-muted-foreground text-xs">{s.desc}</span>
              </button>
            ))}
          </div>
        )}
        {showEmoji && (
          <>
          <div className="fixed inset-0 z-40" onClick={() => setShowEmoji(false)} />
          <div className="absolute bottom-16 right-4 z-50">
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
                  Übernehmen
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
        {/* Bild-Preview (#414) */}
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
        <div className="flex gap-2 items-end">
          <textarea ref={textareaRef} value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            onBlur={() => setTimeout(() => setShowSuggest(false), 150)}
            placeholder={t("agentChat.messagePlaceholder")}
            rows={1}
            className="flex-1 min-w-0 px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-ring resize-none"
            style={{ maxHeight: "120px", overflowY: "auto" }} />
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
            className="flex p-2 border rounded-md bg-background hover:bg-muted transition-colors flex-shrink-0"
            aria-label="Bild hochladen">
            <ImagePlus className={`h-4 w-4 ${pendingImages.length > 0 ? "text-primary" : "text-muted-foreground"}`} />
          </button>
          <button onClick={() => setShowEmoji(v => !v)} type="button"
            className="hidden sm:flex p-2 border rounded-md bg-background hover:bg-muted transition-colors flex-shrink-0"
            aria-label="Toggle emoji picker">
            <Smile className="h-4 w-4 text-muted-foreground" />
          </button>
          {sending ? (
            <button onClick={stop}
              className="p-2 bg-destructive text-destructive-foreground rounded-md hover:bg-destructive/90 transition-colors flex-shrink-0"
              title={`Abbrechen${elapsed > 0 ? ` (${elapsed}s)` : ""}`}
              aria-label="Stop generation">
              <Square className="h-4 w-4" />
            </button>
          ) : (
            <button onClick={() => send()} disabled={!input.trim() || coachChecking}
              className="p-2 bg-primary text-primary-foreground rounded-md hover:bg-primary/90 disabled:opacity-50 transition-colors flex-shrink-0"
              aria-label="Send message">
              <Send className="h-4 w-4" />
            </button>
          )}
        </div>
      </div>}
      {lightboxSrc && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 cursor-pointer" onClick={() => setLightboxSrc(null)}>
          <img src={lightboxSrc} alt="Fullscreen" className="max-w-[90vw] max-h-[90vh] rounded-lg shadow-2xl" />
        </div>
      )}
    </div>
  );
}
