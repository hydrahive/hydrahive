import { useEffect, useRef, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { ArrowLeft, Send, Bot, User, Network, Terminal } from "lucide-react";
import { api } from "@/lib/api";
import ReactMarkdown from "react-markdown";

interface Message {
  id:      string;
  role:    "user" | "assistant" | "system";
  content: string;
  workers?: string[];
}

const SLASH_COMMANDS = [
  { cmd: "/help",   desc: "Verfügbare Commands anzeigen" },
  { cmd: "/clear",  desc: "Chat-Verlauf leeren" },
  { cmd: "/status", desc: "Projekt- und Agent-Status anzeigen" },
  { cmd: "/retry",  desc: "Letzte Nachricht nochmal senden" },
  { cmd: "/model",  desc: "Aktuelles LLM-Modell anzeigen" },
];

let _msgCounter = 0;
function mkMsg(role: Message["role"], content: string, workers?: string[]): Message {
  return { id: `msg-${++_msgCounter}`, role, content, workers };
}

export function ChatPage() {
  const { id } = useParams<{ id: string }>();
  const navigate  = useNavigate();

  const [messages,     setMessages]     = useState<Message[]>([]);
  const [input,        setInput]        = useState("");
  const [sending,      setSending]      = useState(false);
  const [error,        setError]        = useState("");
  const [projectName,  setProjectName]  = useState(id ?? "");
  const [showSwarm,    setShowSwarm]    = useState(false);
  const [projectData,  setProjectData]  = useState<Record<string, unknown>>({});
  const [bossModel,    setBossModel]    = useState<{model?:string;temperature?:number}>({});
  const [showSuggest,    setShowSuggest]    = useState(false);
  const [suggestIdx,     setSuggestIdx]     = useState(0);
  const [activeTool,     setActiveTool]     = useState<{name:string;detail:string} | null>(null);
  const [streamingMsgId, setStreamingMsgId] = useState<string | null>(null);
  const [doneMsgId,      setDoneMsgId]      = useState<string | null>(null);

  const bottomRef   = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  function toolDetail(name: string, input: Record<string,unknown>): string {
    if (name === "read_system_file" || name === "file_read")   return String(input.path ?? input.file_path ?? "");
    if (name === "write_system_file" || name === "file_write") return String(input.path ?? input.file_path ?? "");
    if (name === "shell_exec")    return String(input.command ?? "").slice(0, 60);
    if (name === "web_search")    return String(input.query ?? "");
    if (name === "http_request")  return String(input.url ?? "");
    if (name === "ask_agent")     return String(input.target ?? "");
    if (name === "delegate_agent")return String(input.target ?? "");
    if (name === "write_memory" || name === "read_memory") return String(input.filename ?? "");
    if (name === "write_handoff" || name === "read_handoff") return String(input.to_agent ?? input.handoff_id ?? "");
    return "";
  }

  const suggestions = input.startsWith("/")
    ? SLASH_COMMANDS.filter(c => c.cmd.startsWith(input.split(" ")[0]))
    : [];

  useEffect(() => {
    if (!id) return;
    api.get<Record<string, unknown>>(`/projects/${id}`)
      .then(d => {
        setProjectData(d);
        const cfg = d.config as { identity?: { name?: string }; agents?: { boss?: string }; chat?: { show_swarm?: boolean } } | undefined;
        if (cfg?.identity?.name) setProjectName(cfg.identity.name);
        if (cfg?.chat?.show_swarm) setShowSwarm(true);
        // Boss-Agent-Modell laden
        const bossId = cfg?.agents?.boss;
        if (bossId) {
          api.get<Record<string, unknown>>(`/agents/${bossId}`)
            .then(a => {
              const llm = (a as any)?.config?.llm as { model?: string; temperature?: number } | undefined;
              if (llm) setBossModel(llm);
            })
            .catch(() => {});
        }
      })
      .catch(() => {});
    api.sessionHistory(id)
      .then(d => {
        const loaded = d.messages
          .filter(m => m.role === "user" || m.role === "assistant")
          .map(m => mkMsg(m.role as "user" | "assistant", m.content));
        if (loaded.length > 0) setMessages(loaded);
      })
      .catch(() => {});
  }, [id]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    setShowSuggest(suggestions.length > 0 && input.length > 0);
    setSuggestIdx(0);
  }, [input]);

  function sysMsg(content: string) {
    setMessages(ms => [...ms, mkMsg("system", content)]);
  }

  function handleSlashCommand(cmd: string): boolean {
    const parts = cmd.trim().split(/\s+/);
    const base  = parts[0].toLowerCase();

    if (base === "/help") {
      sysMsg(
        "**Verfügbare Commands:**\n\n" +
        SLASH_COMMANDS.map(c => `\`${c.cmd}\` — ${c.desc}`).join("\n")
      );
      return true;
    }

    if (base === "/clear") {
      setMessages([]);
      sysMsg("Chat-Verlauf geleert.");
      return true;
    }

    if (base === "/status") {
      const cfg     = projectData.config as { agents?: { boss?: string; workers?: string[] } } | undefined;
      const boss    = cfg?.agents?.boss ?? "—";
      const workers = cfg?.agents?.workers?.join(", ") || "—";
      const model   = bossModel.model ?? "nicht konfiguriert";
      sysMsg(
        `**Projekt:** ${projectName} (\`${id}\`)\n` +
        `**Boss-Agent:** ${boss}\n` +
        `**Worker-Agenten:** ${workers}\n` +
        `**LLM-Modell:** ${model}`
      );
      return true;
    }

    if (base === "/retry") {
      const lastUser = [...messages].reverse().find(m => m.role === "user");
      if (!lastUser) {
        sysMsg("Keine vorherige Nachricht zum Wiederholen.");
        return true;
      }
      setInput(lastUser.content);
      setTimeout(() => textareaRef.current?.focus(), 0);
      return true;
    }

    if (base === "/model") {
      const model = bossModel.model ?? "nicht konfiguriert";
      const temp  = bossModel.temperature ?? "—";
      sysMsg(`**Aktuelles Modell:** \`${model}\`\n**Temperatur:** ${temp}`);
      return true;
    }

    sysMsg(`Unbekannter Command: \`${base}\`. Tippe \`/help\` für eine Übersicht.`);
    return true;
  }

  async function send() {
    if (!input.trim() || sending || !id) return;
    const content = input.trim();
    setInput("");
    setError("");
    setShowSuggest(false);

    if (content.startsWith("/")) {
      handleSlashCommand(content);
      return;
    }

    const userMsg      = mkMsg("user", content);
    const assistantMsg = mkMsg("assistant", "");
    setMessages(ms => [...ms, userMsg]);
    setSending(true);

    try {
      const token = localStorage.getItem("octopos_token") || "";
      const res = await fetch(`/api/projects/${id}/message/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ content }),
      });
      if (!res.ok) {
        const e = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(e.detail || `HTTP ${res.status}`);
      }

      setMessages(ms => [...ms, assistantMsg]);
      setStreamingMsgId(assistantMsg.id);
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
                setActiveTool(null);
                setMessages(ms => ms.map(m =>
                  m.id === assistantMsg.id ? { ...m, content: m.content + evt.text } : m
                ));
              } else if (evt.tool_call !== undefined) {
                setActiveTool({ name: evt.tool_call, detail: toolDetail(evt.tool_call, evt.tool_input ?? {}) });
              } else if (evt.done) {
                break outer;
              } else if (evt.error) {
                throw new Error(evt.error);
              }
            } catch (parseErr) {
              if (parseErr instanceof Error && parseErr.message !== "Unexpected end of JSON input") throw parseErr;
            }
          }
        }
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Fehler beim Senden");
      setMessages(ms => ms.filter(m => m.id !== userMsg.id && m.id !== assistantMsg.id));
      setInput(content);
    } finally {
      setSending(false); setActiveTool(null);
      if (streamingMsgId) setDoneMsgId(streamingMsgId);
      setStreamingMsgId(null);
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
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3 border-b flex-shrink-0">
        <button
          onClick={() => navigate("/projects")}
          className="p-1.5 rounded-md hover:bg-accent transition-colors"
        >
          <ArrowLeft className="h-4 w-4" />
        </button>
        <div className="flex-1 min-w-0">
          <h1 className="text-sm font-semibold truncate">{projectName}</h1>
          <p className="text-xs text-muted-foreground font-mono">{id}</p>
        </div>
        <button
          onClick={() => setShowSwarm(s => !s)}
          title={showSwarm ? "Swarm-Ansicht ausblenden" : "Swarm-Ansicht einblenden"}
          className={`p-1.5 rounded-md transition-colors ${
            showSwarm
              ? "bg-primary/10 text-primary"
              : "text-muted-foreground hover:bg-accent"
          }`}
        >
          <Network className="h-4 w-4" />
        </button>
      </div>

      {/* Nachrichten */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full text-center space-y-3 text-muted-foreground">
            <Bot className="h-10 w-10" />
            <p className="text-sm">Schreib eine Nachricht, um den Boss-Agenten zu erreichen.</p>
            <p className="text-xs opacity-60">Tippe <code className="bg-muted px-1 rounded">/help</code> für verfügbare Commands.</p>
          </div>
        )}
        {messages.map((msg) => {
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
                  {msg.role === "user"
                    ? <span className="whitespace-pre-wrap">{msg.content}</span>
                    : streamingMsgId === msg.id && !msg.content
                      ? <div className="flex gap-1 items-center h-5">
                          <span className="w-1.5 h-1.5 rounded-full bg-muted-foreground animate-bounce [animation-delay:0ms]" />
                          <span className="w-1.5 h-1.5 rounded-full bg-muted-foreground animate-bounce [animation-delay:150ms]" />
                          <span className="w-1.5 h-1.5 rounded-full bg-muted-foreground animate-bounce [animation-delay:300ms]" />
                        </div>
                      : <><ReactMarkdown>{msg.content}</ReactMarkdown>
                          {streamingMsgId === msg.id
                            ? <span className="inline-block w-2 h-4 bg-primary/70 animate-pulse ml-0.5 align-text-bottom rounded-sm" />
                            : doneMsgId === msg.id && <span className="inline-block text-xs text-green-500 ml-1 align-text-bottom">✓</span>
                          }
                        </>
                  }
                </div>
                {showSwarm && msg.role === "assistant" && msg.workers && msg.workers.length > 0 && (
                  <div className="flex flex-wrap gap-1 px-1">
                    {msg.workers.map(w => (
                      <span key={w} className="inline-flex items-center gap-1 text-xs bg-muted text-muted-foreground rounded px-1.5 py-0.5">
                        <Network className="h-2.5 w-2.5" />
                        {w}
                      </span>
                    ))}
                  </div>
                )}
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

      {/* Tool-Status */}
      {activeTool && (
        <div className="px-4 py-1.5 text-xs text-muted-foreground bg-muted/50 border-t flex-shrink-0 flex items-center gap-2 min-w-0">
          <span className="flex gap-0.5 flex-shrink-0">
            <span className="w-1 h-1 rounded-full bg-primary animate-bounce [animation-delay:0ms]" />
            <span className="w-1 h-1 rounded-full bg-primary animate-bounce [animation-delay:150ms]" />
            <span className="w-1 h-1 rounded-full bg-primary animate-bounce [animation-delay:300ms]" />
          </span>
          <code className="font-mono text-primary flex-shrink-0">{activeTool.name}</code>
          {activeTool.detail && <span className="truncate text-muted-foreground">{activeTool.detail}</span>}
        </div>
      )}

      {/* Fehler */}
      {error && (
        <div className="px-4 py-2 text-xs text-destructive bg-destructive/10 border-t flex-shrink-0">
          {error}
        </div>
      )}

      {/* Eingabe */}
      <div className="px-4 py-3 border-t flex-shrink-0 relative">
        {/* Slash-Command Autocomplete */}
        {showSuggest && suggestions.length > 0 && (
          <div className="absolute bottom-full left-4 right-4 mb-1 bg-card border rounded-md shadow-lg overflow-hidden z-10">
            {suggestions.map((s, i) => (
              <button
                key={s.cmd}
                onMouseDown={e => { e.preventDefault(); setInput(s.cmd + " "); setShowSuggest(false); textareaRef.current?.focus(); }}
                className={`w-full flex items-center gap-3 px-3 py-2 text-sm text-left transition-colors ${
                  i === suggestIdx ? "bg-accent text-accent-foreground" : "hover:bg-accent/50"
                }`}
              >
                <span className="font-mono text-primary text-xs">{s.cmd}</span>
                <span className="text-muted-foreground text-xs">{s.desc}</span>
              </button>
            ))}
          </div>
        )}
        <div className="flex gap-2 items-end">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            onBlur={() => setTimeout(() => setShowSuggest(false), 150)}
            placeholder="Nachricht… (Enter zum Senden, Shift+Enter für Zeilenumbruch, / für Commands)"
            rows={1}
            disabled={sending}
            className="flex-1 px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-ring resize-none disabled:opacity-50"
            style={{ maxHeight: "120px", overflowY: "auto" }}
          />
          <button
            onClick={send}
            disabled={!input.trim() || sending}
            className="flex-shrink-0 p-2 bg-primary text-primary-foreground rounded-md hover:bg-primary/90 transition-colors disabled:opacity-40"
          >
            <Send className="h-4 w-4" />
          </button>
        </div>
      </div>
    </div>
  );
}
