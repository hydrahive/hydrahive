import { useEffect, useRef, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { ArrowLeft, Send, Bot, User, Terminal, Smile } from "lucide-react";
import EmojiPicker, { type EmojiClickData, Theme } from "emoji-picker-react";
import { api } from "@/lib/api";
import ReactMarkdown from "react-markdown";

interface Message {
  id:         string;
  role:       "user" | "assistant" | "system";
  content:    string;
  tokenUsage?: { input: number; output: number };
}

const SLASH_COMMANDS = [
  { cmd: "/help",     desc: "Verfügbare Commands anzeigen" },
  { cmd: "/clear",    desc: "Chat-Verlauf leeren" },
  { cmd: "/compact",  desc: "Konversation via LLM zusammenfassen (spart Tokens)" },
  { cmd: "/model",    desc: "Aktuelles LLM-Modell anzeigen" },
  { cmd: "/retry",    desc: "Letzte Nachricht nochmal senden" },
  { cmd: "/remember", desc: "Session im Agenten-Gedächtnis speichern (/remember [name])" },
];

let _msgCounter = 0;
function mkMsg(role: Message["role"], content: string): Message {
  return { id: `msg-${++_msgCounter}`, role, content };
}

export function AgentChatPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const [messages,    setMessages]    = useState<Message[]>([]);
  const [input,       setInput]       = useState("");
  const [sending,     setSending]     = useState(false);
  const [error,       setError]       = useState("");
  const [agentName,   setAgentName]   = useState(id ?? "");
  const [agentModel,  setAgentModel]  = useState<{ model?: string; temperature?: number }>({});
  const [showSuggest, setShowSuggest] = useState(false);
  const [showEmoji, setShowEmoji] = useState(false);
  const [suggestIdx,  setSuggestIdx]  = useState(0);

  const bottomRef   = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

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
      .catch(() => {});
    api.get<{ session_id: string | null; messages: { role: string; content: string }[]; count: number }>(
      `/agents/${id}/session/history`
    )
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
    const base = cmd.trim().split(/\s+/)[0].toLowerCase();

    if (base === "/help") {
      sysMsg("**Verfügbare Commands:**\n\n" +
        SLASH_COMMANDS.map(c => `\`${c.cmd}\` — ${c.desc}`).join("\n"));
      return true;
    }
    if (base === "/clear") {
      setMessages([]);
      api.delete(`/agents/${id}/session`).catch(() => {});
      sysMsg("Chat-Verlauf geleert.");
      return true;
    }
    if (base === "/model") {
      const model = agentModel.model ?? "nicht konfiguriert";
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
        .filter(m => m.role === "user" || m.role === "assistant")
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
      const token = localStorage.getItem("hydrahive_token") || "";
      const res = await fetch(`/api/agents/${id}/message/stream`, {
        method:  "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body:    JSON.stringify({ content }),
      });
      if (!res.ok) {
        const e = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(e.detail || `HTTP ${res.status}`);
      }

      setMessages(ms => [...ms, assistantMsg]);
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
                setMessages(ms => ms.map(m =>
                  m.id === assistantMsg.id ? { ...m, content: m.content + evt.text } : m
                ));
              } else if (evt.done) {
                if (evt.usage && (evt.usage.input > 0 || evt.usage.output > 0))
                  setMessages(ms => ms.map(m => m.id === assistantMsg.id ? { ...m, tokenUsage: evt.usage } : m));
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
      setSending(false);
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
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3 border-b flex-shrink-0">
        <button onClick={() => navigate("/agents")}
          className="p-1.5 rounded-md hover:bg-accent transition-colors">
          <ArrowLeft className="h-4 w-4" />
        </button>
        <div className="w-7 h-7 rounded-full bg-primary/10 flex items-center justify-center flex-shrink-0">
          <Bot className="h-4 w-4 text-primary" />
        </div>
        <div className="flex-1 min-w-0">
          <h1 className="text-sm font-semibold truncate">{agentName}</h1>
          <p className="text-xs text-muted-foreground font-mono">{agentModel.model ?? id}</p>
        </div>
      </div>

      {/* Nachrichten */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full text-center space-y-3 text-muted-foreground">
            <Bot className="h-10 w-10" />
            <p className="text-sm">Direkter Chat mit <strong>{agentName}</strong>.</p>
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
                    : <ReactMarkdown>{msg.content}</ReactMarkdown>
                  }
                  {msg.role === "assistant" && msg.tokenUsage && (msg.tokenUsage.input > 0 || msg.tokenUsage.output > 0) && (
                    <div className="flex gap-1 px-1 pt-1">
                      <span className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground" title="Verbrauchte Tokens dieser Antwort">
                        ↑ {msg.tokenUsage.input.toLocaleString()} ↓ {msg.tokenUsage.output.toLocaleString()} Tokens
                      </span>
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

      {error && (
        <div className="px-4 py-2 text-xs text-destructive bg-destructive/10 border-t flex-shrink-0">
          {error}
        </div>
      )}

      {/* Eingabe */}
      <div className="px-4 py-3 border-t flex-shrink-0 relative">
        {showSuggest && suggestions.length > 0 && (
          <div className="absolute bottom-full left-4 right-4 mb-1 bg-card border rounded-md shadow-lg overflow-hidden z-10">
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
          <div className="absolute bottom-16 right-4 z-50">
            <EmojiPicker
              theme={Theme.DARK}
              onEmojiClick={(e: EmojiClickData) => {
                setInput(prev => prev + e.emoji);
                setShowEmoji(false);
                textareaRef.current?.focus();
              }}
              height={380}
              width={320}
            />
          </div>
        )}
        <div className="flex gap-2 items-end">
          <textarea ref={textareaRef} value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            onBlur={() => setTimeout(() => setShowSuggest(false), 150)}
            placeholder="Nachricht… (Enter zum Senden, Shift+Enter für Zeilenumbruch, / für Commands)"
            rows={1} disabled={sending}
            className="flex-1 px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-ring resize-none disabled:opacity-50"
            style={{ maxHeight: "120px", overflowY: "auto" }} />
          <button onClick={() => setShowEmoji(v => !v)} type="button"
            className="p-2 border rounded-md bg-background hover:bg-muted transition-colors flex-shrink-0">
            <Smile className="h-4 w-4 text-muted-foreground" />
          </button>
          <button onClick={send} disabled={sending || !input.trim()}
            className="p-2 bg-primary text-primary-foreground rounded-md hover:bg-primary/90 disabled:opacity-50 transition-colors flex-shrink-0">
            <Send className="h-4 w-4" />
          </button>
        </div>
      </div>
    </div>
  );
}
