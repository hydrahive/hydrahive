import { useEffect, useRef, useState } from "react";
import { Send, Bot, User, Terminal, Settings, BookOpen, Save, X, Plus, RefreshCw, Plug } from "lucide-react";
import { api, McpServer } from "@/lib/api";
import { SkillsPanel } from "@/components/SkillsPanel";
import ReactMarkdown from "react-markdown";

// ── Typen ────────────────────────────────────────────────────────────────────

interface Message { id: string; role: "user"|"assistant"|"system"; content: string; }

interface AgentCfg {
  identity:        string;
  llm:             { model: string; temperature: number; max_tokens: number; fallback_models?: string[] };
  tools?:          string[];
  allowed_agents?: string[];
  mcp_servers?:    string[];
  soul?:           string;
}
interface AgentInfo { agent_id: string; config: AgentCfg; }

// ── Konstanten ───────────────────────────────────────────────────────────────

const ALL_TOOLS: { id: string; label: string }[] = [
  { id: "file_read",        label: "Datei lesen" },
  { id: "file_write",       label: "Datei schreiben" },
  { id: "web_search",       label: "Web-Suche" },
  { id: "http_request",     label: "HTTP-Request" },
  { id: "shell_exec",       label: "Shell-Befehl" },
  { id: "read_system_file", label: "Systemdatei lesen" },
  { id: "write_system_file",label: "Systemdatei schreiben" },
  { id: "read_memory",      label: "Gedächtnis lesen" },
  { id: "write_memory",     label: "Gedächtnis schreiben" },
  { id: "ask_agent",        label: "Agent fragen (sync)" },
  { id: "delegate_agent",   label: "Agent beauftragen (async)" },
  { id: "write_handoff",    label: "AgentLink Handoff schreiben" },
  { id: "read_handoff",     label: "AgentLink Handoff lesen" },
];

const KNOWN_MODELS = [
  "claude-haiku-4-5-20251001","claude-sonnet-4-6","claude-opus-4-6",
  "gpt-4o-mini","gpt-4o",
  "ollama/mistral:latest","ollama/llama3.1:8b","ollama/llama3.2:3b",
];

const SLASH_COMMANDS = [
  { cmd: "/help",     desc: "Verfügbare Commands" },
  { cmd: "/clear",    desc: "Chat-Verlauf leeren" },
  { cmd: "/model",    desc: "Modell anzeigen" },
  { cmd: "/retry",    desc: "Letzte Nachricht wiederholen" },
  { cmd: "/remember", desc: "Session speichern" },
];

let _cnt = 0;
const mkMsg = (role: Message["role"], content: string): Message =>
  ({ id: `m${++_cnt}`, role, content });

// ── Haupt-Komponente ──────────────────────────────────────────────────────────

export function MyAgentPage() {
  const [tab,        setTab]        = useState<"chat"|"settings"|"skills"|"mcp">("chat");
  const [messages,   setMessages]   = useState<Message[]>([]);
  const [input,      setInput]      = useState("");
  const [sending,    setSending]    = useState(false);
  const [chatError,  setChatError]  = useState("");
  const [agentInfo,  setAgentInfo]  = useState<AgentInfo | null>(null);
  const [showSuggest,setShowSuggest]= useState(false);
  const [suggestIdx, setSuggestIdx] = useState(0);
  const [agents,     setAgents]     = useState<string[]>([]);
  const [mcpServers, setMcpServers] = useState<McpServer[]>([]);
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

  // ── Daten laden ──────────────────────────────────────────────────────────
  async function loadAgent() {
    try {
      const d = await api.get<AgentInfo>("/me/agent");
      // Soul nachladen
      const soul = await api.get<{soul:string;exists:boolean}>(`/agents/${d.agent_id}/soul`)
        .catch(() => ({ soul: "", exists: false }));
      setAgentInfo({ ...d, config: { ...d.config, soul: soul.soul } });
    } catch { /* ignore */ }
  }

  useEffect(() => {
    loadAgent();
    api.get<{session_id:string|null;messages:{role:string;content:string}[];count:number}>(
      "/me/agent/session/history"
    ).then(d => {
      const loaded = d.messages
        .filter(m => m.role === "user" || m.role === "assistant")
        .map(m => mkMsg(m.role as "user"|"assistant", m.content));
      if (loaded.length > 0) setMessages(loaded);
    }).catch(() => {});
    api.get<Record<string,unknown>>("/agents").then(d => {
      setAgents(Object.keys(d).filter(id => !id.startsWith("personal_")));
    }).catch(() => {});
    api.mcpServers().then(d => setMcpServers(d.servers)).catch(() => {});
  }, []);

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages]);
  useEffect(() => { setShowSuggest(suggestions.length > 0 && input.length > 0); setSuggestIdx(0); }, [input]);

  // ── Chat-Logik ────────────────────────────────────────────────────────────
  function sysMsg(c: string) { setMessages(ms => [...ms, mkMsg("system", c)]); }

  function handleSlash(cmd: string): boolean {
    const base = cmd.trim().split(/\s+/)[0].toLowerCase();
    const agentId = agentInfo?.agent_id;
    if (base === "/help") { sysMsg("**Commands:**\n\n" + SLASH_COMMANDS.map(c=>`\`${c.cmd}\` — ${c.desc}`).join("\n")); return true; }
    if (base === "/clear") {
      setMessages([]);
      api.delete("/me/agent/session").catch(() => {});
      sysMsg("Chat-Verlauf geleert."); return true;
    }
    if (base === "/model") {
      sysMsg(`**Modell:** \`${agentInfo?.config?.llm?.model ?? "?"}\`\n**Temperatur:** ${agentInfo?.config?.llm?.temperature ?? "?"}`);
      return true;
    }
    if (base === "/retry") {
      const last = [...messages].reverse().find(m => m.role === "user");
      if (!last) { sysMsg("Keine Nachricht zum Wiederholen."); return true; }
      setInput(last.content); setTimeout(() => textareaRef.current?.focus(), 0); return true;
    }
    if (base === "/remember") {
      const parts = cmd.trim().split(/\s+/);
      const fn = parts[1] ? parts[1].replace(/[^a-z0-9_-]/gi,"-").toLowerCase() : new Date().toISOString().slice(0,10);
      const history = messages.filter(m=>m.role==="user"||m.role==="assistant").slice(-30)
        .map(m=>`**${m.role==="user"?"User":"Agent"}:** ${m.content}`).join("\n\n");
      if (!history) { sysMsg("Kein Verlauf."); return true; }
      if (agentId) {
        api.post(`/agents/${agentId}/memory`, { filename: fn, content: `# Session ${new Date().toLocaleString("de")}\n\n${history}` })
          .then(() => sysMsg(`Gespeichert als \`${fn}.md\``))
          .catch((e:Error) => sysMsg(`Fehler: ${e.message}`));
      }
      return true;
    }
    sysMsg(`Unbekannter Command: \`${base}\`. /help für Übersicht.`); return true;
  }

  async function send() {
    if (!input.trim() || sending) return;
    const content = input.trim();
    setInput(""); setChatError(""); setShowSuggest(false);
    if (content.startsWith("/")) { handleSlash(content); return; }

    const userMsg = mkMsg("user", content);
    const asstMsg = mkMsg("assistant", "");
    setMessages(ms => [...ms, userMsg]);
    setSending(true);
    try {
      const token = localStorage.getItem("octopos_token") || "";
      const res = await fetch("/api/me/agent/message/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ content }),
      });
      if (!res.ok) { const e = await res.json().catch(()=>({detail:res.statusText})); throw new Error(e.detail||`HTTP ${res.status}`); }
      setMessages(ms => [...ms, asstMsg]);
      setStreamingMsgId(asstMsg.id);
      const reader = res.body!.getReader(); const dec = new TextDecoder(); let buf = "";
      outer: while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const parts = buf.split("\n\n"); buf = parts.pop() ?? "";
        for (const part of parts) {
          for (const line of part.split("\n")) {
            if (!line.startsWith("data: ")) continue;
            try {
              const evt = JSON.parse(line.slice(6));
              if (evt.text !== undefined) { setActiveTool(null); setMessages(ms => ms.map(m => m.id===asstMsg.id ? {...m,content:m.content+evt.text} : m)); }
              else if (evt.tool_call !== undefined) setActiveTool({ name: evt.tool_call, detail: toolDetail(evt.tool_call, evt.tool_input ?? {}) });
              else if (evt.done) break outer;
              else if (evt.error) throw new Error(evt.error);
            } catch(pe) { if (pe instanceof Error && pe.message !== "Unexpected end of JSON input") throw pe; }
          }
        }
      }
    } catch(e) {
      setChatError(e instanceof Error ? e.message : "Fehler");
      setMessages(ms => ms.filter(m => m.id!==userMsg.id && m.id!==asstMsg.id));
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
      if (e.key==="ArrowDown") { e.preventDefault(); setSuggestIdx(i=>(i+1)%suggestions.length); return; }
      if (e.key==="ArrowUp")   { e.preventDefault(); setSuggestIdx(i=>(i-1+suggestions.length)%suggestions.length); return; }
      if (e.key==="Tab"||(e.key==="Enter"&&showSuggest)) { e.preventDefault(); setInput(suggestions[suggestIdx].cmd+" "); setShowSuggest(false); return; }
      if (e.key==="Escape") { setShowSuggest(false); return; }
    }
    if (e.key==="Enter"&&!e.shiftKey) { e.preventDefault(); send(); }
  }

  const identity = agentInfo?.config?.identity ?? "Mein Agent";
  const model    = agentInfo?.config?.llm?.model ?? "";

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div className="flex flex-col h-full">
      {/* Header + Tabs */}
      <div className="border-b flex-shrink-0">
        <div className="flex items-center gap-3 px-4 py-3">
          <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center flex-shrink-0">
            <Bot className="h-4 w-4 text-primary" />
          </div>
          <div className="flex-1 min-w-0">
            <h1 className="text-sm font-semibold truncate">{identity}</h1>
            {model && <p className="text-xs text-muted-foreground font-mono">{model}</p>}
          </div>
        </div>
        <div className="flex gap-0 px-4">
          {[
            { id: "chat",     label: "Chat",           icon: Bot },
            { id: "settings", label: "Einstellungen",  icon: Settings },
            { id: "skills",   label: "Skills",         icon: BookOpen },
            { id: "mcp",      label: "MCP",            icon: Plug },
          ].map(({ id, label, icon: Icon }) => (
            <button key={id} onClick={() => setTab(id as typeof tab)}
              className={`flex items-center gap-1.5 px-3 py-2 text-xs border-b-2 transition-colors ${
                tab === id
                  ? "border-primary text-primary font-medium"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`}>
              <Icon className="h-3.5 w-3.5" />{label}
            </button>
          ))}
        </div>
      </div>

      {/* ── Chat Tab ──────────────────────────────────────────────────────── */}
      {tab === "chat" && (
        <>
          <div className="flex-1 overflow-y-auto p-4 space-y-4">
            {messages.length === 0 && (
              <div className="flex flex-col items-center justify-center h-full text-center space-y-3 text-muted-foreground">
                <Bot className="h-10 w-10" />
                <p className="text-sm font-medium">Hallo! Ich bin <strong>{identity}</strong>.</p>
                <p className="text-xs opacity-60">Tippe <code className="bg-muted px-1 rounded">/help</code> für Commands.</p>
              </div>
            )}
            {messages.map(msg => {
              if (msg.role === "system") return (
                <div key={msg.id} className="flex justify-center">
                  <div className="flex items-start gap-2 max-w-[85%] bg-muted/40 border rounded-lg px-3 py-2 text-xs text-muted-foreground">
                    <Terminal className="h-3.5 w-3.5 mt-0.5 flex-shrink-0 text-primary/60" />
                    <div className="prose prose-sm max-w-none dark:prose-invert prose-p:my-0.5">
                      <ReactMarkdown>{msg.content}</ReactMarkdown>
                    </div>
                  </div>
                </div>
              );
              return (
                <div key={msg.id} className={`flex gap-3 ${msg.role==="user" ? "justify-end" : "justify-start"}`}>
                  {msg.role==="assistant" && (
                    <div className="w-7 h-7 rounded-full bg-primary/10 flex items-center justify-center flex-shrink-0 mt-0.5">
                      <Bot className="h-4 w-4 text-primary" />
                    </div>
                  )}
                  <div className="max-w-[75%]">
                    <div className={`rounded-lg px-3 py-2 text-sm break-words ${
                      msg.role==="user"
                        ? "bg-primary text-primary-foreground"
                        : "bg-card border prose prose-sm max-w-none dark:prose-invert"
                    }`}>
                      {msg.role==="user"
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
                  </div>
                  {msg.role==="user" && (
                    <div className="w-7 h-7 rounded-full bg-secondary flex items-center justify-center flex-shrink-0 mt-0.5">
                      <User className="h-4 w-4" />
                    </div>
                  )}
                </div>
              );
            })}
            {sending && messages[messages.length-1]?.role !== "assistant" && (
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
          {chatError && <div className="px-4 py-2 text-xs text-destructive bg-destructive/10 border-t flex-shrink-0">{chatError}</div>}
          <div className="px-4 py-3 border-t flex-shrink-0 relative">
            {showSuggest && suggestions.length > 0 && (
              <div className="absolute bottom-full left-4 right-4 mb-1 bg-card border rounded-md shadow-lg overflow-hidden z-10">
                {suggestions.map((s,i) => (
                  <button key={s.cmd}
                    onMouseDown={e => { e.preventDefault(); setInput(s.cmd+" "); setShowSuggest(false); textareaRef.current?.focus(); }}
                    className={`w-full flex items-center gap-3 px-3 py-2 text-sm text-left transition-colors ${i===suggestIdx ? "bg-accent text-accent-foreground" : "hover:bg-accent/50"}`}>
                    <span className="font-mono text-primary text-xs">{s.cmd}</span>
                    <span className="text-muted-foreground text-xs">{s.desc}</span>
                  </button>
                ))}
              </div>
            )}
            <div className="flex gap-2 items-end">
              <textarea ref={textareaRef} value={input}
                onChange={e => setInput(e.target.value)} onKeyDown={onKeyDown}
                onBlur={() => setTimeout(() => setShowSuggest(false), 150)}
                placeholder="Nachricht… (Enter senden, Shift+Enter Umbruch)" rows={1} disabled={sending}
                className="flex-1 px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-ring resize-none disabled:opacity-50"
                style={{ maxHeight: "120px", overflowY: "auto" }} />
              <button onClick={send} disabled={sending||!input.trim()}
                className="p-2 bg-primary text-primary-foreground rounded-md hover:bg-primary/90 disabled:opacity-50 transition-colors flex-shrink-0">
                <Send className="h-4 w-4" />
              </button>
            </div>
          </div>
        </>
      )}

      {/* ── Einstellungen Tab ─────────────────────────────────────────────── */}
      {tab === "settings" && agentInfo && (
        <SettingsPanel
          agentInfo={agentInfo}
          agents={agents}
          onSaved={loadAgent}
        />
      )}

      {/* ── Skills Tab ────────────────────────────────────────────────────── */}
      {tab === "skills" && agentInfo && (
        <div className="flex-1 overflow-y-auto">
          <SkillsPanel agentId={agentInfo.agent_id} />
        </div>
      )}

      {/* ── MCP Tab ───────────────────────────────────────────────────────── */}
      {tab === "mcp" && agentInfo && (
        <McpTab agentInfo={agentInfo} mcpServers={mcpServers} onSaved={loadAgent} />
      )}
    </div>
  );
}

// ── MCP Tab ───────────────────────────────────────────────────────────────────

function McpTab({
  agentInfo, mcpServers, onSaved,
}: {
  agentInfo: AgentInfo;
  mcpServers: McpServer[];
  onSaved: () => void;
}) {
  const cfg = agentInfo.config;
  const [selected, setSelected] = useState<string[]>(cfg.mcp_servers ?? []);
  const [saving,   setSaving]   = useState(false);
  const [msg,      setMsg]      = useState("");

  function toggle(id: string) {
    setSelected(s => s.includes(id) ? s.filter(x => x !== id) : [...s, id]);
  }

  async function save() {
    setSaving(true); setMsg("");
    try {
      await api.put("/me/agent", {
        identity:        cfg.identity,
        soul:            cfg.soul ?? "",
        model:           cfg.llm?.model,
        temperature:     cfg.llm?.temperature ?? 0.7,
        max_tokens:      cfg.llm?.max_tokens ?? 4096,
        fallback_models: cfg.llm?.fallback_models ?? [],
        tools:           cfg.tools ?? [],
        allowed_agents:  cfg.allowed_agents ?? [],
        mcp_servers:     selected,
      });
      setMsg("Gespeichert ✓");
      onSaved();
      setTimeout(() => setMsg(""), 3000);
    } catch(e) { setMsg(e instanceof Error ? e.message : "Fehler"); }
    finally { setSaving(false); }
  }

  if (mcpServers.length === 0) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center text-center p-8 text-muted-foreground space-y-3">
        <Plug className="h-10 w-10 opacity-30" />
        <p className="text-sm">Keine MCP-Server verfügbar.</p>
        <p className="text-xs">Ein Admin kann Server unter <strong>MCP-Server</strong> konfigurieren.</p>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-6 max-w-2xl">
      <div className="space-y-1">
        <h2 className="text-sm font-semibold">MCP-Server</h2>
        <p className="text-xs text-muted-foreground">Wähle welche MCP-Server dein Agent nutzen soll. Die Server stellen externe Tools bereit.</p>
      </div>

      <div className="space-y-2">
        {mcpServers.map(s => (
          <label key={s.id} className="flex items-center gap-3 p-3 border rounded-lg cursor-pointer hover:bg-accent/40 transition-colors select-none">
            <input type="checkbox" checked={selected.includes(s.id)} onChange={() => toggle(s.id)} className="rounded" />
            <div className="w-7 h-7 rounded-md bg-primary/10 flex items-center justify-center flex-shrink-0">
              <Plug className="h-3.5 w-3.5 text-primary" />
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium">{s.name}</span>
                <span className="text-xs text-muted-foreground font-mono bg-muted px-1.5 py-0.5 rounded">{s.id}</span>
                <span className="text-xs text-muted-foreground">{s.transport}</span>
              </div>
              <p className="text-xs text-muted-foreground font-mono truncate">{s.url}</p>
            </div>
          </label>
        ))}
      </div>

      <div className="flex items-center gap-3">
        <button onClick={save} disabled={saving}
          className="flex items-center gap-2 px-4 py-2 text-sm bg-primary text-primary-foreground rounded-md hover:bg-primary/90 disabled:opacity-50 transition-colors">
          <Save className="h-3.5 w-3.5" />
          {saving ? "Speichern…" : "Speichern"}
        </button>
        {msg && <span className={`text-xs ${msg.includes("✓") ? "text-green-600" : "text-destructive"}`}>{msg}</span>}
      </div>
    </div>
  );
}

// ── Settings Panel ────────────────────────────────────────────────────────────

function SettingsPanel({
  agentInfo, agents, onSaved,
}: {
  agentInfo: AgentInfo;
  agents: string[];
  onSaved: () => void;
}) {
  const cfg = agentInfo.config;

  const [identity,       setIdentity]       = useState(cfg.identity ?? "");
  const [soul,           setSoul]           = useState(cfg.soul ?? "");
  const [model,          setModel]          = useState(cfg.llm?.model ?? "");
  const [temperature,    setTemperature]    = useState(cfg.llm?.temperature ?? 0.7);
  const [maxTokens,      setMaxTokens]      = useState(cfg.llm?.max_tokens ?? 4096);
  const [fallbacks,      setFallbacks]      = useState<string[]>(cfg.llm?.fallback_models ?? []);
  const [fbInput,        setFbInput]        = useState("");
  const [tools,          setTools]          = useState<string[]>(cfg.tools ?? []);
  const [allowedAgents,  setAllowedAgents]  = useState<string[]>(cfg.allowed_agents ?? []);
  const [saving,         setSaving]         = useState(false);
  const [saveMsg,        setSaveMsg]        = useState("");

  // Sync state wenn agentInfo von außen aktualisiert wird (nach Speichern oder Reload)
  useEffect(() => {
    const c = agentInfo.config;
    setIdentity(c.identity ?? "");
    setSoul(c.soul ?? "");
    setModel(c.llm?.model ?? "");
    setTemperature(c.llm?.temperature ?? 0.7);
    setMaxTokens(c.llm?.max_tokens ?? 4096);
    setFallbacks(c.llm?.fallback_models ?? []);
    setTools(c.tools ?? []);
    setAllowedAgents(c.allowed_agents ?? []);
  }, [agentInfo]);

  function toggleTool(id: string) {
    setTools(t => t.includes(id) ? t.filter(x => x!==id) : [...t, id]);
  }
  function toggleAgent(id: string) {
    setAllowedAgents(a => a.includes(id) ? a.filter(x => x!==id) : [...a, id]);
  }
  function addFallback() {
    const v = fbInput.trim();
    if (v && !fallbacks.includes(v)) setFallbacks(f => [...f, v]);
    setFbInput("");
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true); setSaveMsg("");
    try {
      await api.put("/me/agent", {
        identity, soul, model, temperature, max_tokens: maxTokens,
        fallback_models: fallbacks, tools, allowed_agents: allowedAgents,
      });
      setSaveMsg("Gespeichert ✓");
      onSaved();
      setTimeout(() => setSaveMsg(""), 3000);
    } catch(e) { setSaveMsg(e instanceof Error ? e.message : "Fehler"); }
    finally { setSaving(false); }
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <form onSubmit={handleSave} className="p-6 space-y-8 max-w-2xl">

        {/* Persönlichkeit */}
        <section className="space-y-3">
          <h2 className="text-sm font-semibold text-foreground flex items-center gap-2">
            <Bot className="h-4 w-4" />Persönlichkeit
          </h2>
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">Name / Identität</label>
            <input value={identity} onChange={e => setIdentity(e.target.value)}
              className="w-full px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary" />
          </div>
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">Soul — Charakter & Verhalten (Markdown)</label>
            <textarea value={soul} onChange={e => setSoul(e.target.value)} rows={6}
              placeholder="Beschreibe wie dein Agent sein soll, was er bevorzugt, seine Stärken..."
              className="w-full px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary resize-none font-mono" />
          </div>
        </section>

        {/* Modell */}
        <section className="space-y-3">
          <h2 className="text-sm font-semibold text-foreground">Modell</h2>
          <div className="grid grid-cols-2 gap-3">
            <div className="col-span-2 space-y-1">
              <label className="text-xs text-muted-foreground">Primäres Modell</label>
              <input value={model} onChange={e => setModel(e.target.value)}
                list="model-suggestions"
                className="w-full px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary" />
              <datalist id="model-suggestions">
                {[...new Set([...KNOWN_MODELS, model].filter(Boolean))].map(m => <option key={m} value={m} />)}
              </datalist>
            </div>
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground">Temperatur ({temperature})</label>
              <input type="range" min={0} max={1} step={0.05} value={temperature}
                onChange={e => setTemperature(parseFloat(e.target.value))}
                className="w-full" />
            </div>
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground">Max Tokens</label>
              <input type="number" value={maxTokens} min={256} max={32000} step={256}
                onChange={e => setMaxTokens(parseInt(e.target.value))}
                className="w-full px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary" />
            </div>
          </div>
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">Fallback-Modelle</label>
            <div className="flex flex-wrap gap-1 mb-2">
              {fallbacks.map(m => (
                <span key={m} className="inline-flex items-center gap-1 px-2 py-0.5 text-xs bg-secondary rounded">
                  {m}
                  <button type="button" onClick={() => setFallbacks(f => f.filter(x => x!==m))}>
                    <X className="h-2.5 w-2.5" />
                  </button>
                </span>
              ))}
            </div>
            <div className="flex gap-2">
              <input value={fbInput} onChange={e => setFbInput(e.target.value)}
                onKeyDown={e => { if(e.key==="Enter"){e.preventDefault();addFallback();} }}
                list="model-suggestions"
                placeholder="z.B. ollama/mistral:latest"
                className="flex-1 px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary" />
              <button type="button" onClick={addFallback}
                className="px-3 py-2 text-sm border rounded-md hover:bg-accent transition-colors">
                <Plus className="h-4 w-4" />
              </button>
            </div>
          </div>
        </section>

        {/* Tools */}
        <section className="space-y-3">
          <h2 className="text-sm font-semibold text-foreground">Tools</h2>
          <div className="grid grid-cols-2 gap-2">
            {ALL_TOOLS.map(t => (
              <label key={t.id} className="flex items-center gap-2 text-sm cursor-pointer select-none">
                <input type="checkbox" checked={tools.includes(t.id)} onChange={() => toggleTool(t.id)}
                  className="rounded" />
                <span className="text-xs">{t.label}</span>
                <span className="text-xs text-muted-foreground font-mono">({t.id})</span>
              </label>
            ))}
          </div>
        </section>

        {/* Delegation */}
        {agents.length > 0 && (
          <section className="space-y-3">
            <h2 className="text-sm font-semibold text-foreground">Agenten-Delegation</h2>
            <p className="text-xs text-muted-foreground">Welche Agenten darf dein Agent via ask_agent/delegate_agent beauftragen?</p>
            <div className="grid grid-cols-2 gap-2">
              {agents.map(id => (
                <label key={id} className="flex items-center gap-2 text-sm cursor-pointer select-none">
                  <input type="checkbox" checked={allowedAgents.includes(id)} onChange={() => toggleAgent(id)}
                    className="rounded" />
                  <span className="text-xs font-mono">{id}</span>
                </label>
              ))}
            </div>
          </section>
        )}

        {/* Save */}
        <div className="flex items-center gap-3">
          <button type="submit" disabled={saving}
            className="flex items-center gap-2 px-4 py-2 text-sm bg-primary text-primary-foreground rounded-md hover:bg-primary/90 disabled:opacity-50 transition-colors">
            <Save className="h-3.5 w-3.5" />
            {saving ? "Speichern…" : "Speichern"}
          </button>
          {saveMsg && (
            <span className={`text-xs ${saveMsg.includes("✓") ? "text-green-600" : "text-destructive"}`}>
              {saveMsg}
            </span>
          )}
          <button type="button" onClick={onSaved}
            className="ml-auto flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors">
            <RefreshCw className="h-3 w-3" />Neu laden
          </button>
        </div>
      </form>
    </div>
  );
}
