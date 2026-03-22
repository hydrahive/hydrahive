import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { ArrowLeft, Send, Bot, User, Network, Terminal, Radar, Sparkles } from "lucide-react";
import { api } from "@/lib/api";
import ReactMarkdown from "react-markdown";

interface Message {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  workers?: string[];
}

const SLASH_COMMANDS = [
  { cmd: "/help", desc: "Verfuegbare Commands anzeigen" },
  { cmd: "/clear", desc: "Chat-Verlauf leeren" },
  { cmd: "/status", desc: "Projekt- und Agent-Status anzeigen" },
  { cmd: "/retry", desc: "Letzte Nachricht nochmal senden" },
  { cmd: "/model", desc: "Aktuelles LLM-Modell anzeigen" },
];

let msgCounter = 0;
function mkMsg(role: Message["role"], content: string, workers?: string[]): Message {
  return { id: `msg-${++msgCounter}`, role, content, workers };
}

export function ChatPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [projectName, setProjectName] = useState(id ?? "");
  const [showSwarm, setShowSwarm] = useState(false);
  const [projectData, setProjectData] = useState<Record<string, unknown>>({});
  const [bossModel, setBossModel] = useState<{ model?: string; temperature?: number }>({});
  const [showSuggest, setShowSuggest] = useState(false);
  const [suggestIdx, setSuggestIdx] = useState(0);
  const [activeTool, setActiveTool] = useState<{ name: string; detail: string } | null>(null);
  const [streamingMsgId, setStreamingMsgId] = useState<string | null>(null);
  const [doneMsgId, setDoneMsgId] = useState<string | null>(null);

  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

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
            .catch(() => {});
        }
      })
      .catch(() => {});
    api.sessionHistory(id)
      .then((d) => {
        const loaded = d.messages.filter((m) => m.role === "user" || m.role === "assistant").map((m) => mkMsg(m.role as "user" | "assistant", m.content));
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
    setMessages((ms) => [...ms, mkMsg("system", content)]);
  }

  function handleSlashCommand(cmd: string): boolean {
    const parts = cmd.trim().split(/\s+/);
    const base = parts[0].toLowerCase();

    if (base === "/help") {
      sysMsg("**Verfuegbare Commands:**\n\n" + SLASH_COMMANDS.map((c) => `\`${c.cmd}\` — ${c.desc}`).join("\n"));
      return true;
    }
    if (base === "/clear") {
      setMessages([]);
      sysMsg("Chat-Verlauf geleert.");
      return true;
    }
    if (base === "/status") {
      const cfg = projectData.config as { agents?: { boss?: string; workers?: string[] } } | undefined;
      const boss = cfg?.agents?.boss ?? "—";
      const workers = cfg?.agents?.workers?.join(", ") || "—";
      const model = bossModel.model ?? "nicht konfiguriert";
      sysMsg(`**Projekt:** ${projectName} (\`${id}\`)\n**Boss-Agent:** ${boss}\n**Worker-Agenten:** ${workers}\n**LLM-Modell:** ${model}`);
      return true;
    }
    if (base === "/retry") {
      const lastUser = [...messages].reverse().find((m) => m.role === "user");
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
      const temp = bossModel.temperature ?? "—";
      sysMsg(`**Aktuelles Modell:** \`${model}\`\n**Temperatur:** ${temp}`);
      return true;
    }
    sysMsg(`Unbekannter Command: \`${base}\`. Tippe \`/help\` fuer eine Uebersicht.`);
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

    const userMsg = mkMsg("user", content);
    const assistantMsg = mkMsg("assistant", "");
    setMessages((ms) => [...ms, userMsg]);
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

      setMessages((ms) => [...ms, assistantMsg]);
      setStreamingMsgId(assistantMsg.id);
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
                setMessages((ms) => ms.map((m) => m.id === assistantMsg.id ? { ...m, content: m.content + evt.text } : m));
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
      setMessages((ms) => ms.filter((m) => m.id !== userMsg.id && m.id !== assistantMsg.id));
      setInput(content);
    } finally {
      setSending(false);
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
    const model = bossModel.model ?? "kein Modell";
    return [
      { label: model, tone: "ok" },
      { label: showSwarm ? "Swarm sichtbar" : "Swarm kompakt", tone: "default" },
    ];
  }, [bossModel.model, showSwarm]);

  return (
    <div className="space-y-6">
      <section className="hero-panel">
        <div className="relative z-10 shell-grid">
          <div className="space-y-5 lg:col-span-8">
            <div className="flex flex-wrap items-center gap-3">
              <button onClick={() => navigate("/projects")} className="inline-flex items-center gap-2 rounded-2xl border bg-background/60 px-4 py-2 text-sm transition hover:bg-background">
                <ArrowLeft className="h-4 w-4" />
                Zurueck zu Projekten
              </button>
              <span className="status-pill status-pill-ok"><Radar className="h-3.5 w-3.5" />Projekt-Chat aktiv</span>
            </div>
            <div>
              <h1 className="shell-title">{projectName}</h1>
              <p className="shell-copy mt-3 max-w-2xl">
                Direkter Kanal zum Boss-Agenten des Projekts. Streaming, Slash-Commands und Tool-Hinweise bleiben erhalten,
                aber der Chat ist jetzt visuell staerker als Arbeitsflaeche statt als nackter Verlauf aufgebaut.
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              {statusPills.map((pill) => (
                <span key={pill.label} className={pill.tone === "ok" ? "status-pill status-pill-ok" : "status-pill"}>{pill.label}</span>
              ))}
              {id && <span className="status-pill font-mono">{id}</span>}
            </div>
          </div>
          <div className="lg:col-span-4">
            <div className="app-panel app-panel-muted p-5">
              <div className="flex items-center gap-2 text-sm font-medium"><Sparkles className="h-4 w-4 text-primary" />Chat-Steuerung</div>
              <div className="mt-4 flex flex-wrap gap-2">
                <button onClick={() => setShowSwarm((s) => !s)} className={`inline-flex items-center gap-2 rounded-2xl border px-4 py-2 text-sm transition ${showSwarm ? "bg-primary/10 text-primary" : "hover:bg-accent"}`}>
                  <Network className="h-4 w-4" />
                  {showSwarm ? "Swarm ausblenden" : "Swarm einblenden"}
                </button>
              </div>
              <p className="mt-3 text-sm text-muted-foreground">Slash-Commands bleiben unten ueber ` / ` verfuegbar. Tool-Aufrufe werden waehrend des Streams weiterhin live angezeigt.</p>
            </div>
          </div>
        </div>
      </section>

      <section className="section-card flex min-h-[65vh] flex-col overflow-hidden p-0">
        <div className="border-b bg-muted/20 px-5 py-4">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="metric-kicker">Conversation</p>
              <h2 className="mt-2 text-lg font-semibold tracking-tight">Projektkanal</h2>
            </div>
            <span className="status-pill">{messages.length} Nachricht{messages.length !== 1 ? "en" : ""}</span>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-5 space-y-4">
          {messages.length === 0 && (
            <div className="flex h-full flex-col items-center justify-center space-y-3 text-center text-muted-foreground">
              <Bot className="h-10 w-10" />
              <p className="text-sm">Schreib eine Nachricht, um den Boss-Agenten zu erreichen.</p>
              <p className="text-xs opacity-60">Tippe <code className="rounded bg-muted px-1">/help</code> fuer verfuegbare Commands.</p>
            </div>
          )}
          {messages.map((msg) => {
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
                      <span className="whitespace-pre-wrap">{msg.content}</span>
                    ) : streamingMsgId === msg.id && !msg.content ? (
                      <div className="flex h-5 items-center gap-1">
                        <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground [animation-delay:0ms]" />
                        <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground [animation-delay:150ms]" />
                        <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground [animation-delay:300ms]" />
                      </div>
                    ) : (
                      <>
                        <ReactMarkdown>{msg.content}</ReactMarkdown>
                        {streamingMsgId === msg.id ? <span className="ml-0.5 inline-block h-4 w-2 animate-pulse rounded-sm bg-primary/70 align-text-bottom" /> : doneMsgId === msg.id && <span className="ml-1 inline-block text-xs text-green-500 align-text-bottom">✓</span>}
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

        {activeTool && (
          <div className="border-t bg-muted/50 px-5 py-2 text-xs text-muted-foreground">
            <div className="flex min-w-0 items-center gap-2">
              <span className="flex flex-shrink-0 gap-0.5">
                <span className="h-1 w-1 animate-bounce rounded-full bg-primary [animation-delay:0ms]" />
                <span className="h-1 w-1 animate-bounce rounded-full bg-primary [animation-delay:150ms]" />
                <span className="h-1 w-1 animate-bounce rounded-full bg-primary [animation-delay:300ms]" />
              </span>
              <code className="flex-shrink-0 font-mono text-primary">{activeTool.name}</code>
              {activeTool.detail && <span className="truncate">{activeTool.detail}</span>}
            </div>
          </div>
        )}

        {error && <div className="border-t bg-destructive/10 px-5 py-3 text-xs text-destructive">{error}</div>}

        <div className="border-t px-5 py-4 relative">
          {showSuggest && suggestions.length > 0 && (
            <div className="absolute bottom-full left-5 right-5 z-10 mb-2 overflow-hidden rounded-2xl border bg-card shadow-lg">
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
          <div className="flex items-end gap-3">
            <textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKeyDown}
              onBlur={() => setTimeout(() => setShowSuggest(false), 150)}
              placeholder="Nachricht... (Enter zum Senden, Shift+Enter fuer Zeilenumbruch, / fuer Commands)"
              rows={1}
              disabled={sending}
              className="min-h-[52px] flex-1 resize-none rounded-2xl border bg-background px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary disabled:opacity-50"
              style={{ maxHeight: "140px", overflowY: "auto" }}
            />
            <button onClick={send} disabled={!input.trim() || sending} className="flex h-[52px] w-[52px] flex-shrink-0 items-center justify-center rounded-2xl bg-primary text-primary-foreground transition hover:bg-primary/90 disabled:opacity-40">
              <Send className="h-4 w-4" />
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}
