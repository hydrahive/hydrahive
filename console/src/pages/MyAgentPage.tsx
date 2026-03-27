import { useEffect, useRef, useState } from "react";
import { Send, Square, Bot, User, Terminal, Settings, BookOpen, Save, X, Plus, RefreshCw, Plug, Monitor, MessageSquare, CheckCircle, AlertCircle, Wifi, WifiOff, Sparkles, Shield, Smile, Mail, Phone, Timer, Trash2, Pencil } from "lucide-react";
import EmojiPicker, { type EmojiClickData, Theme } from "emoji-picker-react";
import { api, McpServer, WksConfig, DiscordConfig, MailConfig, WhatsAppStatus, WhatsAppConfig, PlatformOverviewEntry } from "@/lib/api";
import { SkillsPanel } from "@/components/SkillsPanel";
import ReactMarkdown from "react-markdown";
import { useTranslation } from "react-i18next";

// ── Typen ────────────────────────────────────────────────────────────────────

interface Message { id: string; role: "user"|"assistant"|"system"; content: string; tokenUsage?: { input: number; output: number; rounds?: number }; }

interface AgentCfg {
  identity:        string;
  llm:             { model: string; temperature: number; max_tokens: number; fallback_models?: string[] };
  tools?:          string[];
  allowed_agents?: string[];
  mcp_servers?:    string[];
  execution_modes?: {
    default?: "safe" | "elevated" | "root";
    safe?: { permissions?: string[] };
    elevated?: { permissions?: string[] };
    root?: { permissions?: string[] };
  };
  soul?:           string;
}
interface AgentInfo { agent_id: string; config: AgentCfg; }

// ── Konstanten ───────────────────────────────────────────────────────────────

const ALL_TOOLS: { id: string; label: string }[] = [
  { id: "file_read",        label: "Datei lesen" },
  { id: "file_write",       label: "Datei schreiben" },
  { id: "project_shell",    label: "⚠ Projekt-Shell (Whitelist)" },
  { id: "web_search",       label: "Web-Suche" },
  { id: "http_request",     label: "HTTP-Request" },
  { id: "shell_exec",       label: "Shell-Befehl (Server)" },
  { id: "read_system_file", label: "Systemdatei lesen" },
  { id: "write_system_file",label: "Systemdatei schreiben" },
  { id: "read_memory",      label: "Gedächtnis lesen" },
  { id: "write_memory",     label: "Gedächtnis schreiben" },
  { id: "ask_agent",        label: "Agent fragen (sync)" },
  { id: "delegate_agent",   label: "Agent beauftragen (async)" },
  { id: "write_handoff",    label: "AgentLink Handoff schreiben" },
  { id: "read_handoff",     label: "AgentLink Handoff lesen" },
  { id: "gitea_repo_inspect", label: "Gitea-Repo pruefen" },
  { id: "gitea_repo_tree",  label: "Gitea-Repo Struktur" },
  { id: "gitea_repo_file",  label: "Gitea-Repo Datei" },
  { id: "gitea_repo_commits", label: "Gitea-Repo Commits" },
  { id: "gitea_repo_diff", label: "Gitea-Repo Diff" },
  { id: "gitea_create_issue", label: "Gitea-Issue erstellen" },
  { id: "gitea_comment_issue", label: "Gitea-Issue kommentieren" },
  { id: "gitea_update_issue", label: "Gitea-Issue aktualisieren" },
  { id: "wks_shell_exec",   label: "WKS Shell-Befehl" },
  { id: "wks_file_read",    label: "WKS Datei lesen" },
  { id: "wks_file_write",   label: "WKS Datei schreiben" },
  { id: "send_mail",        label: "Mail senden" },
  { id: "receive_mail",     label: "Mail empfangen" },
  { id: "discord_send",              label: "Discord: Nachricht senden" },
  { id: "discord_read",              label: "Discord: Nachrichten lesen" },
  { id: "discord_list_channels",     label: "Discord: Text-Channels auflisten" },
  { id: "discord_list_all_channels", label: "Discord: Alle Channels auflisten" },
  { id: "discord_create_category",   label: "Discord: Kategorie erstellen" },
  { id: "discord_create_channel",    label: "Discord: Channel erstellen" },
  { id: "discord_delete_channel",    label: "Discord: Channel löschen" },
  { id: "discord_set_topic",         label: "Discord: Channel-Topic setzen" },
  { id: "discord_rename_channel",    label: "Discord: Channel umbenennen" },
  { id: "discord_list_members",      label: "Discord: Mitglieder auflisten" },
  { id: "discord_list_roles",        label: "Discord: Rollen auflisten" },
  { id: "discord_delete_message",    label: "Discord: Nachricht löschen" },
  { id: "discord_pin_message",       label: "Discord: Nachricht anpinnen" },
  { id: "create_agent",   label: "⚠ Agent anlegen (Admin)" },
  { id: "delete_agent",   label: "⚠ Agent löschen (Admin)" },
  { id: "create_project", label: "⚠ Projekt anlegen (Admin)" },
  { id: "delete_project", label: "⚠ Projekt löschen (Admin)" },
];

const BROWSER_HOST = typeof window !== "undefined" ? window.location.hostname : "127.0.0.1";

function resolveSearchUiUrl(url: string) {
  try {
    const parsed = new URL(url);
    if (parsed.hostname === "localhost" || parsed.hostname === "127.0.0.1") {
      parsed.hostname = BROWSER_HOST;
      return parsed.toString();
    }
  } catch {
    return url;
  }
  return url;
}

function modeSummary(cfg?: AgentCfg["execution_modes"]) {
  const defaultMode = cfg?.default ?? "safe";
  const counts = {
    safe: cfg?.safe?.permissions?.length ?? 0,
    elevated: cfg?.elevated?.permissions?.length ?? 0,
    root: cfg?.root?.permissions?.length ?? 0,
  };
  return { defaultMode, counts };
}

const KNOWN_MODELS = [
  "claude-haiku-4-5-20251001","claude-sonnet-4-6","claude-opus-4-6",
  "gpt-4o-mini","gpt-4o",
  "ollama/mistral:latest","ollama/llama3.1:8b","ollama/llama3.2:3b",
];

// SLASH_COMMANDS moved inside MyAgentPage component to use live t() calls

let _cnt = 0;
const mkMsg = (role: Message["role"], content: string): Message =>
  ({ id: `m${++_cnt}`, role, content });

// ── Haupt-Komponente ──────────────────────────────────────────────────────────

export function MyAgentPage() {
  const { t } = useTranslation();

  const SLASH_COMMANDS = [
    { cmd: "/help",     desc: t("slashCommands.help") },
    { cmd: "/clear",    desc: t("slashCommands.clear") },
    { cmd: "/model",    desc: t("slashCommands.model") },
    { cmd: "/retry",    desc: t("slashCommands.retry") },
    { cmd: "/remember", desc: t("slashCommands.remember") },
  ];

  const [tab,        setTab]        = useState<"chat"|"settings"|"skills"|"mcp"|"platforms"|"wks"|"discord"|"whatsapp"|"telegram"|"mail"|"heartbeat">("chat");
  const [messages,   setMessages]   = useState<Message[]>([]);
  const [input,      setInput]      = useState("");
  const [sending,    setSending]    = useState(false);
  const [chatError,  setChatError]  = useState("");
  const [loadError,   setLoadError]  = useState("");
  const [agentInfo,  setAgentInfo]  = useState<AgentInfo | null>(null);
  const [showSuggest,setShowSuggest]= useState(false);
  const [showEmoji,  setShowEmoji]  = useState(false);
  const [suggestIdx, setSuggestIdx] = useState(0);
  const [agents,     setAgents]     = useState<string[]>([]);
  const [mcpServers, setMcpServers] = useState<McpServer[]>([]);
  const [activeTool,     setActiveTool]     = useState<{name:string;detail:string} | null>(null);
  const [streamingMsgId, setStreamingMsgId] = useState<string | null>(null);
  const [doneMsgId,      setDoneMsgId]      = useState<string | null>(null);

  const bottomRef       = useRef<HTMLDivElement>(null);
  const textareaRef     = useRef<HTMLTextAreaElement>(null);
  const abortRef        = useRef<AbortController | null>(null);
  const elapsedTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [elapsed, setElapsed] = useState(0);

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
    setLoadError("");
    try {
      const d = await api.get<AgentInfo>("/me/agent");
      // Soul nachladen
      const soul = await api.get<{soul:string;exists:boolean}>(`/agents/${d.agent_id}/soul`)
        .catch(() => ({ soul: "", exists: false }));
      setAgentInfo({ ...d, config: { ...d.config, soul: soul.soul } });
    } catch (e) {
      setAgentInfo(null);
      setLoadError(e instanceof Error ? e.message : "Fehler beim Laden des Agenten");
    }
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

  async function stop() {
    if (abortRef.current) abortRef.current.abort();
    const token = localStorage.getItem("hydrahive_token") || "";
    fetch("/api/me/agent/interrupt", { method: "POST", headers: { Authorization: `Bearer ${token}` } }).catch(() => {});
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
    setElapsed(0);
    const controller = new AbortController();
    abortRef.current = controller;
    elapsedTimerRef.current = setInterval(() => setElapsed((s) => s + 1), 1000);
    try {
      const token = localStorage.getItem("hydrahive_token") || "";
      const res = await fetch("/api/me/agent/message/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ content }),
        signal: controller.signal,
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
              else if (evt.done) {
                if (evt.usage && (evt.usage.input > 0 || evt.usage.output > 0))
                  setMessages(ms => ms.map(m => m.id===asstMsg.id ? {...m, tokenUsage: evt.usage} : m));
                break outer;
              }
              else if (evt.error) throw new Error(evt.error);
            } catch(pe) { if (pe instanceof Error && pe.message !== "Unexpected end of JSON input") throw pe; }
          }
        }
      }
    } catch(e) {
      if (e instanceof DOMException && e.name === "AbortError") {
        // User aborted — keep partial response, no error
      } else {
        setChatError(e instanceof Error ? e.message : "Fehler");
        setMessages(ms => ms.filter(m => m.id!==userMsg.id && m.id!==asstMsg.id));
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
      if (e.key==="ArrowDown") { e.preventDefault(); setSuggestIdx(i=>(i+1)%suggestions.length); return; }
      if (e.key==="ArrowUp")   { e.preventDefault(); setSuggestIdx(i=>(i-1+suggestions.length)%suggestions.length); return; }
      if (e.key==="Tab"||(e.key==="Enter"&&showSuggest)) { e.preventDefault(); setInput(suggestions[suggestIdx].cmd+" "); setShowSuggest(false); return; }
      if (e.key==="Escape") { setShowSuggest(false); return; }
    }
    if (e.key==="Enter"&&!e.shiftKey) { e.preventDefault(); send(); }
  }

  const identity = agentInfo?.config?.identity ?? "Mein Agent";
  const model    = agentInfo?.config?.llm?.model ?? "";
  const exec     = modeSummary(agentInfo?.config?.execution_modes);

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
            { id: "chat",      label: t("myAgent.chatTab"),       icon: Bot },
            { id: "settings",  label: t("myAgent.settingsTab"),   icon: Settings },
            { id: "heartbeat", label: t("myAgent.heartbeatTab"),  icon: Timer },
            { id: "skills",    label: t("myAgent.skillsTab"),     icon: BookOpen },
            { id: "mcp",       label: t("myAgent.mcpTab"),        icon: Plug },
            { id: "platforms", label: t("myAgent.platformsTab"),  icon: Wifi },
            { id: "wks",       label: t("myAgent.wksTab"),        icon: Monitor },
            { id: "discord",   label: t("myAgent.discordTab"),    icon: MessageSquare },
            { id: "whatsapp",  label: t("myAgent.whatsappTab"),   icon: Phone },
            { id: "telegram",  label: t("myAgent.telegramTab"),   icon: Send },
            { id: "mail",      label: t("myAgent.mailTab"),       icon: Mail },
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
        <div className="flex-1 overflow-y-auto p-4 sm:p-6">
          <div className="grid gap-6 xl:grid-cols-[minmax(0,1.8fr)_22rem]">
            <section className="space-y-4">
              {loadError && (
                <div className="rounded-2xl border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive">
                  {loadError.includes("Token")
                    ? t("myAgent.sessionExpired")
                    : loadError}
                </div>
              )}
              <div className="rounded-[28px] border border-border/60 bg-card/80 p-5 shadow-[0_20px_80px_rgba(15,23,42,0.08)] backdrop-blur">
                <div className="flex flex-wrap items-start justify-between gap-4">
                  <div className="space-y-2">
                    <div className="inline-flex items-center gap-2 rounded-full border border-primary/20 bg-primary/10 px-3 py-1 text-xs font-medium text-primary">
                      <Bot className="h-3.5 w-3.5" />
                      {t("myAgent.myChatLabel")}
                    </div>
                    <div>
                      <h2 className="text-xl font-semibold tracking-tight">{identity}</h2>
                      <p className="mt-1 text-sm text-muted-foreground">
                        {t("myAgent.chatSubtitle")}
                      </p>
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-2 text-xs">
                    <span className="rounded-full border border-border/70 bg-background/80 px-3 py-1.5 font-medium text-foreground">
                      {agentInfo?.config?.llm?.model ?? t("myAgent.noModel")}
                    </span>
                    <span className="rounded-full border border-border/70 bg-background/80 px-3 py-1.5 text-muted-foreground">
                      {t("myAgent.mode")}: {exec.defaultMode}
                    </span>
                    <span className="rounded-full border border-border/70 bg-background/80 px-3 py-1.5 text-muted-foreground">
                      {sending ? t("myAgent.streamingActive") : t("myAgent.ready")}
                    </span>
                    <span className="rounded-full border border-border/70 bg-background/80 px-3 py-1.5 text-muted-foreground">
                      {messages.filter((m) => m.role !== "system").length} {t("myAgent.messages")}
                    </span>
                  </div>
                </div>
              </div>

              <div className="rounded-[28px] border border-border/60 bg-card/80 shadow-[0_20px_80px_rgba(15,23,42,0.08)] backdrop-blur">
                <div className="border-b border-border/60 px-4 py-3 sm:px-5">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                      <h3 className="text-sm font-semibold">{t("myAgent.historyTitle")}</h3>
                      <p className="text-xs text-muted-foreground">{t("myAgent.historySubtitle")}</p>
                    </div>
                    {activeTool && (
                      <div className="inline-flex max-w-full items-center gap-2 rounded-full border border-primary/20 bg-primary/10 px-3 py-1 text-xs text-primary">
                        <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                        <code className="max-w-[10rem] truncate font-mono">{activeTool.name}</code>
                      </div>
                    )}
                  </div>
                </div>

                <div className="space-y-4 px-4 py-5 sm:px-5">
                  {messages.length === 0 && (
                    <div className="flex min-h-[18rem] flex-col items-center justify-center rounded-3xl border border-dashed border-border/70 bg-muted/20 px-6 text-center text-muted-foreground">
                      <Bot className="h-10 w-10 opacity-70" />
                      <p className="mt-4 text-sm font-medium text-foreground">{t("myAgent.greetingTitle", { name: identity })}</p>
                      <p className="mt-2 max-w-md text-xs">{t("myAgent.greetingSubtitle")} <code className="rounded bg-background px-1.5 py-0.5">/help</code> {t("myAgent.greetingSubtitle2")}</p>
                    </div>
                  )}

                  {messages.map((msg) => {
                    if (msg.role === "system") return (
                      <div key={msg.id} className="flex justify-center">
                        <div className="flex max-w-[90%] items-start gap-2 rounded-2xl border border-border/70 bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
                          <Terminal className="mt-0.5 h-3.5 w-3.5 flex-shrink-0 text-primary/60" />
                          <div className="prose prose-sm max-w-none dark:prose-invert prose-p:my-0.5">
                            <ReactMarkdown>{msg.content}</ReactMarkdown>
                          </div>
                        </div>
                      </div>
                    );

                    return (
                      <div key={msg.id} className={`flex gap-3 ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
                        {msg.role === "assistant" && (
                          <div className="mt-0.5 flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-2xl bg-primary/10">
                            <Bot className="h-4 w-4 text-primary" />
                          </div>
                        )}
                        <div className="max-w-[85%]">
                          <div className={`rounded-[22px] px-4 py-3 text-sm break-words shadow-sm ${
                            msg.role === "user"
                              ? "bg-primary text-primary-foreground"
                              : "border border-border/60 bg-background/90 prose prose-sm max-w-none dark:prose-invert"
                          }`}>
                            {msg.role === "user"
                              ? <span className="whitespace-pre-wrap">{msg.content}</span>
                              : streamingMsgId === msg.id && !msg.content
                                ? <div className="flex h-5 items-center gap-1">
                                    <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground animate-bounce [animation-delay:0ms]" />
                                    <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground animate-bounce [animation-delay:150ms]" />
                                    <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground animate-bounce [animation-delay:300ms]" />
                                  </div>
                                : <>
                                    <ReactMarkdown>{msg.content}</ReactMarkdown>
                                    {streamingMsgId === msg.id
                                      ? <span className="ml-0.5 inline-block h-4 w-2 animate-pulse rounded-sm bg-primary/70 align-text-bottom" />
                                      : doneMsgId === msg.id && <span className="ml-1 inline-block align-text-bottom text-xs text-green-500">✓</span>
                                    }
                                  </>
                            }
                          </div>
                          {msg.role === "assistant" && msg.tokenUsage && (msg.tokenUsage.input > 0 || msg.tokenUsage.output > 0) && (
                            <div className="flex gap-1 px-1 pt-1">
                              <span className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground" title="Verbrauchte Tokens dieser Antwort (inkl. aller Tool-Runden)">
                                ↑ {msg.tokenUsage.input.toLocaleString()} ↓ {msg.tokenUsage.output.toLocaleString()} Tokens
                                {msg.tokenUsage.rounds && msg.tokenUsage.rounds > 1 && (
                                  <span className="opacity-60">· {msg.tokenUsage.rounds} Runden</span>
                                )}
                              </span>
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
                      <div className="rounded-[22px] border border-border/60 bg-background/90 px-4 py-3 shadow-sm">
                        <div className="flex h-5 items-center gap-1">
                          <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground animate-bounce [animation-delay:0ms]" />
                          <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground animate-bounce [animation-delay:150ms]" />
                          <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground animate-bounce [animation-delay:300ms]" />
                        </div>
                      </div>
                    </div>
                  )}

                  <div ref={bottomRef} />
                </div>

                {activeTool && (
                  <div className="border-t border-border/60 bg-muted/30 px-4 py-2 text-xs text-muted-foreground sm:px-5">
                    <div className="flex min-w-0 items-center gap-2">
                      <span className="flex flex-shrink-0 gap-0.5">
                        <span className="h-1 w-1 rounded-full bg-primary animate-bounce [animation-delay:0ms]" />
                        <span className="h-1 w-1 rounded-full bg-primary animate-bounce [animation-delay:150ms]" />
                        <span className="h-1 w-1 rounded-full bg-primary animate-bounce [animation-delay:300ms]" />
                      </span>
                      <code className="flex-shrink-0 font-mono text-primary">{activeTool.name}</code>
                      {activeTool.detail && <span className="truncate">{activeTool.detail}</span>}
                    </div>
                  </div>
                )}

                {chatError && (
                  <div className="border-t border-destructive/30 bg-destructive/10 px-4 py-2 text-xs text-destructive sm:px-5">
                    {chatError}
                  </div>
                )}

                <div className="border-t border-border/60 px-4 py-4 sm:px-5">
                  <div className="relative">
                    {showSuggest && suggestions.length > 0 && (
                      <div className="absolute bottom-full left-0 right-0 mb-2 overflow-hidden rounded-2xl border border-border/70 bg-card shadow-lg z-10">
                        {suggestions.map((s, i) => (
                          <button key={s.cmd}
                            onMouseDown={(e) => { e.preventDefault(); setInput(s.cmd + " "); setShowSuggest(false); textareaRef.current?.focus(); }}
                            className={`flex w-full items-center gap-3 px-4 py-3 text-left text-sm transition-colors ${i === suggestIdx ? "bg-accent text-accent-foreground" : "hover:bg-accent/50"}`}>
                            <span className="font-mono text-xs text-primary">{s.cmd}</span>
                            <span className="text-xs text-muted-foreground">{s.desc}</span>
                          </button>
                        ))}
                      </div>
                    )}

                    <div className="rounded-[24px] border border-border/70 bg-background/90 p-3 shadow-sm">
                      <div className="mb-3 flex flex-wrap gap-2">
                        {SLASH_COMMANDS.map((cmd) => (
                          <button key={cmd.cmd}
                            type="button"
                            onClick={() => { setInput(`${cmd.cmd} `); textareaRef.current?.focus(); }}
                            className="rounded-full border border-border/70 bg-card px-3 py-1 text-[11px] text-muted-foreground transition hover:border-primary/40 hover:text-foreground">
                            {cmd.cmd}
                          </button>
                        ))}
                      </div>
                      {showEmoji && (
                        <>
                        <div className="fixed inset-0 z-40" onClick={() => setShowEmoji(false)} />
                        <div className="absolute bottom-16 right-0 z-50">
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
                      <div className="flex items-end gap-3">
                        <textarea ref={textareaRef} value={input}
                          onChange={(e) => setInput(e.target.value)} onKeyDown={onKeyDown}
                          onBlur={() => setTimeout(() => setShowSuggest(false), 150)}
                          placeholder={t("myAgent.messagePlaceholder")} rows={1}
                          className="min-h-[3rem] flex-1 resize-none rounded-2xl border border-border/60 bg-card px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                          style={{ maxHeight: "160px", overflowY: "auto" }} />
                        <button onClick={() => setShowEmoji(v => !v)} type="button"
                          className="inline-flex h-12 w-12 items-center justify-center rounded-2xl border border-border/60 bg-card transition hover:bg-muted">
                          <Smile className="h-5 w-5 text-muted-foreground" />
                        </button>
                        {sending ? (
                          <button onClick={stop}
                            className="inline-flex h-12 w-12 items-center justify-center rounded-2xl bg-destructive text-destructive-foreground transition hover:bg-destructive/90"
                            title={`Abbrechen${elapsed > 0 ? ` (${elapsed}s)` : ""}`}>
                            <Square className="h-4 w-4" />
                          </button>
                        ) : (
                          <button onClick={send} disabled={!input.trim()}
                            className="inline-flex h-12 w-12 items-center justify-center rounded-2xl bg-primary text-primary-foreground transition hover:bg-primary/90 disabled:opacity-50">
                            <Send className="h-4 w-4" />
                          </button>
                        )}
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </section>

            <aside className="xl:sticky xl:top-24 xl:self-start">
              <div className="space-y-4 rounded-[28px] border border-border/60 bg-card/80 p-5 shadow-[0_20px_80px_rgba(15,23,42,0.08)] backdrop-blur">
                <div className="space-y-1">
                  <h3 className="text-sm font-semibold">{t("myAgent.contextTitle")}</h3>
                  <p className="text-xs text-muted-foreground">{t("myAgent.contextSubtitle")}</p>
                </div>

                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-1">
                  <div className="rounded-2xl border border-border/70 bg-background/70 p-4">
                    <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">
                      <Sparkles className="h-3.5 w-3.5" />
                      {t("myAgent.modelLabel")}
                    </div>
                    <div className="mt-3 text-sm font-medium">{agentInfo?.config?.llm?.model ?? t("myAgent.noModel")}</div>
                    <div className="mt-1 text-xs text-muted-foreground">{t("myAgent.fallbacks", { count: agentInfo?.config?.llm?.fallback_models?.length ?? 0 })}</div>
                  </div>

                  <div className="rounded-2xl border border-border/70 bg-background/70 p-4">
                    <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">
                      <Settings className="h-3.5 w-3.5" />
                      {t("myAgent.statusLabel")}
                    </div>
                    <div className="mt-3 flex items-center gap-2 text-sm">
                      {sending ? <RefreshCw className="h-4 w-4 animate-spin text-primary" /> : <CheckCircle className="h-4 w-4 text-emerald-500" />}
                      <span>{sending ? t("myAgent.streamingNow") : t("myAgent.agentReady")}</span>
                    </div>
                    {activeTool && <div className="mt-2 text-xs text-muted-foreground">{t("myAgent.activeTool")}: <code className="font-mono text-foreground">{activeTool.name}</code></div>}
                  </div>

                  <div className="rounded-2xl border border-border/70 bg-background/70 p-4">
                    <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">
                      <Shield className="h-3.5 w-3.5" />
                      {t("myAgent.executionModes")}
                    </div>
                    <div className="mt-3 flex items-center justify-between gap-3">
                      <span className="text-sm font-medium">{t("myAgent.defaultMode", { mode: exec.defaultMode })}</span>
                    </div>
                    <div className="mt-3 space-y-2 text-xs text-muted-foreground">
                      <div className="flex items-center justify-between gap-3">
                        <span>safe</span>
                        <span className="font-medium text-foreground">{t("myAgent.permissions", { count: exec.counts.safe })}</span>
                      </div>
                      <div className="flex items-center justify-between gap-3">
                        <span>elevated</span>
                        <span className="font-medium text-foreground">{t("myAgent.permissions", { count: exec.counts.elevated })}</span>
                      </div>
                      <div className="flex items-center justify-between gap-3">
                        <span>root</span>
                        <span className="font-medium text-foreground">{t("myAgent.permissions", { count: exec.counts.root })}</span>
                      </div>
                    </div>
                  </div>

                  <div className="rounded-2xl border border-border/70 bg-background/70 p-4 sm:col-span-2 xl:col-span-1">
                    <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">
                      <MessageSquare className="h-3.5 w-3.5" />
                      {t("myAgent.commandsLabel")}
                    </div>
                    <div className="mt-3 space-y-2">
                      {SLASH_COMMANDS.map((cmd) => (
                        <div key={cmd.cmd} className="flex items-start justify-between gap-3 text-xs">
                          <code className="rounded bg-muted px-1.5 py-0.5 text-primary">{cmd.cmd}</code>
                          <span className="text-right text-muted-foreground">{cmd.desc}</span>
                        </div>
                      ))}
                    </div>
                  </div>

                  <div className="rounded-2xl border border-border/70 bg-background/70 p-4 sm:col-span-2 xl:col-span-1">
                    <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">
                      <Terminal className="h-3.5 w-3.5" />
                      {t("myAgent.sessionLabel")}
                    </div>
                    <div className="mt-3 space-y-2 text-xs text-muted-foreground">
                      <div className="flex items-center justify-between gap-3">
                        <span>{t("myAgent.historyCount")}</span>
                        <span className="font-medium text-foreground">{messages.length}</span>
                      </div>
                      <div className="flex items-center justify-between gap-3">
                        <span>{t("myAgent.activeTools")}</span>
                        <span className="font-medium text-foreground">{activeTool ? 1 : 0}</span>
                      </div>
                      <div className="flex items-center justify-between gap-3">
                        <span>{t("myAgent.errorStatus")}</span>
                        <span className={`font-medium ${chatError ? "text-destructive" : "text-foreground"}`}>{chatError ? t("myAgent.errorState") : t("myAgent.cleanState")}</span>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </aside>
          </div>
        </div>
      )}

      {/* ── Einstellungen Tab ─────────────────────────────────────────────── */}
      {tab === "settings" && agentInfo && (
        <SettingsPanel
          agentInfo={agentInfo}
          agents={agents}
          onSaved={loadAgent}
        />
      )}
      {tab === "settings" && !agentInfo && (
        <div className="flex-1 flex items-center justify-center p-8 text-center text-muted-foreground">
          <div className="max-w-md space-y-3">
            <p className="text-sm font-medium text-foreground">{t("myAgent.configNotLoaded")}</p>
            <p className="text-xs">{t("myAgent.configNotLoadedDetail")}</p>
          </div>
        </div>
      )}

      {/* ── Skills Tab ────────────────────────────────────────────────────── */}
      {tab === "skills" && agentInfo && (
        <div className="flex-1 overflow-y-auto">
          <SkillsPanel agentId={agentInfo.agent_id} />
        </div>
      )}
      {tab === "skills" && !agentInfo && (
        <div className="flex-1 flex items-center justify-center p-8 text-center text-muted-foreground">
          <div className="max-w-md space-y-3">
            <p className="text-sm font-medium text-foreground">{t("myAgent.skillsNotLoaded")}</p>
            <p className="text-xs">{t("myAgent.skillsNotLoadedDetail")}</p>
          </div>
        </div>
      )}

      {/* ── MCP Tab ───────────────────────────────────────────────────────── */}
      {tab === "mcp" && agentInfo && (
        <McpTab agentInfo={agentInfo} mcpServers={mcpServers} onSaved={loadAgent} />
      )}
      {tab === "mcp" && !agentInfo && (
        <div className="flex-1 flex items-center justify-center p-8 text-center text-muted-foreground">
          <div className="max-w-md space-y-3">
            <p className="text-sm font-medium text-foreground">{t("myAgent.mcpNotLoaded")}</p>
            <p className="text-xs">{t("myAgent.mcpNotLoadedDetail")}</p>
          </div>
        </div>
      )}

      {/* ── Platforms Tab ───────────────────────────────────────────────── */}
      {tab === "platforms" && (
        <PlatformsTab />
      )}

      {/* ── WKS Tab ───────────────────────────────────────────────────────── */}
      {tab === "wks" && (
        <WksTab />
      )}

      {/* ── Discord Tab ───────────────────────────────────────────────────── */}
      {tab === "discord" && (
        <DiscordTab />
      )}

      {/* ── WhatsApp Tab ──────────────────────────────────────────────────── */}
      {tab === "whatsapp" && (
        <WhatsAppTab />
      )}

      {/* ── Telegram Tab ──────────────────────────────────────────────────── */}
      {tab === "telegram" && (
        <TelegramTab />
      )}

      {/* ── Heartbeat Tab ─────────────────────────────────────────────────── */}
      {tab === "heartbeat" && agentInfo && (
        <HeartbeatTab agentInfo={agentInfo} onSaved={loadAgent} />
      )}
      {tab === "heartbeat" && !agentInfo && (
        <div className="flex-1 flex items-center justify-center p-8 text-center text-muted-foreground">
          <p className="text-sm">{t("myAgent.hbNotLoaded")}</p>
        </div>
      )}

      {/* ── Mail Tab ──────────────────────────────────────────────────────── */}
      {tab === "mail" && (
        <MailTab />
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
  const { t } = useTranslation();
  const cfg = agentInfo.config;
  const [selected, setSelected] = useState<string[]>(cfg.mcp_servers ?? []);
  const [saving,   setSaving]   = useState(false);
  const [msg,      setMsg]      = useState("");
  const amemServer = mcpServers.find((s) => s.id === "amem");
  const amemEnabled = selected.includes("amem");

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
      setMsg(t("myAgent.mcpSaved"));
      onSaved();
      setTimeout(() => setMsg(""), 3000);
    } catch(e) { setMsg(e instanceof Error ? e.message : "Fehler"); }
    finally { setSaving(false); }
  }

  if (mcpServers.length === 0) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center text-center p-8 text-muted-foreground space-y-3">
        <Plug className="h-10 w-10 opacity-30" />
        <p className="text-sm">{t("myAgent.mcpNoServers")}</p>
        <p className="text-xs">{t("myAgent.mcpNoServersHint")}</p>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-6 max-w-2xl">
      <div className="space-y-1">
        <h2 className="text-sm font-semibold">{t("myAgent.mcpTitle")}</h2>
        <p className="text-xs text-muted-foreground">{t("myAgent.mcpSubtitle")}</p>
      </div>

      {amemServer && (
        <div className="rounded-2xl border border-primary/20 bg-primary/5 p-4">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="space-y-1">
              <div className="flex items-center gap-2 text-sm font-medium">
                <Sparkles className="h-4 w-4 text-primary" />
                {t("myAgent.mcpAmemTitle")}
              </div>
              <p className="text-xs text-muted-foreground">
                {t("myAgent.mcpAmemDesc")}
              </p>
              <p className="text-xs font-mono text-muted-foreground">{amemServer.url}</p>
            </div>
            <div className="text-right text-xs">
              <div className={`inline-flex rounded-full px-2.5 py-1 ${amemEnabled ? "bg-emerald-500/15 text-emerald-700" : "bg-muted text-muted-foreground"}`}>
                {amemEnabled ? t("myAgent.mcpAmemAssigned") : t("myAgent.mcpAmemNotAssigned")}
              </div>
              {typeof amemServer.meta?.search_ui_url === "string" && (
                <div className="mt-2">
                  <a
                    href={resolveSearchUiUrl(amemServer.meta.search_ui_url as string)}
                    target="_blank"
                    rel="noreferrer"
                    className="text-primary hover:underline"
                  >
                    {t("myAgent.mcpSearchUi")}
                  </a>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

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
                {s.id === "amem" && <span className="text-xs text-primary bg-primary/10 px-1.5 py-0.5 rounded">shared memory</span>}
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
          {saving ? t("myAgent.mcpSaving") : t("myAgent.mcpSave")}
        </button>
        {msg && <span className={`text-xs ${msg.includes("✓") ? "text-green-600" : "text-destructive"}`}>{msg}</span>}
      </div>
    </div>
  );
}

// ── Platform Overview Tab ────────────────────────────────────────────────────

function PlatformsTab() {
  const { t } = useTranslation();
  const [username, setUsername] = useState("");
  const [platforms, setPlatforms] = useState<PlatformOverviewEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let mounted = true;
    setLoading(true);
    api.myPlatforms()
      .then(d => {
        if (!mounted) return;
        setUsername(d.username);
        setPlatforms(d.platforms);
      })
      .catch(e => {
        if (!mounted) return;
        setError(e instanceof Error ? e.message : "Fehler");
      })
      .finally(() => {
        if (mounted) setLoading(false);
      });
    return () => { mounted = false; };
  }, []);

  const supported = platforms.filter(p => p.supported);
  const planned = platforms.filter(p => !p.supported);

  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-6 max-w-5xl">
      <div className="space-y-1">
        <h2 className="text-sm font-semibold flex items-center gap-2">
          <Wifi className="h-4 w-4" />
          {t("myAgent.platformsTitle")}
        </h2>
        <p className="text-xs text-muted-foreground">
          {t("myAgent.platformsSubtitle")}
        </p>
      </div>

      {loading && (
        <div className="rounded-2xl border border-border/60 bg-card/70 p-6 text-sm text-muted-foreground">
          {t("myAgent.platformsLoading")}
        </div>
      )}

      {error && (
        <div className="rounded-2xl border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
          {error}
        </div>
      )}

      {!loading && !error && (
        <>
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {supported.map(entry => (
              <PlatformCard key={entry.platform} entry={entry} />
            ))}
          </div>

          {planned.length > 0 && (
            <div className="space-y-3">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{t("myAgent.platformsPlanned")}</h3>
              <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                {planned.map(entry => (
                  <PlatformCard key={entry.platform} entry={entry} />
                ))}
              </div>
            </div>
          )}

          <div className="rounded-2xl border border-border/60 bg-card/80 p-5 space-y-3">
            <h3 className="text-sm font-semibold">{t("myAgent.platformsOverview")}</h3>
            <ul className="space-y-2 text-sm text-muted-foreground">
              <li>{t("myAgent.platformsUser")}: <span className="font-mono text-foreground">{username || "?"}</span></li>
              <li>{t("myAgent.platformsActive")}: <span className="text-foreground font-medium">{supported.filter(p => p.connected).length}</span></li>
              <li>{t("myAgent.platformsPlannedCount")}: <span className="text-foreground font-medium">{planned.length}</span></li>
            </ul>
          </div>
        </>
      )}
    </div>
  );
}

function PlatformCard({ entry }: { entry: PlatformOverviewEntry }) {
  const { t } = useTranslation();
  const statusLabel = entry.supported
    ? (entry.connected ? t("myAgent.platformStatusConnected") : (entry.configured ? t("myAgent.platformStatusConfigured") : t("myAgent.platformStatusNotConfigured")))
    : t("myAgent.platformStatusPlanned");
  const statusClass = entry.supported
    ? (entry.connected ? "bg-emerald-500/15 text-emerald-700" : "bg-amber-500/15 text-amber-700")
    : "bg-muted text-muted-foreground";

  return (
    <div className="rounded-2xl border border-border/60 bg-card/80 p-4 space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h4 className="text-sm font-semibold">{entry.label}</h4>
          <p className="text-xs text-muted-foreground font-mono">{entry.platform}</p>
        </div>
        <span className={`rounded-full px-2.5 py-1 text-xs font-medium ${statusClass}`}>{statusLabel}</span>
      </div>
      <div className="space-y-2 text-xs">
        <div className="flex items-center justify-between gap-3">
          <span className="text-muted-foreground">{t("myAgent.platformSupported")}</span>
          <span className={entry.supported ? "text-emerald-700" : "text-muted-foreground"}>{entry.supported ? t("myAgent.platformYes") : t("myAgent.platformNo")}</span>
        </div>
        <div className="flex items-center justify-between gap-3">
          <span className="text-muted-foreground">{t("myAgent.platformConfigured")}</span>
          <span className={entry.configured ? "text-foreground" : "text-muted-foreground"}>{entry.configured ? t("myAgent.platformYes") : t("myAgent.platformNo")}</span>
        </div>
        <div className="flex items-center justify-between gap-3">
          <span className="text-muted-foreground">{t("myAgent.platformConnected")}</span>
          <span className={entry.connected ? "text-foreground" : "text-muted-foreground"}>{entry.connected ? t("myAgent.platformYes") : t("myAgent.platformNo")}</span>
        </div>
      </div>
      {Object.keys(entry.details || {}).length > 0 && (
        <div className="rounded-xl bg-muted/50 p-3 text-xs text-muted-foreground space-y-1">
          {Object.entries(entry.details).map(([key, value]) => (
            <div key={key} className="flex items-start justify-between gap-3">
              <span className="font-medium">{key}</span>
              <span className="text-right break-all">{Array.isArray(value) ? value.join(", ") : String(value)}</span>
            </div>
          ))}
        </div>
      )}
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
  const { t } = useTranslation();
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
  const [availableModels, setAvailableModels] = useState<{id:string;label:string;provider:string;wks_base_url?:string}[]>([]);

  useEffect(() => {
    api.get<{models:{id:string;label:string;provider:string;wks_base_url?:string}[]}>("/llm/available-models")
      .then(r => setAvailableModels(r.models))
      .catch(() => {});
  }, []);

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
      const selectedModel = availableModels.find(m => m.id === model);
      const ollama_base_url = selectedModel?.wks_base_url ?? null;
      await api.put("/me/agent", {
        identity, soul, model, temperature, max_tokens: maxTokens,
        fallback_models: fallbacks, tools, allowed_agents: allowedAgents,
        ollama_base_url,
      });
      setSaveMsg(t("myAgent.settingsSaved"));
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
            <Bot className="h-4 w-4" />{t("myAgent.settingsSectionPersonality")}
          </h2>
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">{t("myAgent.settingsNameLabel")}</label>
            <input value={identity} onChange={e => setIdentity(e.target.value)}
              className="w-full px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary" />
          </div>
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">{t("myAgent.settingsSoulLabel")}</label>
            <textarea value={soul} onChange={e => setSoul(e.target.value)} rows={6}
              placeholder={t("myAgent.settingsSoulPlaceholder")}
              className="w-full px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary resize-none font-mono" />
          </div>
        </section>

        {/* Modell */}
        <section className="space-y-3">
          <h2 className="text-sm font-semibold text-foreground">{t("myAgent.settingsSectionModel")}</h2>
          <div className="grid grid-cols-2 gap-3">
            <div className="col-span-2 space-y-1">
              <label className="text-xs text-muted-foreground">{t("myAgent.settingsPrimaryModel")}</label>
              <select value={model} onChange={e => setModel(e.target.value)}
                className="w-full px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary">
                {model && !availableModels.find(m => m.id === model) && (
                  <option value={model}>{model}</option>
                )}
                {availableModels.length === 0
                  ? KNOWN_MODELS.map(m => <option key={m} value={m}>{m}</option>)
                  : availableModels.map(m => <option key={m.id} value={m.id}>{m.label}</option>)
                }
              </select>
            </div>
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground">{t("myAgent.settingsTemperature", { value: temperature })}</label>
              <input type="range" min={0} max={1} step={0.05} value={temperature}
                onChange={e => setTemperature(parseFloat(e.target.value))}
                className="w-full" />
            </div>
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground">{t("myAgent.settingsMaxTokens")}</label>
              <input type="number" value={maxTokens} min={256} max={32000} step={256}
                onChange={e => setMaxTokens(parseInt(e.target.value))}
                className="w-full px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary" />
            </div>
          </div>
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">{t("myAgent.settingsFallbackModels")}</label>
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
              <select value={fbInput} onChange={e => setFbInput(e.target.value)}
                className="flex-1 px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary">
                <option value="">{t("myAgent.settingsSelectModel")}</option>
                {(availableModels.length === 0 ? KNOWN_MODELS.map(m=>({id:m,label:m})) : availableModels)
                  .filter(m => !fallbacks.includes(m.id))
                  .map(m => <option key={m.id} value={m.id}>{m.label}</option>)}
              </select>
              <button type="button" onClick={addFallback}
                className="px-3 py-2 text-sm border rounded-md hover:bg-accent transition-colors">
                <Plus className="h-4 w-4" />
              </button>
            </div>
          </div>
        </section>

        {/* Tools */}
        <section className="space-y-3">
          <h2 className="text-sm font-semibold text-foreground">{t("myAgent.settingsSectionTools")}</h2>
          <div className="grid grid-cols-2 gap-2">
            {ALL_TOOLS.map(t => {
              const isDanger = ["project_shell","create_agent","delete_agent","create_project","delete_project"].includes(t.id);
              return (
                <label key={t.id} className={`flex items-center gap-2 text-sm cursor-pointer select-none${isDanger ? " text-red-500" : ""}`}>
                  <input type="checkbox" checked={tools.includes(t.id)} onChange={() => toggleTool(t.id)}
                    className="rounded" />
                  <span className="text-xs">{t.label}</span>
                  <span className={`text-xs font-mono ${isDanger ? "text-red-400" : "text-muted-foreground"}`}>({t.id})</span>
                </label>
              );
            })}
          </div>
        </section>

        {/* Delegation */}
        {agents.length > 0 && (
          <section className="space-y-3">
            <h2 className="text-sm font-semibold text-foreground">{t("myAgent.settingsSectionDelegation")}</h2>
            <p className="text-xs text-muted-foreground">{t("myAgent.settingsDelegationHint")}</p>
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
            {saving ? t("myAgent.settingsSaving") : t("myAgent.settingsSave")}
          </button>
          {saveMsg && (
            <span className={`text-xs ${saveMsg.includes("✓") ? "text-green-600" : "text-destructive"}`}>
              {saveMsg}
            </span>
          )}
          <button type="button" onClick={onSaved}
            className="ml-auto flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors">
            <RefreshCw className="h-3 w-3" />{t("myAgent.settingsReload")}
          </button>
        </div>
      </form>
    </div>
  );
}

// ── WKS Tab ───────────────────────────────────────────────────────────────────

function WksTab() {
  const { t } = useTranslation();
  const [wks,         setWks]         = useState<WksConfig | null>(null);
  const [ip,          setIp]          = useState("");
  const [sshUser,     setSshUser]     = useState("");
  const [ollamaPort,  setOllamaPort]  = useState(11434);
  const [sshKey,      setSshKey]      = useState("");
  const [saving,      setSaving]      = useState(false);
  const [msg,         setMsg]         = useState("");
  const [wksModels,   setWksModels]   = useState<{id:string;label:string}[]>([]);
  const [testMsg,     setTestMsg]     = useState("");
  const [testing,     setTesting]     = useState(false);
  const [pubKey,      setPubKey]      = useState("");
  const [generating,  setGenerating]  = useState(false);
  const [sshTestMsg,  setSshTestMsg]  = useState("");
  const [sshTesting,  setSshTesting]  = useState(false);

  useEffect(() => {
    api.getWks().then(d => {
      setWks(d);
      setIp(d.ip);
      setSshUser(d.ssh_user);
      setOllamaPort(d.ollama_port);
      if (d.has_ssh_key) {
        api.getWksPubkey().then(r => setPubKey(r.public_key)).catch(() => {});
      }
    }).catch(() => {});
  }, []);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true); setMsg("");
    try {
      await api.updateWks({ ip, ssh_user: sshUser, ollama_port: ollamaPort, ssh_key: sshKey });
      setMsg(t("myAgent.wksSave") + " ✓");
      setSshKey("");
      const updated = await api.getWks();
      setWks(updated);
      setTimeout(() => setMsg(""), 3000);
    } catch(e) { setMsg(e instanceof Error ? e.message : "Fehler"); }
    finally { setSaving(false); }
  }

  async function generateKey() {
    setGenerating(true);
    try {
      const r = await api.generateWksKey();
      setPubKey(r.public_key);
      const updated = await api.getWks();
      setWks(updated);
      setMsg(t("myAgent.wksSshKeyGenerated"));
      setTimeout(() => setMsg(""), 3000);
    } catch(e) { setMsg(e instanceof Error ? e.message : "Fehler"); }
    finally { setGenerating(false); }
  }

  async function testSsh() {
    setSshTesting(true); setSshTestMsg("");
    try {
      const r = await api.testWksSsh();
      if (r.ok) setSshTestMsg(`✓ Verbunden — ${r.hostname} (${r.user})`);
      else setSshTestMsg(`✗ ${r.error}`);
    } catch(e) { setSshTestMsg(e instanceof Error ? e.message : "Fehler"); }
    finally { setSshTesting(false); }
  }

  async function testConnection() {
    setTesting(true); setTestMsg(""); setWksModels([]);
    try {
      const r = await api.getWksOllamaModels();
      if (r.models.length > 0) {
        setWksModels(r.models);
        setTestMsg(`✓ Verbunden — ${r.models.length} Modell(e) gefunden`);
      } else if (r.error) {
        setTestMsg(`Fehler: ${r.error}`);
      } else {
        setTestMsg("Verbunden, aber keine Ollama-Modelle gefunden");
      }
    } catch(e) { setTestMsg(e instanceof Error ? e.message : "Verbindung fehlgeschlagen"); }
    finally { setTesting(false); }
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <form onSubmit={save} className="p-6 space-y-8 max-w-2xl">
        <div className="space-y-1">
          <h2 className="text-sm font-semibold flex items-center gap-2">
            <Monitor className="h-4 w-4" />{t("myAgent.wksTitle")}
          </h2>
          <p className="text-xs text-muted-foreground">
            {t("myAgent.wksSubtitle")}
          </p>
          {wks?.configured && (
            <p className="text-xs text-green-600 font-medium">
              {t("myAgent.wksConfigured", { user: wks.ssh_user, ip: wks.ip, port: wks.ollama_port })}
              {wks.has_ssh_key && t("myAgent.wksHasSshKey")}
            </p>
          )}
        </div>

        <section className="space-y-3">
          <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">{t("myAgent.wksSectionConnection")}</h3>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground">{t("myAgent.wksIpLabel")}</label>
              <input value={ip} onChange={e => setIp(e.target.value)} placeholder="192.168.1.100"
                className="w-full px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary" />
            </div>
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground">{t("myAgent.wksSshUserLabel")}</label>
              <input value={sshUser} onChange={e => setSshUser(e.target.value)} placeholder="till"
                className="w-full px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary" />
            </div>
          </div>
          {/* SSH Key */}
          <div className="space-y-2">
            <label className="text-xs text-muted-foreground">{t("myAgent.wksSshKeyLabel")}</label>
            {pubKey ? (
              <div className="space-y-1.5">
                <div className="flex items-center gap-2">
                  <span className="text-xs text-green-600 font-medium">{t("myAgent.wksSshKeyPresent")}</span>
                  <button type="button" onClick={() => navigator.clipboard.writeText(pubKey)}
                    className="text-xs text-muted-foreground hover:text-foreground border rounded px-2 py-0.5">
                    {t("myAgent.wksCopyPublicKey")}
                  </button>
                </div>
                <p className="text-xs font-mono bg-muted/50 rounded px-2 py-1.5 break-all text-muted-foreground">
                  {pubKey}
                </p>
                <p className="text-xs text-muted-foreground">
                  {t("myAgent.wksSshKeyHint")}
                </p>
              </div>
            ) : (
              <div className="space-y-2">
                <button type="button" onClick={generateKey} disabled={generating}
                  className="flex items-center gap-2 px-3 py-1.5 text-sm border rounded-md hover:bg-accent transition-colors disabled:opacity-50">
                  {generating ? t("myAgent.wksGenerating") : t("myAgent.wksGenerateKey")}
                </button>
                <p className="text-xs text-muted-foreground">{t("myAgent.wksOrPasteKey")}</p>
                <textarea value={sshKey} onChange={e => setSshKey(e.target.value)} rows={4}
                  placeholder={"-----BEGIN OPENSSH PRIVATE KEY-----\n...\n-----END OPENSSH PRIVATE KEY-----"}
                  className="w-full px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary resize-none font-mono text-xs" />
              </div>
            )}
            {/* SSH Test */}
            {wks?.has_ssh_key && ip && (
              <div className="flex items-center gap-3 pt-1">
                <button type="button" onClick={testSsh} disabled={sshTesting}
                  className="flex items-center gap-2 px-3 py-1.5 text-sm border rounded-md hover:bg-accent transition-colors disabled:opacity-50">
                  {sshTesting ? t("myAgent.wksSshTesting") : t("myAgent.wksSshTest")}
                </button>
                {sshTestMsg && (
                  <span className={`text-xs ${sshTestMsg.startsWith("✓") ? "text-green-600" : "text-destructive"}`}>
                    {sshTestMsg}
                  </span>
                )}
              </div>
            )}
          </div>
        </section>

        <section className="space-y-3">
          <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">{t("myAgent.wksSectionOllama")}</h3>
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">{t("myAgent.wksOllamaPort")}</label>
            <input type="number" value={ollamaPort} onChange={e => setOllamaPort(parseInt(e.target.value))}
              className="w-40 px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary" />
          </div>
          <div className="flex items-center gap-3">
            <button type="button" onClick={testConnection} disabled={testing || !ip}
              className="flex items-center gap-2 px-3 py-2 text-sm border rounded-md hover:bg-accent transition-colors disabled:opacity-50">
              <RefreshCw className={`h-3.5 w-3.5 ${testing ? "animate-spin" : ""}`} />
              {testing ? t("myAgent.wksTesting") : t("myAgent.wksTestConnection")}
            </button>
            {testMsg && (
              <span className={`text-xs ${testMsg.startsWith("✓") ? "text-green-600" : "text-destructive"}`}>{testMsg}</span>
            )}
          </div>
          {wksModels.length > 0 && (
            <div className="space-y-1">
              <p className="text-xs text-muted-foreground">{t("myAgent.wksAvailableModels")}</p>
              <div className="flex flex-wrap gap-1">
                {wksModels.map(m => (
                  <span key={m.id} className="text-xs bg-secondary px-2 py-0.5 rounded font-mono">{m.label}</span>
                ))}
              </div>
            </div>
          )}
        </section>

        <div className="flex items-center gap-3">
          <button type="submit" disabled={saving || !ip}
            className="flex items-center gap-2 px-4 py-2 text-sm bg-primary text-primary-foreground rounded-md hover:bg-primary/90 disabled:opacity-50 transition-colors">
            <Save className="h-3.5 w-3.5" />
            {saving ? t("myAgent.wksSaving") : t("myAgent.wksSave")}
          </button>
          {msg && <span className={`text-xs ${msg.includes("✓") ? "text-green-600" : "text-destructive"}`}>{msg}</span>}
        </div>
      </form>
    </div>
  );
}


// ── Discord Tab ───────────────────────────────────────────────────────────────

function DiscordTab() {
  const { t } = useTranslation();
  const [cfg,          setCfg]          = useState<DiscordConfig | null>(null);
  const [botToken,     setBotToken]     = useState("");
  const [changeToken,  setChangeToken]  = useState(false);
  const [guildId,      setGuildId]      = useState("");
  const [selectedIds,  setSelectedIds]  = useState<Set<string>>(new Set());
  const [ignoreBots,           setIgnoreBots]           = useState(true);
  const [requireMention,       setRequireMention]       = useState(false);
  const [loopDetection,        setLoopDetection]        = useState(true);
  const [loopBotThreshold,     setLoopBotThreshold]     = useState(3);
  const [loopPingpongSeconds,  setLoopPingpongSeconds]  = useState(30);
  const [loopCooldownSeconds,  setLoopCooldownSeconds]  = useState(300);
  const [channels,     setChannels]     = useState<{id:string;name:string}[]>([]);
  const [loadingCh,    setLoadingCh]    = useState(false);
  const [saving,       setSaving]       = useState(false);
  const [testing,      setTesting]      = useState(false);
  const [msg,          setMsg]          = useState("");

  useEffect(() => {
    api.getDiscord().then(d => {
      setCfg(d);
      setGuildId(d.guild_id ?? "");
      setSelectedIds(new Set(d.channel_ids ?? []));
      setIgnoreBots(d.ignore_bots ?? true);
      setRequireMention(d.require_mention ?? false);
      setLoopDetection(d.loop_detection ?? true);
      setLoopBotThreshold(d.loop_bot_threshold ?? 6);
      setLoopPingpongSeconds(d.loop_pingpong_seconds ?? 30);
      setLoopCooldownSeconds(d.loop_cooldown_seconds ?? 300);
    }).catch(() => {});
  }, []);

  async function loadChannels() {
    setLoadingCh(true); setMsg("");
    try {
      const res = await api.getDiscordChannels();
      setChannels(res.channels ?? []);
      if ((res.channels ?? []).length === 0) setMsg("Keine Text-Channels gefunden");
    } catch (err: unknown) {
      setMsg("Fehler: " + (err instanceof Error ? err.message : String(err)));
    } finally { setLoadingCh(false); }
  }

  function toggleChannel(id: string) {
    setSelectedIds(prev => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true); setMsg("");
    try {
      const res = await api.updateDiscord({
        bot_token: (!cfg?.configured || changeToken) ? botToken.trim() : "",
        guild_id: guildId.trim(),
        channel_ids: [...selectedIds],
        ignore_bots: ignoreBots,
        require_mention: requireMention,
        loop_detection: loopDetection,
        loop_bot_threshold: loopBotThreshold,
        loop_pingpong_seconds: loopPingpongSeconds,
        loop_cooldown_seconds: loopCooldownSeconds,
      });
      setMsg(`✓ Bot "${res.bot_name}" verbunden`);
      setBotToken(""); setChangeToken(false);
      const updated = await api.getDiscord();
      setCfg(updated);
      setSelectedIds(new Set(updated.channel_ids ?? []));
    } catch (err: unknown) {
      setMsg("Fehler: " + (err instanceof Error ? err.message : String(err)));
    } finally { setSaving(false); }
  }

  async function handleDelete() {
    if (!confirm(t("myAgent.discordDeleteConfirm"))) return;
    await api.deleteDiscord();
    setCfg({ configured: false });
    setGuildId(""); setSelectedIds(new Set()); setChannels([]);
    setMsg("Bot entfernt");
  }

  async function handleTest() {
    setTesting(true); setMsg("");
    try {
      const res = await api.testDiscord();
      setMsg(res.ok ? `✓ Bot "${res.bot_name}" erreichbar` : `Fehler: ${res.error}`);
    } catch (err: unknown) {
      setMsg("Fehler: " + (err instanceof Error ? err.message : String(err)));
    } finally { setTesting(false); }
  }

  return (
    <div className="flex-1 overflow-y-auto p-4 space-y-6">
      <div>
        <h2 className="text-sm font-semibold mb-1">{t("myAgent.discordTitle")}</h2>
        <p className="text-xs text-muted-foreground">
          {t("myAgent.discordSubtitle")}
        </p>
      </div>

      {cfg?.configured && (
        <div className={`flex items-center gap-2 p-3 rounded-md text-xs border ${cfg.connected ? "bg-green-50 border-green-200 text-green-700" : "bg-yellow-50 border-yellow-200 text-yellow-700"}`}>
          {cfg.connected ? <Wifi className="h-3.5 w-3.5" /> : <WifiOff className="h-3.5 w-3.5" />}
          {cfg.connected ? t("myAgent.discordOnline") : t("myAgent.discordOffline")}
          {cfg.guild_id && <span className="ml-auto text-muted-foreground">{t("myAgent.discordGuild", { id: cfg.guild_id })}</span>}
        </div>
      )}

      <form onSubmit={handleSave} className="space-y-4">
        {/* Token */}
        <div>
          <label className="text-xs font-medium block mb-1">{t("myAgent.discordBotToken")}</label>
          {cfg?.configured && !changeToken ? (
            <div className="flex items-center gap-2">
              <span className="text-xs text-muted-foreground font-mono">••••••••••••••••••••</span>
              <button type="button" onClick={() => setChangeToken(true)}
                className="text-xs text-primary underline">{t("myAgent.discordChangeToken")}</button>
            </div>
          ) : (
            <>
              <input type="password" value={botToken} onChange={e => setBotToken(e.target.value)}
                placeholder="Bot-Token von Discord Developer Portal"
                className="w-full text-xs border rounded-md px-3 py-2 bg-background font-mono" />
              {cfg?.configured && (
                <button type="button" onClick={() => { setChangeToken(false); setBotToken(""); }}
                  className="text-xs text-muted-foreground underline mt-1">{t("myAgent.discordCancelToken")}</button>
              )}
            </>
          )}
        </div>

        {/* Guild ID */}
        <div>
          <label className="text-xs font-medium block mb-1">{t("myAgent.discordGuildId")}</label>
          <input type="text" value={guildId} onChange={e => setGuildId(e.target.value)}
            placeholder="z.B. 1234567890123456789"
            className="w-full text-xs border rounded-md px-3 py-2 bg-background font-mono" />
        </div>

        {/* Channels */}
        <div>
          <div className="flex items-center justify-between mb-2">
            <label className="text-xs font-medium">{t("myAgent.discordChannels")}</label>
            <button type="button" onClick={loadChannels} disabled={loadingCh || !cfg?.configured}
              className="flex items-center gap-1 text-xs text-primary hover:underline disabled:opacity-40">
              {loadingCh ? <RefreshCw className="h-3 w-3 animate-spin" /> : <RefreshCw className="h-3 w-3" />}
              {t("myAgent.discordLoadChannels")}
            </button>
          </div>
          {channels.length > 0 ? (
            <div className="border rounded-md divide-y max-h-48 overflow-y-auto">
              {channels.map(ch => (
                <label key={ch.id} className="flex items-center gap-2 px-3 py-2 text-xs cursor-pointer hover:bg-accent">
                  <input type="checkbox" checked={selectedIds.has(ch.id)} onChange={() => toggleChannel(ch.id)}
                    className="accent-primary" />
                  <span className="font-medium">#{ch.name}</span>
                  <span className="text-muted-foreground font-mono ml-auto">{ch.id}</span>
                </label>
              ))}
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">
              {selectedIds.size > 0
                ? t("myAgent.discordSelectedChannels", { count: selectedIds.size })
                : t("myAgent.discordNoChannels")}
            </p>
          )}
        </div>

        {/* Bots ignorieren */}
        <label className="flex items-center gap-2 text-xs cursor-pointer">
          <input type="checkbox" checked={ignoreBots} onChange={e => setIgnoreBots(e.target.checked)}
            className="accent-primary" />
          <span>{t("myAgent.discordIgnoreBots")}</span>
        </label>

        {/* Nur bei @Mention */}
        <label className="flex items-center gap-2 text-xs cursor-pointer">
          <input type="checkbox" checked={requireMention} onChange={e => setRequireMention(e.target.checked)}
            className="accent-primary" />
          <span>{t("myAgent.discordRequireMention")}</span>
        </label>

        {/* Loop-Detektion */}
        <div className="border rounded-md p-3 space-y-3">
          <label className="flex items-center gap-2 text-xs cursor-pointer font-medium">
            <input type="checkbox" checked={loopDetection} onChange={e => setLoopDetection(e.target.checked)}
              className="accent-primary" />
            <span>Loop-Detektion (Circuit Breaker)</span>
          </label>
          {loopDetection && (
            <div className="grid grid-cols-3 gap-3 pl-5">
              <div>
                <label className="text-xs text-muted-foreground block mb-1">Max. Bot-Nachrichten</label>
                <input type="number" min={2} max={50} value={loopBotThreshold}
                  onChange={e => setLoopBotThreshold(Number(e.target.value))}
                  className="w-full text-xs border rounded px-2 py-1 bg-background" />
              </div>
              <div>
                <label className="text-xs text-muted-foreground block mb-1">PingPong-Fenster (s)</label>
                <input type="number" min={5} max={300} value={loopPingpongSeconds}
                  onChange={e => setLoopPingpongSeconds(Number(e.target.value))}
                  className="w-full text-xs border rounded px-2 py-1 bg-background" />
              </div>
              <div>
                <label className="text-xs text-muted-foreground block mb-1">Cooldown (s)</label>
                <input type="number" min={10} max={3600} value={loopCooldownSeconds}
                  onChange={e => setLoopCooldownSeconds(Number(e.target.value))}
                  className="w-full text-xs border rounded px-2 py-1 bg-background" />
              </div>
            </div>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <button type="submit" disabled={saving || (!cfg?.configured && !botToken.trim())}
            className="flex items-center gap-2 px-4 py-2 text-sm bg-primary text-primary-foreground rounded-md hover:bg-primary/90 disabled:opacity-50 transition-colors">
            <Save className="h-3.5 w-3.5" />
            {saving ? t("myAgent.discordSaving") : t("myAgent.discordSave")}
          </button>
          {cfg?.configured && (
            <>
              <button type="button" onClick={handleTest} disabled={testing}
                className="flex items-center gap-2 px-3 py-2 text-sm border rounded-md hover:bg-accent disabled:opacity-50 transition-colors">
                {testing ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <CheckCircle className="h-3.5 w-3.5" />}
                {t("myAgent.discordTest")}
              </button>
              <button type="button" onClick={handleDelete}
                className="flex items-center gap-2 px-3 py-2 text-sm border border-destructive text-destructive rounded-md hover:bg-destructive/10 transition-colors">
                <X className="h-3.5 w-3.5" />
                {t("myAgent.discordRemove")}
              </button>
            </>
          )}
          {msg && (
            <span className={`text-xs flex items-center gap-1 ${msg.startsWith("✓") ? "text-green-600" : "text-destructive"}`}>
              {msg.startsWith("✓") ? <CheckCircle className="h-3 w-3" /> : <AlertCircle className="h-3 w-3" />}
              {msg}
            </span>
          )}
        </div>
      </form>

      <section className="space-y-2 border-t pt-4">
        <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wide">{t("myAgent.discordSetupTitle")}</h3>
        <ol className="text-xs text-muted-foreground space-y-1 list-decimal list-inside">
          <li>{t("myAgent.discordSetup1")}</li>
          <li>{t("myAgent.discordSetup2")}</li>
          <li>{t("myAgent.discordSetup3")}</li>
          <li>{t("myAgent.discordSetup4")}</li>
          <li>{t("myAgent.discordSetup5")}</li>
          <li>{t("myAgent.discordSetup6")}</li>
        </ol>
      </section>
    </div>
  );
}

// ── WhatsApp Tab ──────────────────────────────────────────────────────────────

function WhatsAppTab() {
  const { t } = useTranslation();
  const [status,  setStatus]  = useState<WhatsAppStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [msg,     setMsg]     = useState("");
  const [cfg, setCfg] = useState<WhatsAppConfig>({
    private_chats_enabled: true,
    group_chats_enabled:   false,
    require_keyword:       "",
    allowed_numbers:       [],
    blocked_numbers:       [],
    owner_numbers:         [],
  });
  const [cfgSaving, setCfgSaving] = useState(false);
  const [numInput,   setNumInput]   = useState("");
  const [blockInput, setBlockInput] = useState("");
  const [ownerInput, setOwnerInput] = useState("");
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  async function fetchStatus() {
    try {
      const s = await api.getWhatsApp();
      setStatus(s);
      if (s.private_chats_enabled !== undefined) {
        setCfg({
          private_chats_enabled: s.private_chats_enabled ?? true,
          group_chats_enabled:   s.group_chats_enabled   ?? false,
          require_keyword:       s.require_keyword       ?? "",
          allowed_numbers:       s.allowed_numbers       ?? [],
          blocked_numbers:       s.blocked_numbers       ?? [],
          owner_numbers:         s.owner_numbers         ?? [],
        });
      }
      if (s.status === "connected" || s.status === "bridge_unavailable" || s.status === "disconnected") {
        if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
      }
    } catch {}
  }

  async function saveCfg() {
    setCfgSaving(true); setMsg("");
    try {
      await api.updateWhatsAppConfig(cfg);
      setMsg("Gespeichert ✓");
      setTimeout(() => setMsg(""), 3000);
    } catch (e) { setMsg(e instanceof Error ? e.message : "Fehler"); }
    finally { setCfgSaving(false); }
  }

  useEffect(() => {
    fetchStatus();
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, []);

  async function handleConnect() {
    setLoading(true); setMsg("");
    try {
      const s = await api.connectWhatsApp();
      setStatus(s);
      // Polling starten bis verbunden
      if (s.status === "waiting_qr" || s.status === "connecting") {
        pollRef.current = setInterval(fetchStatus, 2500);
      }
    } catch (err: unknown) {
      setMsg("Fehler: " + (err instanceof Error ? err.message : String(err)));
    } finally { setLoading(false); }
  }

  async function handleDisconnect() {
    if (!confirm(t("myAgent.whatsappDisconnect") + "?")) return;
    try {
      await api.disconnectWhatsApp();
      setStatus({ configured: false, status: "disconnected", qr: null, phone: null });
      setMsg(t("myAgent.whatsappDisconnected2"));
      if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    } catch (err: unknown) {
      setMsg("Fehler: " + (err instanceof Error ? err.message : String(err)));
    }
  }

  const connected   = status?.status === "connected";
  const waitingQr   = status?.status === "waiting_qr";
  const reconnecting = status?.status === "reconnecting" || status?.status === "connecting";
  const bridgeDown  = status?.status === "bridge_unavailable";

  return (
    <div className="flex-1 overflow-y-auto p-4 space-y-6">
      <div>
        <h2 className="text-sm font-semibold mb-1">{t("myAgent.whatsappTitle")}</h2>
        <p className="text-xs text-muted-foreground">
          {t("myAgent.whatsappSubtitle")}
        </p>
      </div>

      {/* Status-Badge */}
      {status && (
        <div className={`flex items-center gap-2 p-3 rounded-md text-xs border ${
          connected   ? "bg-green-50 border-green-200 text-green-700" :
          waitingQr   ? "bg-yellow-50 border-yellow-200 text-yellow-700" :
          bridgeDown  ? "bg-red-50 border-red-200 text-red-700" :
                        "bg-muted border-border text-muted-foreground"
        }`}>
          {connected   ? <CheckCircle className="h-3.5 w-3.5" /> :
           waitingQr   ? <Sparkles className="h-3.5 w-3.5 animate-pulse" /> :
           bridgeDown  ? <AlertCircle className="h-3.5 w-3.5" /> :
                         <WifiOff className="h-3.5 w-3.5" />}
          <span>
            {connected    ? t("myAgent.whatsappConnected", { phone: status.phone ? ` · +${status.phone}` : "" }) :
             waitingQr    ? t("myAgent.whatsappWaitingQr") :
             reconnecting ? t("myAgent.whatsappReconnecting") :
             bridgeDown   ? t("myAgent.whatsappBridgeDown") :
                            t("myAgent.whatsappDisconnected")}
          </span>
          {(waitingQr || reconnecting) && (
            <span className="ml-auto text-xs opacity-60 animate-pulse">●</span>
          )}
        </div>
      )}

      {/* QR-Code */}
      {waitingQr && status?.qr && (
        <div className="flex flex-col items-center gap-3 p-4 border rounded-md bg-white">
          <p className="text-xs text-muted-foreground">
            {t("myAgent.whatsappQrHint")}
          </p>
          <img src={status.qr} alt="WhatsApp QR-Code" className="w-56 h-56 rounded" />
          <p className="text-xs text-muted-foreground animate-pulse">{t("myAgent.whatsappWaitScan")}</p>
        </div>
      )}

      {/* Aktionen */}
      <div className="flex flex-wrap items-center gap-2">
        {!connected && !waitingQr && !reconnecting && (
          <button onClick={handleConnect} disabled={loading}
            className="flex items-center gap-2 px-4 py-2 text-sm bg-primary text-primary-foreground rounded-md hover:bg-primary/90 disabled:opacity-50 transition-colors">
            <Phone className="h-3.5 w-3.5" />
            {loading ? t("myAgent.whatsappConnecting") : t("myAgent.whatsappConnect")}
          </button>
        )}
        {(connected || waitingQr || reconnecting) && (
          <button onClick={handleDisconnect}
            className="flex items-center gap-2 px-3 py-2 text-sm border border-destructive text-destructive rounded-md hover:bg-destructive/10 transition-colors">
            <X className="h-3.5 w-3.5" />
            {t("myAgent.whatsappDisconnect")}
          </button>
        )}
        {msg && (
          <span className={`text-xs flex items-center gap-1 ${msg.startsWith("✓") || msg === "Verbindung getrennt" ? "text-green-600" : "text-destructive"}`}>
            <AlertCircle className="h-3 w-3" />
            {msg}
          </span>
        )}
      </div>

      {/* Konfiguration */}
      {(status?.configured || (status && status.status !== "disconnected")) && (
        <section className="space-y-4 border-t pt-5">
          <h3 className="text-sm font-semibold">{t("myAgent.whatsappConfigTitle")}</h3>

          {/* Chat-Typen */}
          <div className="space-y-2">
            <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">{t("myAgent.whatsappRespondTo")}</p>
            <label className="flex items-center gap-3 cursor-pointer">
              <input type="checkbox" checked={cfg.private_chats_enabled}
                onChange={e => setCfg(c => ({ ...c, private_chats_enabled: e.target.checked }))} className="h-4 w-4 rounded" />
              <span className="text-sm">{t("myAgent.whatsappPrivateChats")}</span>
            </label>
            <label className="flex items-center gap-3 cursor-pointer">
              <input type="checkbox" checked={cfg.group_chats_enabled}
                onChange={e => setCfg(c => ({ ...c, group_chats_enabled: e.target.checked }))} className="h-4 w-4 rounded" />
              <span className="text-sm">{t("myAgent.whatsappGroupChats")}</span>
            </label>
          </div>

          {/* Keyword */}
          <div className="space-y-1">
            <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
              {t("myAgent.whatsappKeyword")}
            </label>
            <input value={cfg.require_keyword}
              onChange={e => setCfg(c => ({ ...c, require_keyword: e.target.value }))}
              placeholder="z.B. !agent  oder  @lilith"
              className="w-full px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary" />
            <p className="text-xs text-muted-foreground">{t("myAgent.whatsappKeywordHint")}</p>
          </div>

          {/* Whitelist */}
          <div className="space-y-2">
            <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">{t("myAgent.whatsappAllowed")}</p>
            <div className="flex flex-wrap gap-1">
              {cfg.allowed_numbers.map(n => (
                <span key={n} className="inline-flex items-center gap-1 px-2 py-0.5 text-xs bg-secondary rounded">
                  {n}
                  <button type="button" onClick={() => setCfg(c => ({ ...c, allowed_numbers: c.allowed_numbers.filter(x => x !== n) }))}>
                    <X className="h-2.5 w-2.5" />
                  </button>
                </span>
              ))}
            </div>
            <div className="flex gap-2">
              <input value={numInput} onChange={e => setNumInput(e.target.value)}
                placeholder="+491234567890" onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); if (numInput.trim()) { setCfg(c => ({ ...c, allowed_numbers: [...c.allowed_numbers, numInput.trim()] })); setNumInput(""); }}}}
                className="flex-1 px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary" />
              <button type="button" onClick={() => { if (numInput.trim()) { setCfg(c => ({ ...c, allowed_numbers: [...c.allowed_numbers, numInput.trim()] })); setNumInput(""); }}}
                className="px-3 py-2 text-sm border rounded-md hover:bg-accent transition-colors"><Plus className="h-4 w-4" /></button>
            </div>
          </div>

          {/* Blacklist */}
          <div className="space-y-2">
            <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">{t("myAgent.whatsappBlocked")}</p>
            <div className="flex flex-wrap gap-1">
              {cfg.blocked_numbers.map(n => (
                <span key={n} className="inline-flex items-center gap-1 px-2 py-0.5 text-xs bg-destructive/10 text-destructive rounded">
                  {n}
                  <button type="button" onClick={() => setCfg(c => ({ ...c, blocked_numbers: c.blocked_numbers.filter(x => x !== n) }))}>
                    <X className="h-2.5 w-2.5" />
                  </button>
                </span>
              ))}
            </div>
            <div className="flex gap-2">
              <input value={blockInput} onChange={e => setBlockInput(e.target.value)}
                placeholder="+491234567890" onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); if (blockInput.trim()) { setCfg(c => ({ ...c, blocked_numbers: [...c.blocked_numbers, blockInput.trim()] })); setBlockInput(""); }}}}
                className="flex-1 px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary" />
              <button type="button" onClick={() => { if (blockInput.trim()) { setCfg(c => ({ ...c, blocked_numbers: [...c.blocked_numbers, blockInput.trim()] })); setBlockInput(""); }}}
                className="px-3 py-2 text-sm border rounded-md hover:bg-accent transition-colors"><Plus className="h-4 w-4" /></button>
            </div>

            <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide pt-2">{t("myAgent.whatsappAdmins")}</p>
            <p className="text-xs text-muted-foreground">{t("myAgent.whatsappAdminsHint")}</p>
            <div className="flex flex-wrap gap-1">
              {cfg.owner_numbers.map(n => (
                <span key={n} className="inline-flex items-center gap-1 px-2 py-0.5 text-xs bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400 rounded">
                  <Shield className="h-2.5 w-2.5" />{n}
                  <button type="button" onClick={() => setCfg(c => ({ ...c, owner_numbers: c.owner_numbers.filter(x => x !== n) }))}>
                    <X className="h-2.5 w-2.5" />
                  </button>
                </span>
              ))}
            </div>
            <div className="flex gap-2">
              <input value={ownerInput} onChange={e => setOwnerInput(e.target.value)}
                placeholder="+491234567890" onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); if (ownerInput.trim()) { setCfg(c => ({ ...c, owner_numbers: [...c.owner_numbers, ownerInput.trim()] })); setOwnerInput(""); }}}}
                className="flex-1 px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary" />
              <button type="button" onClick={() => { if (ownerInput.trim()) { setCfg(c => ({ ...c, owner_numbers: [...c.owner_numbers, ownerInput.trim()] })); setOwnerInput(""); }}}
                className="px-3 py-2 text-sm border rounded-md hover:bg-accent transition-colors"><Plus className="h-4 w-4" /></button>
            </div>
          </div>

          <div className="flex items-center gap-3 pt-2">
            <button onClick={saveCfg} disabled={cfgSaving}
              className="flex items-center gap-2 px-4 py-2 text-sm bg-primary text-primary-foreground rounded-md hover:bg-primary/90 disabled:opacity-50 transition-colors">
              <Save className="h-3.5 w-3.5" />{cfgSaving ? t("myAgent.whatsappSaving") : t("myAgent.whatsappSave")}
            </button>
            {msg && <span className={`text-xs ${msg.includes("✓") ? "text-green-600" : "text-destructive"}`}>{msg}</span>}
          </div>
        </section>
      )}
    </div>
  );
}

// ── Mail Tab ───────────────────────────────────────────────────────────────────

// ── Heartbeat Tab ────────────────────────────────────────────────────────────

interface HbTask {
  id: string;
  message: string;
  interval: number | null;
  schedule: string | null;
  active_hours: string | null;
}

const TASK_PRESETS = [
  { label: "Mails prüfen",     id: "check_mail",    message: "Bitte prüfe deine Mails und antworte auf wichtige Nachrichten." },
  { label: "Issues lesen",     id: "check_issues",  message: "Bitte prüfe offene Gitea-Issues in deinen Projekten und gib eine kurze Zusammenfassung." },
  { label: "Tages-Briefing",   id: "daily_briefing",message: "Gib mir ein kurzes Briefing über den aktuellen Stand der laufenden Aufgaben." },
  { label: "Erinnerung",       id: "reminder",      message: "Prüfe ausstehende Aufgaben und erinnere mich an fällige Punkte." },
];

function HeartbeatTab({ agentInfo, onSaved }: { agentInfo: AgentInfo; onSaved: () => void }) {
  const { t } = useTranslation();
  const cfg = agentInfo.config as AgentCfg & {
    heartbeat?: { enabled?: boolean; interval?: string; timeout?: string; on_failure?: string };
    heartbeat_tasks?: HbTask[];
  };

  const [enabled,    setEnabled]    = useState(cfg.heartbeat?.enabled ?? true);
  const [interval,   setInterval]   = useState(cfg.heartbeat?.interval ?? "60s");
  const [timeout,    setTimeout_]   = useState(cfg.heartbeat?.timeout ?? "180s");
  const [onFailure,  setOnFailure]  = useState(cfg.heartbeat?.on_failure ?? "ignore");
  const [tasks,      setTasks]      = useState<HbTask[]>(cfg.heartbeat_tasks ?? []);
  const [saving,     setSaving]     = useState(false);
  const [msg,        setMsg]        = useState("");
  const [editIdx,    setEditIdx]    = useState<number | null>(null);
  const [editTask,   setEditTask]   = useState<HbTask | null>(null);
  const [newTask,    setNewTask]    = useState<HbTask>({ id: "", message: "", interval: 1800, schedule: null, active_hours: null });

  async function save() {
    setSaving(true); setMsg("");
    try {
      await api.patchMyAgentHeartbeat({
        heartbeat: { enabled, interval, timeout, on_failure: onFailure },
        heartbeat_tasks: tasks,
      });
      setMsg(t("myAgent.hbSaved"));
      onSaved();
      setTimeout(() => setMsg(""), 3000);
    } catch (e) { setMsg(e instanceof Error ? e.message : "Fehler"); }
    finally { setSaving(false); }
  }

  function addTask() {
    if (!newTask.id || !newTask.message) return;
    setTasks(t => [...t, { ...newTask }]);
    setNewTask({ id: "", message: "", interval: 1800, schedule: null, active_hours: null });
  }

  function removeTask(idx: number) {
    setTasks(t => t.filter((_, i) => i !== idx));
  }

  function startEdit(idx: number) {
    setEditIdx(idx);
    setEditTask({ ...tasks[idx] });
  }

  function saveEdit() {
    if (!editTask || editIdx === null) return;
    setTasks(t => t.map((item, i) => i === editIdx ? { ...editTask } : item));
    setEditIdx(null);
    setEditTask(null);
  }

  function cancelEdit() {
    setEditIdx(null);
    setEditTask(null);
  }

  function applyPreset(id: string, message: string) {
    setNewTask(t => ({ ...t, id: t.id || id, message }));
  }

  const inputCls = "w-full px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary";

  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-8 max-w-2xl">

      {/* Basis-Config */}
      <section className="space-y-4">
        <h2 className="text-sm font-semibold flex items-center gap-2"><Timer className="h-4 w-4" />{t("myAgent.hbTitle")}</h2>
        <label className="flex items-center gap-3 cursor-pointer">
          <input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} className="h-4 w-4 rounded" />
          <span className="text-sm font-medium">{t("myAgent.hbActive")}</span>
          <span className="text-xs text-muted-foreground">{t("myAgent.hbActiveHint")}</span>
        </label>
        <div className="grid grid-cols-3 gap-3">
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">{t("myAgent.hbInterval")}</label>
            <input value={interval} onChange={e => setInterval(e.target.value)} placeholder="60s" className={inputCls} />
          </div>
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">{t("myAgent.hbTimeout")}</label>
            <input value={timeout} onChange={e => setTimeout_(e.target.value)} placeholder="180s" className={inputCls} />
          </div>
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">{t("myAgent.hbOnTimeout")}</label>
            <select value={onFailure} onChange={e => setOnFailure(e.target.value)} className={inputCls}>
              <option value="ignore">ignore</option>
              <option value="restart">restart</option>
              <option value="stop">stop</option>
              <option value="alert">alert</option>
            </select>
          </div>
        </div>
      </section>

      {/* Aufgaben */}
      <section className="space-y-4">
        <h2 className="text-sm font-semibold flex items-center gap-2"><Bot className="h-4 w-4" />{t("myAgent.hbTasksTitle")}</h2>
        <p className="text-xs text-muted-foreground">{t("myAgent.hbTasksSubtitle")}</p>

        {/* Bestehende Tasks */}
        {tasks.length > 0 && (
          <div className="space-y-2">
            {tasks.map((task, i) => (
              editIdx === i && editTask ? (
                <div key={i} className="rounded-xl border bg-secondary/30 p-4 space-y-3">
                  <div className="grid grid-cols-2 gap-3">
                    <div className="space-y-1">
                      <label className="text-xs text-muted-foreground">{t("myAgent.hbTaskId")}</label>
                      <input value={editTask.id} onChange={e => setEditTask(et => et && ({ ...et, id: e.target.value }))}
                        className={inputCls} />
                    </div>
                    <div className="space-y-1">
                      <label className="text-xs text-muted-foreground">{t("myAgent.hbTaskInterval")}</label>
                      <input type="number" value={editTask.interval ?? ""} onChange={e => setEditTask(et => et && ({ ...et, interval: parseInt(e.target.value) || null, schedule: null }))}
                        className={inputCls} />
                    </div>
                  </div>
                  <div className="space-y-1">
                    <label className="text-xs text-muted-foreground">{t("myAgent.hbTaskMessage")}</label>
                    <textarea value={editTask.message} onChange={e => setEditTask(et => et && ({ ...et, message: e.target.value }))}
                      rows={4} className="w-full px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary resize-none" />
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <div className="space-y-1">
                      <label className="text-xs text-muted-foreground">{t("myAgent.hbTaskActiveHours")}</label>
                      <input value={editTask.active_hours ?? ""} onChange={e => setEditTask(et => et && ({ ...et, active_hours: e.target.value || null }))}
                        placeholder="07:00-22:00" className={inputCls} />
                    </div>
                    <div className="space-y-1">
                      <label className="text-xs text-muted-foreground">{t("myAgent.hbTaskCron")}</label>
                      <input value={editTask.schedule ?? ""} onChange={e => setEditTask(et => et && ({ ...et, schedule: e.target.value || null, interval: e.target.value ? null : et.interval }))}
                        placeholder="0 8 * * *" className={inputCls} />
                    </div>
                  </div>
                  <div className="flex gap-2">
                    <button type="button" onClick={saveEdit}
                      className="flex items-center gap-2 px-3 py-1.5 text-xs rounded-md bg-primary text-primary-foreground hover:bg-primary/90 transition-colors">
                      <Save className="h-3 w-3" />Speichern
                    </button>
                    <button type="button" onClick={cancelEdit}
                      className="flex items-center gap-2 px-3 py-1.5 text-xs rounded-md border hover:bg-accent transition-colors">
                      <X className="h-3 w-3" />Abbrechen
                    </button>
                  </div>
                </div>
              ) : (
                <div key={i} className="flex items-start gap-3 rounded-xl border bg-secondary/30 p-3">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 text-xs text-muted-foreground mb-1">
                      <span className="font-mono font-medium text-foreground">{task.id}</span>
                      {task.interval && <span>alle {task.interval >= 3600 ? `${task.interval/3600}h` : `${task.interval/60}min`}</span>}
                      {task.schedule && <span>Cron: {task.schedule}</span>}
                      {task.active_hours && <span>{task.active_hours}</span>}
                    </div>
                    <p className="text-sm truncate">{task.message}</p>
                  </div>
                  <div className="flex gap-1">
                    <button onClick={() => startEdit(i)} className="text-muted-foreground hover:text-foreground transition-colors p-1">
                      <Pencil className="h-3.5 w-3.5" />
                    </button>
                    <button onClick={() => removeTask(i)} className="text-muted-foreground hover:text-destructive transition-colors p-1">
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>
              )
            ))}
          </div>
        )}
        {tasks.length === 0 && (
          <p className="text-xs text-muted-foreground italic">{t("myAgent.hbNoTasks")}</p>
        )}

        {/* Neue Aufgabe */}
        <div className="rounded-xl border p-4 space-y-3">
          <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">{t("myAgent.hbNewTask")}</p>

          {/* Presets */}
          <div className="flex flex-wrap gap-2">
            {TASK_PRESETS.map(p => (
              <button key={p.label} type="button" onClick={() => applyPreset(p.id, p.message)}
                className="rounded-full border px-3 py-1 text-xs hover:bg-accent transition-colors">
                {p.label}
              </button>
            ))}
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground">{t("myAgent.hbTaskId")}</label>
              <input value={newTask.id} onChange={e => setNewTask(task => ({ ...task, id: e.target.value }))}
                placeholder="z.B. check_mail" className={inputCls} />
            </div>
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground">{t("myAgent.hbTaskInterval")}</label>
              <input type="number" value={newTask.interval ?? ""} onChange={e => setNewTask(task => ({ ...task, interval: parseInt(e.target.value) || null, schedule: null }))}
                placeholder="1800" className={inputCls} />
            </div>
          </div>
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">{t("myAgent.hbTaskMessage")}</label>
            <textarea value={newTask.message} onChange={e => setNewTask(task => ({ ...task, message: e.target.value }))}
              rows={3} placeholder={t("myAgent.hbTaskMessagePlaceholder")}
              className="w-full px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary resize-none" />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground">{t("myAgent.hbTaskActiveHours")}</label>
              <input value={newTask.active_hours ?? ""} onChange={e => setNewTask(task => ({ ...task, active_hours: e.target.value || null }))}
                placeholder="07:00-22:00" className={inputCls} />
            </div>
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground">{t("myAgent.hbTaskCron")}</label>
              <input value={newTask.schedule ?? ""} onChange={e => setNewTask(task => ({ ...task, schedule: e.target.value || null, interval: e.target.value ? null : task.interval }))}
                placeholder="0 8 * * *" className={inputCls} />
            </div>
          </div>
          <button type="button" onClick={addTask} disabled={!newTask.id || !newTask.message}
            className="flex items-center gap-2 px-4 py-2 text-sm rounded-md border hover:bg-accent transition-colors disabled:opacity-40">
            <Plus className="h-3.5 w-3.5" />{t("myAgent.hbAddTask")}
          </button>
        </div>
      </section>

      {/* Speichern */}
      <div className="flex items-center gap-3">
        <button onClick={save} disabled={saving}
          className="flex items-center gap-2 px-4 py-2 text-sm bg-primary text-primary-foreground rounded-md hover:bg-primary/90 disabled:opacity-50 transition-colors">
          <Save className="h-3.5 w-3.5" />{saving ? t("myAgent.hbSaving") : t("myAgent.hbSave")}
        </button>
        {msg && <span className={`text-xs ${msg.includes("✓") ? "text-green-600" : "text-destructive"}`}>{msg}</span>}
      </div>
    </div>
  );
}

function MailTab() {
  const { t } = useTranslation();
  const [cfg,          setCfg]         = useState<MailConfig | null>(null);
  const [mailAddress,  setMailAddress] = useState("");
  const [domain,       setDomain]      = useState("");
  const [createAcc,    setCreateAcc]   = useState(false);
  // manuelle SMTP-Felder
  const [smtpHost,     setSmtpHost]    = useState("");
  const [smtpPort,     setSmtpPort]    = useState("587");
  const [smtpUser,     setSmtpUser]    = useState("");
  const [smtpPassword, setSmtpPassword] = useState("");
  const [imapHost,     setImapHost]    = useState("");
  const [saving,       setSaving]      = useState(false);
  const [msg,          setMsg]         = useState("");

  useEffect(() => {
    api.getMail().then(d => {
      setCfg(d);
      setMailAddress(d.mail_address ?? "");
    }).catch(() => {});
  }, []);

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true); setMsg("");
    try {
      const payload: Parameters<typeof api.updateMail>[0] = {
        mail_address: mailAddress.trim(),
        domain: domain.trim(),
        create_account: createAcc,
      };
      if (!createAcc && smtpHost.trim()) {
        payload.smtp_host     = smtpHost.trim();
        payload.smtp_port     = parseInt(smtpPort) || 587;
        payload.smtp_user     = smtpUser.trim();
        payload.smtp_password = smtpPassword;
        payload.imap_host     = imapHost.trim();
      }
      const res = await api.updateMail(payload);
      setMsg(`✓ Mail-Adresse ${res.mail_address} ${res.created ? "angelegt" : "gespeichert"}`);
      const updated = await api.getMail();
      setCfg(updated);
      setMailAddress(updated.mail_address ?? "");
      setDomain(""); setSmtpPassword("");
    } catch (err: unknown) {
      setMsg("Fehler: " + (err instanceof Error ? err.message : String(err)));
    } finally { setSaving(false); }
  }

  async function handleDelete() {
    if (!confirm(t("myAgent.mailDeleteConfirm"))) return;
    try {
      await api.deleteMail();
      setCfg({ configured: false, mail_address: "", smtp_host: "" });
      setMailAddress(""); setDomain(""); setSmtpHost(""); setSmtpPassword("");
      setMsg(t("myAgent.mailRemoved"));
    } catch (err: unknown) {
      setMsg("Fehler: " + (err instanceof Error ? err.message : String(err)));
    }
  }

  return (
    <div className="flex-1 overflow-y-auto p-4 space-y-6">
      <div>
        <h2 className="text-sm font-semibold mb-1">{t("myAgent.mailTitle")}</h2>
        <p className="text-xs text-muted-foreground">
          {t("myAgent.mailSubtitle")}
        </p>
      </div>

      {cfg?.configured && (
        <div className="flex items-center gap-2 p-3 rounded-md text-xs border bg-green-50 border-green-200 text-green-700">
          <CheckCircle className="h-3.5 w-3.5" />
          <span>{t("myAgent.mailConfigured", { address: cfg.mail_address })}</span>
          {cfg.smtp_host && <span className="ml-auto text-muted-foreground font-mono">{cfg.smtp_host}</span>}
        </div>
      )}

      <form onSubmit={handleSave} className="space-y-4">
        <label className="flex items-center gap-2 text-xs cursor-pointer">
          <input type="checkbox" checked={createAcc} onChange={e => setCreateAcc(e.target.checked)}
            className="accent-primary" />
          <span>{t("myAgent.mailCreateAccount")}</span>
        </label>

        {createAcc ? (
          <>
            <div>
              <label className="text-xs font-medium block mb-1">{t("myAgent.mailLocalpart")}</label>
              <input type="text" value={mailAddress} onChange={e => setMailAddress(e.target.value)}
                placeholder="z.B. meinagent"
                className="w-full text-xs border rounded-md px-3 py-2 bg-background font-mono" />
              <p className="text-xs text-muted-foreground mt-1">{t("myAgent.mailLocalpartHint")}</p>
            </div>
            <div>
              <label className="text-xs font-medium block mb-1">{t("myAgent.mailDomain")}</label>
              <input type="text" value={domain} onChange={e => setDomain(e.target.value)}
                placeholder="z.B. hydrahive.org (leer = Server-Standard)"
                className="w-full text-xs border rounded-md px-3 py-2 bg-background font-mono" />
            </div>
          </>
        ) : (
          <>
            <div>
              <label className="text-xs font-medium block mb-1">{t("myAgent.mailFullAddress")}</label>
              <input type="email" value={mailAddress} onChange={e => setMailAddress(e.target.value)}
                placeholder="agent@example.com"
                className="w-full text-xs border rounded-md px-3 py-2 bg-background font-mono" />
            </div>
            <div className="space-y-3 p-3 border rounded-md bg-muted/30">
              <p className="text-xs font-medium">{t("myAgent.mailSmtpConfig")}</p>
              <div className="grid grid-cols-3 gap-2">
                <div className="col-span-2">
                  <label className="text-xs text-muted-foreground block mb-1">{t("myAgent.mailSmtpHost")}</label>
                  <input type="text" value={smtpHost} onChange={e => setSmtpHost(e.target.value)}
                    placeholder="smtp.example.com"
                    className="w-full text-xs border rounded-md px-3 py-2 bg-background font-mono" />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground block mb-1">{t("myAgent.mailSmtpPort")}</label>
                  <input type="number" value={smtpPort} onChange={e => setSmtpPort(e.target.value)}
                    placeholder="587"
                    className="w-full text-xs border rounded-md px-3 py-2 bg-background font-mono" />
                </div>
              </div>
              <div>
                <label className="text-xs text-muted-foreground block mb-1">{t("myAgent.mailSmtpUser")}</label>
                <input type="text" value={smtpUser} onChange={e => setSmtpUser(e.target.value)}
                  placeholder="agent@example.com"
                  className="w-full text-xs border rounded-md px-3 py-2 bg-background font-mono" />
              </div>
              <div>
                <label className="text-xs text-muted-foreground block mb-1">{t("myAgent.mailSmtpPassword")}</label>
                <input type="password" value={smtpPassword} onChange={e => setSmtpPassword(e.target.value)}
                  placeholder="SMTP-Passwort"
                  className="w-full text-xs border rounded-md px-3 py-2 bg-background" />
              </div>
              <div>
                <label className="text-xs text-muted-foreground block mb-1">{t("myAgent.mailImapHost")}</label>
                <input type="text" value={imapHost} onChange={e => setImapHost(e.target.value)}
                  placeholder="imap.example.com"
                  className="w-full text-xs border rounded-md px-3 py-2 bg-background font-mono" />
              </div>
            </div>
          </>
        )}

        <div className="flex flex-wrap items-center gap-2">
          <button type="submit" disabled={saving || !mailAddress.trim()}
            className="flex items-center gap-2 px-4 py-2 text-sm bg-primary text-primary-foreground rounded-md hover:bg-primary/90 disabled:opacity-50 transition-colors">
            <Save className="h-3.5 w-3.5" />
            {saving ? t("myAgent.mailSaving") : t("myAgent.mailSave")}
          </button>
          {cfg?.configured && (
            <button type="button" onClick={handleDelete}
              className="flex items-center gap-2 px-3 py-2 text-sm border border-destructive text-destructive rounded-md hover:bg-destructive/10 transition-colors">
              <X className="h-3.5 w-3.5" />
              {t("myAgent.mailRemove")}
            </button>
          )}
          {msg && (
            <span className={`text-xs flex items-center gap-1 ${msg.startsWith("✓") ? "text-green-600" : "text-destructive"}`}>
              {msg.startsWith("✓") ? <CheckCircle className="h-3 w-3" /> : <AlertCircle className="h-3 w-3" />}
              {msg}
            </span>
          )}
        </div>
      </form>

      <section className="space-y-2 border-t pt-4">
        <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wide">{t("myAgent.mailNotesTitle")}</h3>
        <ul className="text-xs text-muted-foreground space-y-1 list-disc list-inside">
          <li>{t("myAgent.mailNote1")}</li>
          <li>{t("myAgent.mailNote2")}</li>
          <li>{t("myAgent.mailNote3")}</li>
        </ul>
      </section>
    </div>
  );
}

// ── TelegramTab ───────────────────────────────────────────────────────────────

import type { TelegramStatus, TelegramConfig } from "../lib/api";

function TelegramTab() {
  const { t } = useTranslation();
  const [status,   setStatus]   = useState<TelegramStatus | null>(null);
  const [loading,  setLoading]  = useState(false);
  const [token,    setToken]    = useState("");
  const [cfg, setCfg] = useState<TelegramConfig>({
    allow_private:    true,
    allow_groups:     false,
    require_keyword:  "",
    allowed_user_ids: [],
    blocked_user_ids: [],
    admin_user_ids:   [],
  });
  const [cfgSaving,  setCfgSaving]  = useState(false);
  const [uidInput,   setUidInput]   = useState("");
  const [blockInput, setBlockInput] = useState("");
  const [adminInput, setAdminInput] = useState("");

  async function fetchStatus() {
    try {
      const s = await api.getTelegram();
      setStatus(s);
      if (s.allow_private !== undefined) {
        setCfg({
          allow_private:    s.allow_private    ?? true,
          allow_groups:     s.allow_groups     ?? false,
          require_keyword:  s.require_keyword  ?? "",
          allowed_user_ids: s.allowed_user_ids ?? [],
          blocked_user_ids: s.blocked_user_ids ?? [],
          admin_user_ids:   s.admin_user_ids   ?? [],
        });
      }
    } catch { /* ignore */ }
  }

  useEffect(() => { fetchStatus(); }, []);

  async function handleConnect() {
    if (!token.trim()) return;
    setLoading(true);
    try {
      const s = await api.connectTelegram({ bot_token: token, ...cfg });
      setStatus(s);
      setToken("");
    } catch (e: any) {
      alert(e?.message ?? "Verbindung fehlgeschlagen");
    } finally {
      setLoading(false);
    }
  }

  async function handleDisconnect() {
    if (!confirm(t("myAgent.telegramDisconnectConfirm"))) return;
    setLoading(true);
    try {
      await api.disconnectTelegram();
      setStatus({ configured: false, enabled: false, status: "stopped", bot_username: "" });
    } finally {
      setLoading(false);
    }
  }

  async function saveCfg() {
    setCfgSaving(true);
    try {
      await api.updateTelegramConfig(cfg);
      await fetchStatus();
    } finally {
      setCfgSaving(false);
    }
  }

  function addId(list: string[], val: string, setter: (v: string[]) => void, inputSetter: (v: string) => void) {
    const v = val.trim();
    if (v && !list.includes(v)) setter([...list, v]);
    inputSetter("");
  }

  const isRunning = status?.status === "running";
  const statusColor = isRunning
    ? "bg-green-500/20 text-green-600 dark:text-green-400"
    : status?.status === "error"
    ? "bg-destructive/20 text-destructive"
    : "bg-muted text-muted-foreground";
  const statusLabel = isRunning ? t("myAgent.telegramConnected") : status?.status === "error" ? t("myAgent.telegramError") : t("myAgent.telegramDisconnected");

  return (
    <div className="flex-1 overflow-y-auto p-4 sm:p-6 space-y-6 max-w-2xl">
      {/* Status */}
      <section className="rounded-2xl border bg-card p-5 space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Send className="h-5 w-5 text-blue-500" />
            <div>
              <h3 className="font-semibold text-sm">{t("myAgent.telegramTitle")}</h3>
              {status?.bot_username && (
                <p className="text-xs text-muted-foreground">{status.bot_username}</p>
              )}
            </div>
          </div>
          <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${statusColor}`}>
            {statusLabel}
          </span>
        </div>

        {/* Token-Input wenn nicht verbunden */}
        {!isRunning && (
          <div className="space-y-2">
            <label className="text-xs text-muted-foreground">{t("myAgent.telegramTokenLabel")}</label>
            <div className="flex gap-2">
              <input
                type="password"
                value={token}
                onChange={e => setToken(e.target.value)}
                placeholder="1234567890:ABCdef..."
                className="flex-1 rounded-lg border bg-background px-3 py-2 text-sm font-mono"
              />
              <button
                onClick={handleConnect}
                disabled={loading || !token.trim()}
                className="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium disabled:opacity-50"
              >
                {loading ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Send className="h-3.5 w-3.5" />}
                {loading ? t("myAgent.telegramConnecting") : t("myAgent.telegramConnect")}
              </button>
            </div>
            <p className="text-xs text-muted-foreground">
              {t("myAgent.telegramCreateHint")}
            </p>
          </div>
        )}

        {isRunning && (
          <button
            onClick={handleDisconnect}
            disabled={loading}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-destructive/50 text-destructive text-xs font-medium hover:bg-destructive/10"
          >
            <X className="h-3.5 w-3.5" /> {t("myAgent.telegramDisconnectBtn")}
          </button>
        )}
      </section>

      {/* Konfiguration */}
      {status?.configured && (
        <section className="rounded-2xl border bg-card p-5 space-y-5">
          <h3 className="font-semibold text-sm">{t("myAgent.telegramConfigTitle")}</h3>

          {/* Chat-Typen */}
          <div className="space-y-2">
            <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">{t("myAgent.telegramAllowedChats")}</p>
            {[
              { key: "allow_private", label: t("myAgent.telegramPrivate") },
              { key: "allow_groups",  label: t("myAgent.telegramGroups") },
            ].map(({ key, label }) => (
              <label key={key} className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={cfg[key as keyof TelegramConfig] as boolean}
                  onChange={e => setCfg(c => ({ ...c, [key]: e.target.checked }))}
                  className="rounded"
                />
                <span className="text-sm">{label}</span>
              </label>
            ))}
          </div>

          {/* Keyword */}
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">{t("myAgent.telegramKeyword")}</label>
            <input
              value={cfg.require_keyword}
              onChange={e => setCfg(c => ({ ...c, require_keyword: e.target.value }))}
              placeholder="z.B. !bot (leer = alle Nachrichten)"
              className="w-full rounded-lg border bg-background px-3 py-2 text-sm"
            />
          </div>

          {/* Admin-IDs */}
          <div className="space-y-2">
            <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide flex items-center gap-1">
              <Shield className="h-3 w-3" /> {t("myAgent.telegramAdmins")}
            </p>
            <div className="flex flex-wrap gap-1.5">
              {cfg.admin_user_ids.map(id => (
                <span key={id} className="flex items-center gap-1 text-xs bg-amber-500/15 text-amber-600 dark:text-amber-400 border border-amber-500/30 rounded-full px-2 py-0.5">
                  {id}
                  <button onClick={() => setCfg(c => ({ ...c, admin_user_ids: c.admin_user_ids.filter(x => x !== id) }))}><X className="h-3 w-3" /></button>
                </span>
              ))}
            </div>
            <div className="flex gap-2">
              <input value={adminInput} onChange={e => setAdminInput(e.target.value)}
                onKeyDown={e => e.key === "Enter" && addId(cfg.admin_user_ids, adminInput, ids => setCfg(c => ({ ...c, admin_user_ids: ids })), setAdminInput)}
                placeholder="Telegram User-ID (z.B. 123456789)"
                className="flex-1 rounded-lg border bg-background px-3 py-1.5 text-sm" />
              <button onClick={() => addId(cfg.admin_user_ids, adminInput, ids => setCfg(c => ({ ...c, admin_user_ids: ids })), setAdminInput)}
                className="px-3 py-1.5 rounded-lg border text-sm">+ Add</button>
            </div>
            <p className="text-xs text-muted-foreground">{t("myAgent.telegramAdminHint")}</p>
          </div>

          {/* Whitelist */}
          <div className="space-y-2">
            <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">{t("myAgent.telegramWhitelist")}</p>
            <div className="flex flex-wrap gap-1.5">
              {cfg.allowed_user_ids.map(id => (
                <span key={id} className="flex items-center gap-1 text-xs bg-muted rounded-full px-2 py-0.5">
                  {id}
                  <button onClick={() => setCfg(c => ({ ...c, allowed_user_ids: c.allowed_user_ids.filter(x => x !== id) }))}><X className="h-3 w-3" /></button>
                </span>
              ))}
            </div>
            <div className="flex gap-2">
              <input value={uidInput} onChange={e => setUidInput(e.target.value)}
                onKeyDown={e => e.key === "Enter" && addId(cfg.allowed_user_ids, uidInput, ids => setCfg(c => ({ ...c, allowed_user_ids: ids })), setUidInput)}
                placeholder="User-ID (leer = alle erlaubt)"
                className="flex-1 rounded-lg border bg-background px-3 py-1.5 text-sm" />
              <button onClick={() => addId(cfg.allowed_user_ids, uidInput, ids => setCfg(c => ({ ...c, allowed_user_ids: ids })), setUidInput)}
                className="px-3 py-1.5 rounded-lg border text-sm">+ Add</button>
            </div>
          </div>

          {/* Blacklist */}
          <div className="space-y-2">
            <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">{t("myAgent.telegramBlacklist")}</p>
            <div className="flex flex-wrap gap-1.5">
              {cfg.blocked_user_ids.map(id => (
                <span key={id} className="flex items-center gap-1 text-xs bg-destructive/10 text-destructive rounded-full px-2 py-0.5">
                  {id}
                  <button onClick={() => setCfg(c => ({ ...c, blocked_user_ids: c.blocked_user_ids.filter(x => x !== id) }))}><X className="h-3 w-3" /></button>
                </span>
              ))}
            </div>
            <div className="flex gap-2">
              <input value={blockInput} onChange={e => setBlockInput(e.target.value)}
                onKeyDown={e => e.key === "Enter" && addId(cfg.blocked_user_ids, blockInput, ids => setCfg(c => ({ ...c, blocked_user_ids: ids })), setBlockInput)}
                placeholder="User-ID blockieren"
                className="flex-1 rounded-lg border bg-background px-3 py-1.5 text-sm" />
              <button onClick={() => addId(cfg.blocked_user_ids, blockInput, ids => setCfg(c => ({ ...c, blocked_user_ids: ids })), setBlockInput)}
                className="px-3 py-1.5 rounded-lg border text-sm">+ Add</button>
            </div>
          </div>

          <button onClick={saveCfg} disabled={cfgSaving}
            className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium disabled:opacity-50">
            <Save className="h-3.5 w-3.5" />
            {cfgSaving ? t("myAgent.telegramSaving") : t("myAgent.telegramSave")}
          </button>
        </section>
      )}

      {/* Hinweis */}
      <section className="rounded-2xl border bg-muted/30 p-4 text-xs text-muted-foreground space-y-1">
        <p className="font-medium text-foreground">{t("myAgent.telegramSetupTitle")}</p>
        <ol className="list-decimal list-inside space-y-1">
          <li>{t("myAgent.telegramSetup1")}</li>
          <li>{t("myAgent.telegramSetup2")}</li>
          <li>{t("myAgent.telegramSetup3")}</li>
          <li>{t("myAgent.telegramSetup4")}</li>
        </ol>
      </section>
    </div>
  );
}
