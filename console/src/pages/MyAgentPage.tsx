import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { Send, Square, Bot, User, Terminal, Settings, BookOpen, Save, X, Plus, RefreshCw, Plug, Monitor, MessageSquare, CheckCircle, AlertCircle, Wifi, WifiOff, Sparkles, Shield, Smile, Mail, Phone, Timer, Trash2, Pencil, Workflow, Clock, ArrowLeft, RotateCcw, Download, Upload, KeyRound, Copy, Lightbulb, Menu, Puzzle, ChevronDown, ImagePlus } from "lucide-react";
import { cn } from "@/lib/utils";

const ButlerEmbed = lazy(() => import("./ButlerPage").then(m => ({ default: m.ButlerPage })));
import EmojiPicker, { type EmojiClickData, Theme } from "emoji-picker-react";
import { api, McpServer, WksConfig, DiscordConfig, MailConfig, WhatsAppStatus, WhatsAppConfig, PlatformOverviewEntry, type SessionPreview } from "@/lib/api";
import { useCapabilities } from "@/hooks/useCapabilities";
import { SkillsPanel } from "@/components/SkillsPanel";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import ReactMarkdown from "react-markdown";
import { useTranslation } from "react-i18next";
import { sseStream } from "@/lib/sseStream";

// ── Typen ────────────────────────────────────────────────────────────────────

interface Message { id: string; role: "user"|"assistant"|"system"|"tool"; content: string; tokenUsage?: { input: number; output: number; rounds?: number; cache_write?: number; cache_read?: number }; model?: string; isFallback?: boolean; ts?: string; }

function MsgTime({ iso }: { iso?: string }) {
  if (!iso) return null;
  try { const d = new Date(iso); return <span className="text-[10px] text-muted-foreground/50">{d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })}</span>; }
  catch { return null; }
}

interface AgentCfg {
  identity:        string;
  llm:             { model: string; temperature: number; max_tokens: number; fallback_models?: string[] };
  tools?:          string[];
  allowed_agents?: string[];
  mcp_servers?:    string[];
  execution_modes?: {
    default?: "safe" | "elevated" | "root" | "unrestricted";
    safe?: { permissions?: string[] };
    elevated?: { permissions?: string[] };
    root?: { permissions?: string[] };
    unrestricted?: { permissions?: string[] };
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
    unrestricted: "∞",
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
  ({ id: `m${++_cnt}`, role, content, ts: new Date().toISOString() });

// ── MessengerSection ──────────────────────────────────────────────────────────

function MessengerSection({ title, icon: Icon, defaultOpen, children }: {
  title: string; icon: any; defaultOpen?: boolean; children: React.ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen ?? false);
  return (
    <div className="border rounded-xl overflow-hidden">
      <button onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-3 px-4 py-3 hover:bg-muted transition-colors">
        <Icon className="h-4 w-4 text-muted-foreground" />
        <span className="font-medium text-sm">{title}</span>
        <ChevronDown className={cn("h-4 w-4 ml-auto text-muted-foreground transition-transform", open && "rotate-180")} />
      </button>
      {open && <div className="border-t px-4 py-4">{children}</div>}
    </div>
  );
}

// ── Haupt-Komponente ──────────────────────────────────────────────────────────

export function MyAgentPage() {
  const { t } = useTranslation();
  const { capabilities } = useCapabilities();

  const SLASH_COMMANDS = [
    { cmd: "/help",     desc: t("slashCommands.help") },
    { cmd: "/clear",    desc: t("slashCommands.clear") },
    { cmd: "/model",    desc: t("slashCommands.model") },
    { cmd: "/retry",    desc: t("slashCommands.retry") },
    { cmd: "/remember", desc: t("slashCommands.remember") },
    { cmd: "/history",  desc: "Vergangene Sessions anzeigen" },
  ];

  const [tab,        setTab]        = useState<string>("chat");
  const [userApps, setUserApps] = useState<{ id: string; name: string; tab: { label: string; icon: string; order: number }; config_fields: any[]; config: Record<string, unknown>; enabled: boolean }[]>([]);
  const [messages,   setMessages]   = useState<Message[]>([]);
  const [input,      setInput]      = useState("");
  const [sending,    setSending]    = useState(false);
  const [chatError,  setChatError]  = useState("");
  const [coachEnabled, setCoachEnabled] = useState(() => localStorage.getItem("hh_prompt_coach") === "1");
  const [coachFeedback, setCoachFeedback] = useState<{ ok: boolean; suggestion?: string; reason?: string } | null>(null);
  const [coachChecking, setCoachChecking] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [loadError,   setLoadError]  = useState("");
  const [agentInfo,  setAgentInfo]  = useState<AgentInfo | null>(null);
  const [showSuggest,  setShowSuggest]  = useState(false);
  const [showEmoji,    setShowEmoji]    = useState(false);
  const [suggestIdx,   setSuggestIdx]   = useState(0);
  const [showHistory,  setShowHistory]  = useState(false);
  const [pastSessions, setPastSessions] = useState<SessionPreview[]>([]);
  const [viewSession,  setViewSession]  = useState<{ id: string; messages: Message[]; startedAt: string } | null>(null);
  const [agents,     setAgents]     = useState<string[]>([]);
  const [mcpServers, setMcpServers] = useState<McpServer[]>([]);
  const [activeTool,     setActiveTool]     = useState<{name:string;detail:string} | null>(null);
  const [streamingMsgId, setStreamingMsgId] = useState<string | null>(null);
  const [doneMsgId,      setDoneMsgId]      = useState<string | null>(null);

  const [pendingImages, setPendingImages] = useState<{data: string; media_type: string; preview: string}[]>([]);
  const [lightboxSrc, setLightboxSrc] = useState<string | null>(null);
  const fileInputRef    = useRef<HTMLInputElement>(null);
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
      setLoadError(e instanceof Error ? e.message : t("common.error"));
    }
  }

  useEffect(() => {
    loadAgent();
    api.get<{ apps: typeof userApps }>("/me/user-apps").then(r => setUserApps(r.apps || [])).catch(e => console.error("Failed to load user apps", e));
    api.get<{session_id:string|null;messages:{role:string;content:string}[];count:number}>(
      "/me/agent/session/history"
    ).then(d => {
      const loaded = d.messages
        .filter((m: any) => m.role === "user" || m.role === "assistant" || m.role === "tool")
        .map((m: any) => {
          const msg = mkMsg(m.role as any, m.content);
          if (m.metadata?.input_tokens || m.metadata?.output_tokens) {
            msg.tokenUsage = { input: m.metadata.input_tokens || 0, output: m.metadata.output_tokens || 0, rounds: m.metadata.rounds, cache_write: m.metadata.cache_write_tokens || 0, cache_read: m.metadata.cache_read_tokens || 0 };
          }
          return msg;
        });
      if (loaded.length > 0) setMessages(loaded);
    }).catch(e => console.error("Failed to load agent session history", e));
    api.get<Record<string,unknown>>("/agents").then(d => {
      setAgents(Object.keys(d).filter(id => !id.startsWith("personal_")));
    }).catch(e => console.error("Failed to load agents list", e));
    api.mcpServers().then(d => setMcpServers(d.servers)).catch(e => console.error("Failed to load MCP servers", e));
  }, []);

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages]);
  useEffect(() => { setShowSuggest(suggestions.length > 0 && input.length > 0); setSuggestIdx(0); }, [input]);

  // ── Chat-Logik ────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!showHistory || !agentInfo?.agent_id) return;
    api.listSessions(agentInfo.agent_id, 30).then(d => setPastSessions(d.sessions)).catch(e => console.error("Failed to list past sessions", e));
  }, [showHistory, agentInfo?.agent_id]);

  async function openPastSession(sid: string) {
    if (!agentInfo?.agent_id) return;
    try {
      const d = await api.getSessionById(agentInfo.agent_id, sid);
      const msgs = d.messages
        .filter(m => m.role === "user" || m.role === "assistant")
        .map(m => mkMsg(m.role as "user" | "assistant", m.content));
      setViewSession({ id: d.id, messages: msgs, startedAt: d.started_at });
      setShowHistory(false);
    } catch {}
  }

  async function resumePastSession(sid: string) {
    if (!agentInfo?.agent_id) return;
    try {
      const d = await api.resumeSession(agentInfo.agent_id, sid);
      const msgs = d.messages
        .filter(m => m.role === "user" || m.role === "assistant")
        .map(m => mkMsg(m.role as "user" | "assistant", m.content));
      setMessages(msgs);
      setViewSession(null);
      setShowHistory(false);
    } catch {}
  }

  function sysMsg(c: string) { setMessages(ms => [...ms, mkMsg("system", c)]); }

  function handleSlash(cmd: string): boolean {
    const base = cmd.trim().split(/\s+/)[0].toLowerCase();
    const agentId = agentInfo?.agent_id;
    if (base === "/help") { sysMsg("**Commands:**\n\n" + SLASH_COMMANDS.map(c=>`\`${c.cmd}\` — ${c.desc}`).join("\n")); return true; }
    if (base === "/clear") {
      setMessages([]);
      api.delete("/me/agent/session").catch(e => console.error("Failed to clear agent session", e));
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
    if (base === "/history") {
      setViewSession(null);
      setShowHistory(true);
      return true;
    }
    sysMsg(`Unbekannter Command: \`${base}\`. /help für Übersicht.`); return true;
  }

  async function stop() {
    if (abortRef.current) abortRef.current.abort();
    const token = localStorage.getItem("hydrahive_token") || "";
    fetch("/api/me/agent/interrupt", { method: "POST", headers: { Authorization: `Bearer ${token}` } }).catch(e => console.error("Failed to interrupt agent", e));
  }

  async function send(overrideContent?: string) {
    const rawContent = overrideContent ?? input.trim();
    if (!rawContent || sending) return;
    const content = rawContent;
    setInput(""); setChatError(""); setShowSuggest(false); setCoachFeedback(null);
    if (content.startsWith("/")) { handleSlash(content); return; }
    // Companion-Event
    window.dispatchEvent(new CustomEvent("hh-chat-sent", { detail: { text: content } }));

    // Prompt-Coach Check (#169)
    if (!overrideContent && coachEnabled) {
      setCoachChecking(true);
      try {
        const check = await api.post<{ ok: boolean; suggestion?: string; reason?: string }>("/me/agent/coach", { content });
        if (!check.ok) {
          setCoachFeedback(check);
          setInput(content); // Input wiederherstellen
          setCoachChecking(false);
          return;
        }
      } catch { /* Coach-Fehler → durchlassen */ }
      setCoachChecking(false);
    }

    const userMsg = { ...mkMsg("user", content), _images: pendingImages.map(i => i.preview) } as any;
    let curAsst = mkMsg("assistant", "");
    let hadTools = false;
    setMessages(ms => [...ms, userMsg]);
    setSending(true);
    setElapsed(0);
    const controller = new AbortController();
    abortRef.current = controller;
    elapsedTimerRef.current = setInterval(() => setElapsed((s) => s + 1), 1000);
    try {
      setPendingImages([]);
      setMessages(ms => [...ms, curAsst]);
      setStreamingMsgId(curAsst.id);

      await sseStream({
        url: "/api/me/agent/message/stream",
        body: {
          content,
          ...(pendingImages.length > 0 ? { images: pendingImages.map(i => ({ data: i.data, media_type: i.media_type })) } : {}),
        },
        signal: controller.signal,
        onConnectionLost: () => setChatError(t("common.connectionLost", { defaultValue: "Verbindung verloren — bitte nochmal senden" })),
        onEvent: (evt) => {
          if (evt.type === "text") {
            setActiveTool(null);
            if (hadTools) { curAsst = mkMsg("assistant", ""); setMessages(ms => [...ms, curAsst]); setStreamingMsgId(curAsst.id); hadTools = false; }
            setMessages(ms => ms.map(m => m.id===curAsst.id ? {...m,content:m.content+evt.text} : m));
          }
          else if (evt.type === "tool_image") {
            const imgMsg = mkMsg("tool" as any, `__IMG__${evt.tool_name || "screenshot"}|${evt.tool_image}`);
            setMessages(ms => [...ms, imgMsg]);
          }
          else if (evt.type === "tool_call") {
            setActiveTool({ name: evt.tool_call, detail: toolDetail(evt.tool_call, evt.tool_input ?? {}) });
            const toolMsg = mkMsg("tool" as any, `${evt.tool_call}|${evt.tool_detail || toolDetail(evt.tool_call, evt.tool_input ?? {})}`);
            setMessages(ms => [...ms, toolMsg]);
            hadTools = true;
          }
          else if (evt.type === "done") {
            const updates: Partial<Message> = {};
            if (evt.usage && (evt.usage.input > 0 || evt.usage.output > 0))
              updates.tokenUsage = evt.usage;
            if (evt.is_fallback)
              Object.assign(updates, { model: evt.model, isFallback: true });
            if (Object.keys(updates).length > 0)
              setMessages(ms => ms.map(m => m.id===curAsst.id ? {...m, ...updates} : m));
          }
          else if (evt.type === "error") {
            if (evt.session_reset) setMessages([]);
            throw new Error(evt.error);
          }
        },
      });
    } catch(e) {
      if (e instanceof DOMException && e.name === "AbortError") {
        // User aborted — keep partial response, no error
      } else {
        setChatError(e instanceof Error ? e.message : t("common.error"));
        setMessages(ms => ms.filter(m => m.id!==userMsg.id && m.id!==curAsst.id));
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
    <div className="flex flex-col h-full min-w-0 overflow-x-hidden">
      {/* Header + Tabs */}
      {(() => {
        const TAB_LIST = [
          { id: "chat",      label: t("myAgent.chatTab"),       icon: Bot },
          { id: "heartbeat", label: t("myAgent.heartbeatTab"),  icon: Timer },
          { id: "messenger", label: t("myAgent.messengerTab"),  icon: MessageSquare },
          { id: "wks",       label: t("myAgent.wksTab"),        icon: Monitor },
          { id: "butler",    label: "Butler",                   icon: Workflow },
          { id: "account",   label: "Mein Konto",               icon: KeyRound },
          // Dynamische User-App Tabs
          ...userApps.filter(a => a.enabled).map(a => ({
            id: `app-${a.id}`,
            label: a.tab.label,
            icon: Puzzle, // Default Icon für User-Apps
          })),
        ];
        const activeTab = TAB_LIST.find(t => t.id === tab);
        return (
          <>
            <div className="border-b flex-shrink-0 min-w-0">
              <div className="flex items-center gap-3 px-4 py-3">
                {/* Hamburger — nur Mobile */}
                <button onClick={() => setDrawerOpen(true)} className="md:hidden flex items-center justify-center w-8 h-8 rounded-lg hover:bg-muted transition-colors flex-shrink-0" aria-label="Open menu">
                  <Menu className="h-5 w-5" />
                </button>
                <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center flex-shrink-0 hidden md:flex">
                  <Bot className="h-4 w-4 text-primary" />
                </div>
                <div className="flex-1 min-w-0">
                  <h1 className="text-sm font-semibold truncate">{identity}</h1>
                  {model && <p className="text-xs text-muted-foreground font-mono truncate">{model}</p>}
                </div>
                {/* Mobile: aktiver Tab als Badge */}
                {activeTab && (
                  <span className="md:hidden text-xs text-primary font-medium flex items-center gap-1">
                    <activeTab.icon className="h-3.5 w-3.5" />{activeTab.label}
                  </span>
                )}
              </div>
              {/* Desktop Tabs */}
              <div className="hidden md:flex gap-0 px-4 overflow-x-auto scrollbar-none min-w-0">
                {TAB_LIST.map(({ id, label, icon: Icon }) => (
                  <button key={id} onClick={() => setTab(id as typeof tab)}
                    className={`flex shrink-0 items-center gap-1.5 whitespace-nowrap px-3 py-2 text-xs border-b-2 transition-colors ${
                      tab === id
                        ? "border-primary text-primary font-medium"
                        : "border-transparent text-muted-foreground hover:text-foreground"
                    }`}>
                    <Icon className="h-3.5 w-3.5" />{label}
                  </button>
                ))}
              </div>
            </div>

            {/* Mobile Drawer */}
            {drawerOpen && (
              <>
                <div className="fixed inset-0 bg-black/50 z-40 md:hidden" onClick={() => setDrawerOpen(false)} />
                <div className="fixed left-0 top-0 bottom-0 w-64 bg-card border-r z-50 md:hidden overflow-y-auto">
                  <div className="flex items-center justify-between px-4 py-3 border-b">
                    <span className="text-sm font-semibold">{identity}</span>
                    <button onClick={() => setDrawerOpen(false)} className="p-1 rounded-lg hover:bg-muted" aria-label="Close menu"><X className="h-4 w-4" /></button>
                  </div>
                  <div className="py-2">
                    {TAB_LIST.map(({ id, label, icon: Icon }) => (
                      <button key={id} onClick={() => { setTab(id as typeof tab); setDrawerOpen(false); }}
                        className={`flex items-center gap-3 w-full px-4 py-3 text-sm transition-colors ${
                          tab === id ? "bg-primary/10 text-primary font-medium" : "text-muted-foreground hover:bg-muted/50 hover:text-foreground"
                        }`}>
                        <Icon className="h-4 w-4" />{label}
                      </button>
                    ))}
                  </div>
                </div>
              </>
            )}
          </>
        );
      })()}

      {/* ── Chat Tab ──────────────────────────────────────────────────────── */}
      {tab === "chat" && (
        <div className="flex-1 overflow-hidden flex flex-col pt-4 pb-4 pl-4 sm:pt-6 sm:pb-6 sm:pl-6 pr-4 sm:pr-6 min-h-0 min-w-0">
          <div className="grid flex-1 min-h-0 min-w-0 gap-6 xl:grid-cols-[minmax(0,1fr)_16rem]">
            <section className="flex flex-col min-h-0 min-w-0 gap-4">
              {loadError && (
                <div className="rounded-2xl border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive">
                  {loadError.includes("Token")
                    ? t("myAgent.sessionExpired")
                    : loadError}
                </div>
              )}
              <div className="flex flex-col flex-1 min-h-0 min-w-0 rounded-[28px] border border-border/60 bg-card/80 shadow-[0_20px_80px_rgba(15,23,42,0.08)] backdrop-blur">
                <div className="border-b border-border/60 px-4 py-3 sm:px-5">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                      <h3 className="text-sm font-semibold">{t("myAgent.historyTitle")}</h3>
                      <p className="text-xs text-muted-foreground">{t("myAgent.historySubtitle")}</p>
                    </div>
                    <div className="flex items-center gap-2">
                      {activeTool && (
                        <div className="inline-flex max-w-full items-center gap-2 rounded-full border border-primary/20 bg-primary/10 px-3 py-1 text-xs text-primary">
                          <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                          <code className="max-w-[10rem] truncate font-mono">{activeTool.name}</code>
                        </div>
                      )}
                      <button onClick={() => { setShowHistory(h => !h); setViewSession(null); }}
                        className={`p-1.5 rounded-lg transition-colors ${showHistory ? "bg-primary/10 text-primary" : "hover:bg-muted text-muted-foreground"}`}
                        title="Vergangene Sessions"
                        aria-label="Toggle chat history">
                        <Clock className="h-4 w-4" />
                      </button>
                    </div>
                  </div>
                </div>

                {/* History Panel */}
                {showHistory && (
                  <div className="border-b border-border/60">
                    <div className="flex items-center justify-between px-4 py-2 bg-muted/30">
                      <span className="text-xs font-medium text-muted-foreground">Vergangene Sessions</span>
                      <button onClick={() => setShowHistory(false)} className="p-1 rounded hover:bg-accent" aria-label="Close history">
                        <X className="h-3.5 w-3.5" />
                      </button>
                    </div>
                    {pastSessions.length === 0 ? (
                      <p className="text-xs text-muted-foreground text-center py-6">Keine vergangenen Sessions</p>
                    ) : (
                      <div className="divide-y max-h-64 overflow-y-auto">
                        {pastSessions.map(s => (
                          <div key={s.id} className="flex items-stretch hover:bg-accent/50 transition-colors">
                            <button onClick={() => openPastSession(s.id)}
                              className="flex-1 text-left px-4 py-3">
                              <div className="flex items-center justify-between gap-2">
                                <span className="text-xs font-medium">{new Date(s.started_at).toLocaleString("de")}</span>
                                <span className="text-xs text-muted-foreground">{s.message_count} Nachr.</span>
                              </div>
                              {s.preview && (
                                <p className="text-xs text-muted-foreground truncate mt-0.5">{s.preview}</p>
                              )}
                            </button>
                            <button onClick={() => resumePastSession(s.id)}
                              title="Chat fortsetzen"
                              aria-label="Resume session"
                              className="flex items-center px-3 text-primary hover:bg-primary/10 border-l transition-colors flex-shrink-0">
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
                  <div className="flex items-center justify-between px-4 py-2 bg-amber-500/10 border-b border-border/60 text-xs flex-shrink-0 gap-2">
                    <span className="text-amber-600 dark:text-amber-400 font-medium truncate min-w-0">
                      Vergangene Session — {new Date(viewSession.startedAt).toLocaleString("de")}
                    </span>
                    <div className="flex gap-2 flex-shrink-0">
                      <button onClick={() => { setViewSession(null); setShowHistory(true); }}
                        className="flex items-center gap-1 px-2 py-1 rounded hover:bg-accent transition-colors text-muted-foreground">
                        <ArrowLeft className="h-3 w-3" /> Zurück
                      </button>
                      <button onClick={() => resumePastSession(viewSession.id)}
                        className="flex items-center gap-1 px-2 py-1 rounded bg-primary text-primary-foreground hover:bg-primary/90 transition-colors">
                        <RotateCcw className="h-3 w-3" /> Fortsetzen
                      </button>
                      <button onClick={() => { setViewSession(null); api.delete("/me/agent/session").catch(e => console.error("Failed to delete agent session", e)); setMessages([]); }}
                        className="flex items-center gap-1 px-2 py-1 rounded border hover:bg-accent transition-colors text-muted-foreground">
                        <Plus className="h-3 w-3" /> Neuer Chat
                      </button>
                    </div>
                  </div>
                )}

                <div className="flex-1 overflow-y-auto min-h-0 space-y-3 sm:space-y-4 px-3 py-3 sm:px-5 sm:py-5">
                  {!viewSession && messages.length === 0 && (
                    <div className="flex min-h-[18rem] flex-col items-center justify-center rounded-3xl border border-dashed border-border/70 bg-muted/20 px-6 text-center text-muted-foreground">
                      <Bot className="h-10 w-10 opacity-70" />
                      <p className="mt-4 text-sm font-medium text-foreground">{t("myAgent.greetingTitle", { name: identity })}</p>
                      <p className="mt-2 max-w-md text-xs">{t("myAgent.greetingSubtitle")} <code className="rounded bg-background px-1.5 py-0.5">/help</code> {t("myAgent.greetingSubtitle2")}</p>
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
                            <div className="flex flex-wrap gap-1.5 max-w-[90%] justify-center">
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
                    if (msg.role === "system") return (
                      <div key={msg.id} className="flex justify-center">
                        <div className="flex max-w-[90%] items-start gap-2 rounded-2xl border border-border/70 bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
                          <Terminal className="mt-0.5 h-3.5 w-3.5 flex-shrink-0 text-primary/60" />
                          <div className="prose prose-sm max-w-none dark:prose-invert prose-p:my-0.5 overflow-x-auto">
                            <ReactMarkdown>{msg.content}</ReactMarkdown>
                          </div>
                        </div>
                      </div>
                    );

                    return (
                      <div key={msg.id} className={`flex flex-col ${msg.role === "user" ? "items-end" : "items-start"}`}>
                      <div className={`mb-0.5 px-12 ${msg.role === "user" ? "text-right" : "text-left"}`}><MsgTime iso={msg.ts} /></div>
                      <div className={`flex gap-3 w-full ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
                        {msg.role === "assistant" && (
                          <div className="mt-0.5 flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-2xl bg-primary/10">
                            <Bot className="h-4 w-4 text-primary" />
                          </div>
                        )}
                        <div className="max-w-[85%] min-w-0">
                          <div className={`rounded-[22px] px-4 py-3 text-sm break-words shadow-sm overflow-x-auto ${
                            msg.role === "user"
                              ? "bg-primary text-primary-foreground"
                              : "border border-border/60 bg-background/90 prose prose-sm max-w-none dark:prose-invert"
                          }`}>
                            {msg.role === "user"
                              ? <>
                                  {(msg as any)._images && (msg as any)._images.length > 0 && (
                                    <div className="flex gap-1 mb-1 flex-wrap">
                                      {(msg as any)._images.map((src: string, i: number) => (
                                        <img key={i} src={src} alt="" className="h-20 rounded-md" />
                                      ))}
                                    </div>
                                  )}
                                  <span className="whitespace-pre-wrap">{msg.content}</span>
                                </>
                              : streamingMsgId === msg.id && !msg.content
                                ? <div className="flex h-5 items-center gap-1">
                                    <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground animate-bounce [animation-delay:0ms]" />
                                    <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground animate-bounce [animation-delay:150ms]" />
                                    <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground animate-bounce [animation-delay:300ms]" />
                                  </div>
                                : <>
                                    <ReactMarkdown components={{ img: ({ src, alt }) => src?.startsWith("data:image") ? (
                                      <img src={src} alt={alt || ""} className="rounded-md max-h-[400px] w-auto cursor-pointer hover:opacity-80 transition-opacity my-2" onClick={() => setLightboxSrc(src)} />
                                    ) : <img src={src} alt={alt || ""} /> }}>{msg.content}</ReactMarkdown>
                                    {streamingMsgId === msg.id
                                      ? <span className="ml-0.5 inline-block h-4 w-2 animate-pulse rounded-sm bg-primary/70 align-text-bottom" />
                                      : doneMsgId === msg.id && <span className="ml-1 inline-block align-text-bottom text-xs text-green-500">✓</span>
                                    }
                                  </>
                            }
                          </div>
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
                        {msg.role === "user" && (
                          <div className="mt-0.5 flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-2xl bg-secondary">
                            <User className="h-4 w-4" />
                          </div>
                        )}
                      </div>
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

                <div className="border-t border-border/60 bg-card/95 backdrop-blur px-4 py-4 sm:px-5 rounded-b-[28px] flex-shrink-0 min-w-0">
                  <div className="text-right mb-1"><span className="text-[10px] text-muted-foreground/40">{new Date().toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })}</span></div>
                  <div className="relative min-w-0">
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

                    <div className="rounded-[24px] border border-border/70 bg-background/90 p-3 shadow-sm min-w-0">
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
                      {/* Coach Feedback */}
                      {coachFeedback && !coachFeedback.ok && (
                        <div className="rounded-xl border border-orange-500/30 bg-orange-500/5 px-3 py-2 sm:px-4 sm:py-3 mb-2 text-sm space-y-2">
                          <p className="text-orange-400 font-medium text-xs sm:text-sm">{coachFeedback.reason || "Dein Prompt könnte klarer sein"}</p>
                          {coachFeedback.suggestion && (
                            <p className="text-muted-foreground text-xs bg-muted/30 rounded-lg p-2 font-mono">{coachFeedback.suggestion}</p>
                          )}
                          <div className="flex flex-wrap gap-2">
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
                            <a href="/prompt-guide" className="hidden sm:flex rounded-lg px-3 py-1.5 text-xs text-primary hover:underline items-center gap-1">
                              <Lightbulb className="h-3 w-3" /> Prompt-Tipps
                            </a>
                          </div>
                        </div>
                      )}
                      {/* Image previews */}
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
                      {/* Toolbar: Coach + Emoji */}
                      <div className="flex items-center gap-1 sm:gap-2 mb-1 flex-wrap">
                        <label className="flex items-center gap-1 text-xs text-muted-foreground cursor-pointer select-none">
                          <input type="checkbox" checked={coachEnabled} onChange={e => {
                            setCoachEnabled(e.target.checked);
                            localStorage.setItem("hh_prompt_coach", e.target.checked ? "1" : "0");
                          }} className="rounded" />
                          <span className="hidden sm:inline">Prompt-Coach</span>
                          <span className="sm:hidden">🧠</span>
                          {coachChecking && <RefreshCw className="h-3 w-3 animate-spin" />}
                        </label>
                        <button onClick={() => setShowEmoji(v => !v)} type="button"
                          className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-xs text-muted-foreground hover:bg-muted transition">
                          <Smile className="h-3.5 w-3.5" /><span className="hidden sm:inline">Emoji</span>
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
                          className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-xs text-muted-foreground hover:bg-muted transition"
                          aria-label="Bild hochladen">
                          <ImagePlus className={`h-3.5 w-3.5 ${pendingImages.length > 0 ? "text-primary" : ""}`} /><span className="hidden sm:inline">Bild</span>
                        </button>
                      </div>
                      {/* Input + Send */}
                      <div className="flex items-end gap-1.5 sm:gap-2 min-w-0">
                        <textarea ref={textareaRef} value={input}
                          onChange={(e) => setInput(e.target.value)} onKeyDown={onKeyDown}
                          onBlur={() => setTimeout(() => setShowSuggest(false), 150)}
                          placeholder={viewSession ? "Vergangene Session — schreibgeschützt" : t("myAgent.messagePlaceholder")} rows={1}
                          disabled={!!viewSession}
                          className="min-h-[2.5rem] sm:min-h-[3rem] min-w-0 flex-1 resize-none rounded-xl sm:rounded-2xl border border-border/60 bg-card px-3 py-2 sm:px-4 sm:py-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50 disabled:cursor-not-allowed"
                          style={{ maxHeight: "160px", overflowY: "auto" }} />
                        {sending ? (
                          <button onClick={stop}
                            className="inline-flex h-10 w-10 sm:h-12 sm:w-12 shrink-0 items-center justify-center rounded-xl sm:rounded-2xl bg-destructive text-destructive-foreground transition hover:bg-destructive/90"
                            title={`Abbrechen${elapsed > 0 ? ` (${elapsed}s)` : ""}`}
                            aria-label="Stop generation">
                            <Square className="h-4 w-4" />
                          </button>
                        ) : (
                          <button onClick={() => send()} disabled={!input.trim() || coachChecking}
                            className="inline-flex h-10 w-10 sm:h-12 sm:w-12 shrink-0 items-center justify-center rounded-xl sm:rounded-2xl bg-primary text-primary-foreground transition hover:bg-primary/90 disabled:opacity-50"
                            aria-label="Send message">
                            <Send className="h-4 w-4" />
                          </button>
                        )}
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </section>

            <aside className="xl:sticky xl:top-24 xl:self-start pr-4 sm:pr-6">
              <div className="rounded-[28px] border border-border/60 bg-card/80 p-4 shadow-[0_20px_80px_rgba(15,23,42,0.08)] backdrop-blur space-y-3">
                {/* Agent-Name + Status */}
                <div className="flex items-center justify-between gap-2">
                  <div>
                    <div className="text-sm font-semibold">{identity}</div>
                    <div className="text-xs text-muted-foreground font-mono">{agentInfo?.config?.llm?.model ?? t("myAgent.noModel")}</div>
                  </div>
                  <div className="flex items-center gap-1.5">
                    {sending ? <RefreshCw className="h-4 w-4 animate-spin text-primary" /> : <CheckCircle className="h-4 w-4 text-emerald-500" />}
                  </div>
                </div>

                {/* Kompakt-Stats */}
                <div className="grid grid-cols-2 gap-2 text-xs">
                  <div className="rounded-xl border border-border/70 bg-background/70 px-3 py-2">
                    <div className="text-muted-foreground">{t("myAgent.mode")}</div>
                    <div className="font-medium mt-0.5">{exec.defaultMode}</div>
                  </div>
                  <div className="rounded-xl border border-border/70 bg-background/70 px-3 py-2">
                    <div className="text-muted-foreground">{t("myAgent.historyCount")}</div>
                    <div className="font-medium mt-0.5">{messages.filter(m => m.role !== "system").length}</div>
                  </div>
                  <div className="rounded-xl border border-border/70 bg-background/70 px-3 py-2">
                    <div className="text-muted-foreground">safe</div>
                    <div className="font-medium mt-0.5">{exec.counts.safe} Tools</div>
                  </div>
                  <div className="rounded-xl border border-border/70 bg-background/70 px-3 py-2">
                    <div className="text-muted-foreground">elevated</div>
                    <div className="font-medium mt-0.5">{exec.counts.elevated} Tools</div>
                  </div>
                </div>

                {/* Slash-Commands */}
                <div className="rounded-xl border border-border/70 bg-background/70 px-3 py-2 space-y-1.5">
                  {SLASH_COMMANDS.map((cmd) => (
                    <div key={cmd.cmd} className="flex items-center justify-between gap-2 text-xs">
                      <code className="rounded bg-muted px-1.5 py-0.5 text-primary flex-shrink-0">{cmd.cmd}</code>
                      <span className="text-right text-muted-foreground truncate">{cmd.desc}</span>
                    </div>
                  ))}
                </div>

                {activeTool && (
                  <div className="rounded-xl border border-primary/20 bg-primary/5 px-3 py-2 text-xs">
                    <div className="flex items-center gap-1.5 text-primary">
                      <RefreshCw className="h-3 w-3 animate-spin" />
                      <code className="font-mono truncate">{activeTool.name}</code>
                    </div>
                  </div>
                )}
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

      {/* ── Messenger Tab ─────────────────────────────────────────────────── */}
      {tab === "messenger" && (
        <div className="flex-1 overflow-y-auto p-6 space-y-4">
          <h2 className="text-lg font-semibold">Messenger</h2>
          {/* #233: Messenger nur anzeigen wenn Feature installiert/konfiguriert */}
          {(!capabilities["whatsapp-bridge"] || capabilities["whatsapp-bridge"].installed) && (
            <MessengerSection title="WhatsApp" icon={Phone}>
              <WhatsAppTab />
            </MessengerSection>
          )}
          {(!capabilities["discord"] || capabilities["discord"].configured) && (
            <MessengerSection title="Discord" icon={MessageSquare}>
              <DiscordTab />
            </MessengerSection>
          )}
          <MessengerSection title="Telegram" icon={Send}>
            <TelegramTab />
          </MessengerSection>
          {(!capabilities["kas"] || capabilities["kas"].configured) && (
            <MessengerSection title="Mail" icon={Mail}>
              <MailTab />
            </MessengerSection>
          )}
        </div>
      )}

      {/* ── WKS Tab ───────────────────────────────────────────────────────── */}
      {tab === "wks" && (
        <WksTab />
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

      {/* ── Butler Tab ────────────────────────────────────────────────────── */}
      {tab === "butler" && (
        <Suspense fallback={<div className="p-8 text-muted-foreground text-sm">Lade Butler...</div>}>
          <div className="flex flex-col h-screen">
            <div className="p-6 border-b bg-background">
              <div className="mb-0 rounded-xl border bg-muted/30 p-4 space-y-2 max-w-2xl">
                <h3 className="text-sm font-semibold">{t("butler.infoTitle")}</h3>
                <p className="text-xs text-muted-foreground leading-relaxed">{t("butler.infoText")}</p>
              </div>
            </div>
            <div style={{ height: "calc(100vh - 200px)", flex: 1, overflow: "hidden" }}>
              <ButlerEmbed />
            </div>
          </div>
        </Suspense>
      )}

      {tab === "account" && <AccountTab />}

      {/* Dynamische User-App Tabs */}
      {tab.startsWith("app-") && (
        <UserAppTab appId={tab.replace("app-", "")} apps={userApps} />
      )}
      {lightboxSrc && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 cursor-pointer" onClick={() => setLightboxSrc(null)}>
          <img src={lightboxSrc} alt="Fullscreen" className="max-w-[90vw] max-h-[90vh] rounded-lg shadow-2xl" />
        </div>
      )}
    </div>
  );
}

/* ── User App Tab (dynamisch) ────────────────────────────────── */

function UserAppTab({ appId, apps }: { appId: string; apps: any[] }) {
  const app = apps.find(a => a.id === appId);
  const [config, setConfig] = useState<Record<string, unknown>>({});
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");

  useEffect(() => {
    if (app?.config) setConfig(app.config);
  }, [app]);

  if (!app) return <div className="p-8 text-muted-foreground text-sm">App nicht gefunden.</div>;

  async function save() {
    setSaving(true); setMsg("");
    try {
      await api.put(`/me/user-apps/${appId}/config`, config);
      setMsg("Gespeichert");
      setTimeout(() => setMsg(""), 3000);
    } catch (e) { setMsg(e instanceof Error ? e.message : "Fehler"); }
    finally { setSaving(false); }
  }

  return (
    <div className="p-5 max-w-2xl space-y-5">
      <div>
        <h2 className="text-lg font-semibold flex items-center gap-2">
          <Puzzle className="h-5 w-5 text-primary" /> {app.name}
        </h2>
        {app.description && <p className="text-xs text-muted-foreground mt-1">{app.description}</p>}
        <span className="text-xs text-muted-foreground">v{app.version}</span>
      </div>

      {/* Dexcom: Live-Werte-Anzeige */}
      {appId === "dexcom-monitor" && !!config.dexcom_username && !!config.dexcom_password && (
        <DexcomLivePanel />
      )}

      {app.config_fields?.length > 0 ? (
        <div className="space-y-3">
          {app.config_fields.map((field: any) => (
            <div key={field.id} className="space-y-1">
              <label className="text-sm font-medium">{field.label}</label>
              {field.type === "password" ? (
                <input type="password" value={String(config[field.id] ?? field.default ?? "")}
                  onChange={e => setConfig({ ...config, [field.id]: e.target.value })}
                  className="w-full rounded-lg border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring font-mono" />
              ) : field.type === "number" ? (
                <input type="number" value={String(config[field.id] ?? field.default ?? "")}
                  onChange={e => setConfig({ ...config, [field.id]: Number(e.target.value) })}
                  className="w-full rounded-lg border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring" />
              ) : field.type === "toggle" ? (
                <label className="flex items-center gap-2 cursor-pointer">
                  <input type="checkbox" checked={!!config[field.id]}
                    onChange={e => setConfig({ ...config, [field.id]: e.target.checked })}
                    className="rounded" />
                  <span className="text-xs text-muted-foreground">{field.hint || ""}</span>
                </label>
              ) : (
                <input value={String(config[field.id] ?? field.default ?? "")}
                  onChange={e => setConfig({ ...config, [field.id]: e.target.value })}
                  placeholder={field.placeholder || ""}
                  className="w-full rounded-lg border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring" />
              )}
              {field.hint && field.type !== "toggle" && <p className="text-xs text-muted-foreground">{field.hint}</p>}
            </div>
          ))}
          <div className="flex items-center gap-3 pt-2">
            <button onClick={save} disabled={saving}
              className="flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-40">
              <Save className="h-4 w-4" />{saving ? "Speichern..." : "Speichern"}
            </button>
            {msg && <span className="text-sm text-green-500">{msg}</span>}
          </div>
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">Diese App hat keine Konfigurationsoptionen.</p>
      )}
    </div>
  );
}

/* ── Dexcom Live-Panel ───────────────────────────────────────── */

interface GlucoseReading {
  value: number;
  trend_arrow: string;
  timestamp: number;
}

function DexcomLivePanel() {
  const { t } = useTranslation();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [data, setData] = useState<{
    current: { value: number; unit: string; trend: string; status: string };
    readings: GlucoseReading[];
    alert_thresholds: { low: number; high: number };
  } | null>(null);

  async function fetchGlucose() {
    setLoading(true);
    setError("");
    try {
      const result = await api.get<{
        current: { value: number; unit: string; trend: string; status: string } | null;
        readings: GlucoseReading[];
        alert_thresholds: { low: number; high: number };
      }>("/me/user-apps/dexcom-monitor/glucose?minutes=60&count=12");
      if (result.current) {
        setData({ ...result, current: result.current });
      } else {
        setError("Keine Glukosewerte verfügbar — Sensor aktiv?");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Fehler beim Abrufen");
    } finally {
      setLoading(false);
    }
  }

  function glucoseColor(value: number, low: number, high: number): string {
    if (value < low) return "text-red-500";
    if (value > high) return "text-amber-500";
    return "text-green-500";
  }

  function timeAgo(ts: number): string {
    if (!ts) return "?";
    const diff = Math.floor(Date.now() / 1000 - ts);
    if (diff < 60) return "gerade eben";
    if (diff < 3600) return `vor ${Math.floor(diff / 60)} Min`;
    return `vor ${Math.floor(diff / 3600)} Std`;
  }

  return (
    <div className="rounded-xl border bg-card p-4 space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold flex items-center gap-2">
          {t("dexcom.liveTitle", { defaultValue: "Aktuelle Glukosewerte" })}
        </h3>
        <button
          onClick={fetchGlucose}
          disabled={loading}
          className="flex items-center gap-1.5 rounded-lg bg-primary/10 px-3 py-1.5 text-xs font-medium text-primary hover:bg-primary/20 disabled:opacity-40"
        >
          <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
          {loading ? t("dexcom.loading", { defaultValue: "Lade..." }) : t("dexcom.refresh", { defaultValue: "Werte abrufen" })}
        </button>
      </div>

      {error && (
        <div className="text-sm text-red-500 bg-red-500/10 rounded-lg p-3">{error}</div>
      )}

      {data && (
        <>
          {/* Aktueller Wert — groß */}
          <div className="flex items-center gap-4">
            <div className={cn("text-5xl font-bold tabular-nums", glucoseColor(data.current.value, data.alert_thresholds.low, data.alert_thresholds.high))}>
              {data.current.value}
            </div>
            <div className="space-y-1">
              <div className="text-2xl">{data.current.trend}</div>
              <div className="text-xs text-muted-foreground">{data.current.unit}</div>
              <div className={cn(
                "text-xs font-semibold px-2 py-0.5 rounded-full inline-block",
                data.current.status === "normal"
                  ? "bg-green-500/10 text-green-500"
                  : "bg-red-500/10 text-red-500"
              )}>
                {data.current.status === "normal" ? "Im Zielbereich" : data.current.status}
              </div>
            </div>
          </div>

          {/* Letzte Messungen */}
          {data.readings.length > 1 && (
            <div className="space-y-1">
              <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
                {t("dexcom.history", { defaultValue: "Verlauf" })}
              </h4>
              <div className="flex gap-2 flex-wrap">
                {data.readings.map((r, i) => (
                  <div key={i} className={cn(
                    "rounded-lg border px-3 py-2 text-center min-w-[4.5rem]",
                    i === 0 ? "border-primary bg-primary/5" : "bg-muted/30"
                  )}>
                    <div className={cn("text-lg font-bold tabular-nums", glucoseColor(r.value, data.alert_thresholds.low, data.alert_thresholds.high))}>
                      {r.value}
                    </div>
                    <div className="text-sm">{r.trend_arrow}</div>
                    <div className="text-[10px] text-muted-foreground">{timeAgo(r.timestamp)}</div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Schwellwerte */}
          <div className="flex gap-4 text-xs text-muted-foreground">
            <span>Hypo: &lt;{data.alert_thresholds.low} mg/dL</span>
            <span>Hyper: &gt;{data.alert_thresholds.high} mg/dL</span>
          </div>
        </>
      )}

      {!data && !error && !loading && (
        <p className="text-sm text-muted-foreground">
          {t("dexcom.clickToLoad", { defaultValue: "Klicke auf 'Werte abrufen' um die aktuellen Glukosewerte von Dexcom zu laden." })}
        </p>
      )}
    </div>
  );
}

/* ── Account Tab — Mein Konto ────────────────────────────────── */

function AccountTab() {
  const { t } = useTranslation();
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [copied, setCopied] = useState("");

  useEffect(() => {
    api.get<any>("/me/credentials").then(setData).catch(e => console.error("Failed to load credentials", e)).finally(() => setLoading(false));
  }, []);

  function copy(text: string, label: string) {
    navigator.clipboard.writeText(text);
    setCopied(label);
    setTimeout(() => setCopied(""), 2000);
  }

  if (loading) return <div className="p-8 text-sm text-muted-foreground">Lade...</div>;
  if (!data) return <div className="p-8 text-sm text-destructive">Fehler beim Laden der Kontodaten.</div>;

  return (
    <div className="p-5 space-y-5 max-w-2xl">
      <div className="mb-6 rounded-xl border bg-muted/30 p-4 space-y-2">
        <h3 className="text-sm font-semibold">{t("account.infoTitle")}</h3>
        <p className="text-xs text-muted-foreground leading-relaxed">{t("account.infoText")}</p>
      </div>

      <div>
        <h2 className="text-sm font-semibold mb-3 flex items-center gap-2"><User className="h-4 w-4 text-primary" /> Benutzerkonto</h2>
        <div className="grid gap-2 text-sm">
          <InfoRow label="Benutzername" value={data.username} onCopy={copy} copied={copied} />
          <InfoRow label="Rolle" value={data.role} />
          <InfoRow label="Gruppe" value={data.group} />
          <InfoRow label="Console" value={data.console_url} onCopy={copy} copied={copied} />
        </div>
      </div>

      {data.tailscale_ip && (
        <div>
          <h2 className="text-sm font-semibold mb-3 flex items-center gap-2"><Wifi className="h-4 w-4 text-primary" /> Tailscale VPN</h2>
          <div className="grid gap-2 text-sm">
            <InfoRow label="Tailscale IP" value={data.tailscale_ip} onCopy={copy} copied={copied} />
          </div>
        </div>
      )}

      {data.samba?.shares?.length > 0 && (
        <div>
          <h2 className="text-sm font-semibold mb-3 flex items-center gap-2"><KeyRound className="h-4 w-4 text-primary" /> Samba-Zugang (Dateifreigaben)</h2>
          <p className="text-xs text-muted-foreground mb-3">Verbinde dich mit dem Dateimanager: <code className="bg-muted px-1 rounded">{data.samba.hint}</code></p>
          <div className="space-y-2">
            {data.samba.shares.map((s: any) => (
              <div key={s.project} className="rounded-lg border bg-muted/30 p-3">
                <p className="text-xs font-medium mb-1">{s.project}</p>
                <div className="grid gap-1.5">
                  <InfoRow label="Benutzer" value={s.username} onCopy={copy} copied={copied} small />
                  <InfoRow label="Passwort" value={s.password} onCopy={copy} copied={copied} small secret />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {data.wks?.ip && (
        <div>
          <h2 className="text-sm font-semibold mb-3 flex items-center gap-2"><Monitor className="h-4 w-4 text-primary" /> Workstation (WKS)</h2>
          <div className="grid gap-2 text-sm">
            <InfoRow label="IP" value={data.wks.ip} onCopy={copy} copied={copied} />
            <InfoRow label="SSH-User" value={data.wks.ssh_user || "—"} />
            <InfoRow label="Ollama-Port" value={String(data.wks.ollama_port || 11434)} />
          </div>
        </div>
      )}
    </div>
  );
}

function InfoRow({ label, value, onCopy, copied, small, secret }: {
  label: string; value: string; onCopy?: (v: string, l: string) => void;
  copied?: string; small?: boolean; secret?: boolean;
}) {
  const [show, setShow] = useState(false);
  return (
    <div className={`flex items-center justify-between gap-2 ${small ? "text-xs" : "text-sm"}`}>
      <span className="text-muted-foreground shrink-0">{label}</span>
      <div className="flex items-center gap-1.5 min-w-0">
        <span className="font-mono truncate">{secret && !show ? "••••••••" : value}</span>
        {secret && (
          <button onClick={() => setShow(!show)} className="text-xs text-muted-foreground hover:text-foreground">
            {show ? "Verbergen" : "Anzeigen"}
          </button>
        )}
        {onCopy && (
          <button onClick={() => onCopy(value, label)} className="text-muted-foreground hover:text-foreground shrink-0">
            {copied === label ? <CheckCircle className="h-3.5 w-3.5 text-green-500" /> : <Copy className="h-3.5 w-3.5" />}
          </button>
        )}
      </div>
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
    } catch(e) { setMsg(e instanceof Error ? e.message : t("common.error")); }
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
        setError(e instanceof Error ? e.message : t("common.error"));
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
      .catch(e => console.error("Failed to load available models", e));
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
      const pendingFb = fbInput.trim();
      const allFallbacks = pendingFb && !fallbacks.includes(pendingFb)
        ? [...fallbacks, pendingFb]
        : fallbacks;
      if (pendingFb && !fallbacks.includes(pendingFb)) {
        setFallbacks(allFallbacks);
        setFbInput("");
      }
      await api.put("/me/agent", {
        identity, soul, model, temperature, max_tokens: maxTokens,
        fallback_models: allFallbacks, tools, allowed_agents: allowedAgents,
        ollama_base_url,
      });
      setSaveMsg(t("myAgent.settingsSaved"));
      onSaved();
      setTimeout(() => setSaveMsg(""), 3000);
    } catch(e) { setSaveMsg(e instanceof Error ? e.message : t("common.error")); }
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
                {/* API-Modelle + Fallback-Modelle (dedupliziert) */}
                {(() => {
                  const apiIds = new Set(availableModels.map(m => m.id));
                  const all = [
                    ...availableModels.map(m => ({ id: m.id, label: m.label })),
                    ...KNOWN_MODELS.filter(m => !apiIds.has(m)).map(m => ({ id: m, label: m })),
                  ];
                  return all.map(m => <option key={m.id} value={m.id}>{m.label}</option>);
                })()}
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
          <div className="flex items-center gap-3">
            <h2 className="text-sm font-semibold text-foreground">{t("myAgent.settingsSectionTools")}</h2>
            <button type="button" onClick={() => setTools(ALL_TOOLS.filter(t => !["project_shell","create_agent","delete_agent","create_project","delete_project"].includes(t.id)).map(t => t.id))}
              className="text-xs text-muted-foreground hover:text-foreground transition">
              Alle außer ⚠
            </button>
            <button type="button" onClick={() => setTools([])}
              className="text-xs text-muted-foreground hover:text-foreground transition">
              Keine
            </button>
          </div>
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
            <div className="flex items-center gap-3">
              <h2 className="text-sm font-semibold text-foreground">{t("myAgent.settingsSectionDelegation")}</h2>
              <button type="button" onClick={() => setAllowedAgents([...agents])}
                className="text-xs text-muted-foreground hover:text-foreground transition">
                Alle
              </button>
              <button type="button" onClick={() => setAllowedAgents([])}
                className="text-xs text-muted-foreground hover:text-foreground transition">
                Keine
              </button>
            </div>
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

        {/* Backup & Import */}
        <AgentBackupSection />

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

// ── Agent Backup / Import ─────────────────────────────────────────────────────

function AgentBackupSection() {
  const { t } = useTranslation();
  const [importing,  setImporting]  = useState(false);
  const [importMsg,  setImportMsg]  = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  async function handleExport() {
    const token = localStorage.getItem("hydrahive_token") ?? "";
    const res = await fetch("/api/me/agent/export", {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) { alert(t("common.error")); return; }
    const blob = await res.blob();
    const cd = res.headers.get("Content-Disposition") ?? "";
    const match = cd.match(/filename="([^"]+)"/);
    const filename = match ? match[1] : "agent_backup.tar.gz";
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = filename; a.click();
    URL.revokeObjectURL(url);
  }

  async function handleImport(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setImporting(true); setImportMsg("");
    try {
      const token = localStorage.getItem("hydrahive_token") ?? "";
      const fd = new FormData();
      fd.append("file", file);
      const res = await fetch("/api/me/agent/import", {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        body: fd,
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail ?? t("common.error"));
      setImportMsg(`✓ ${data.files} Dateien importiert`);
      setTimeout(() => setImportMsg(""), 4000);
    } catch(err) {
      setImportMsg(err instanceof Error ? err.message : t("common.error"));
    } finally {
      setImporting(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  return (
    <section className="space-y-3 border-t pt-6">
      <h2 className="text-sm font-semibold text-foreground flex items-center gap-2">
        <Download className="h-4 w-4" />
        Agent sichern &amp; übertragen
      </h2>
      <p className="text-xs text-muted-foreground">
        Backup als .tar.gz herunterladen oder auf einem anderen HydraHive-Server importieren.
        Enthält Konfiguration, Memory und Skills.
      </p>
      <div className="flex flex-wrap gap-2 items-center">
        <button
          type="button"
          onClick={handleExport}
          className="flex items-center gap-2 px-3 py-2 text-sm border rounded-md hover:bg-accent transition-colors"
        >
          <Download className="h-3.5 w-3.5" />
          Agent sichern
        </button>
        <button
          type="button"
          onClick={() => fileRef.current?.click()}
          disabled={importing}
          className="flex items-center gap-2 px-3 py-2 text-sm border rounded-md hover:bg-accent transition-colors disabled:opacity-50"
        >
          <Upload className="h-3.5 w-3.5" />
          {importing ? "Importiere…" : "Agent importieren"}
        </button>
        <input
          ref={fileRef}
          type="file"
          accept=".tar.gz,.tgz"
          className="hidden"
          onChange={handleImport}
        />
        {importMsg && (
          <span className={`text-xs ${importMsg.startsWith("✓") ? "text-green-600" : "text-destructive"}`}>
            {importMsg}
          </span>
        )}
      </div>
    </section>
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
        api.getWksPubkey().then(r => setPubKey(r.public_key)).catch(e => console.error("Failed to load WKS public key", e));
      }
    }).catch(e => console.error("Failed to load WKS config", e));
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
    } catch(e) { setMsg(e instanceof Error ? e.message : t("common.error")); }
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
    } catch(e) { setMsg(e instanceof Error ? e.message : t("common.error")); }
    finally { setGenerating(false); }
  }

  async function testSsh() {
    setSshTesting(true); setSshTestMsg("");
    try {
      const r = await api.testWksSsh();
      if (r.ok) setSshTestMsg(`✓ Verbunden — ${r.hostname} (${r.user})`);
      else setSshTestMsg(`✗ ${r.error}`);
    } catch(e) { setSshTestMsg(e instanceof Error ? e.message : t("common.error")); }
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
    } catch(e) { setTestMsg(e instanceof Error ? e.message : t("common.error")); }
    finally { setTesting(false); }
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <form onSubmit={save} className="p-6 space-y-8 max-w-2xl">
        <div className="mb-6 rounded-xl border bg-muted/30 p-4 space-y-2">
          <h3 className="text-sm font-semibold">{t("wks.infoTitle")}</h3>
          <p className="text-xs text-muted-foreground leading-relaxed">{t("wks.infoText")}</p>
        </div>

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
  const [channelModes,   setChannelModes]   = useState<Record<string,string>>({});
  const [channelNames,   setChannelNames]   = useState<Record<string,string>>({});
  const [roles,          setRoles]          = useState<{id:string;name:string;color:string}[]>([]);
  const [loadingRoles,   setLoadingRoles]   = useState(false);
  const [roleWhitelist,  setRoleWhitelist]  = useState<Set<string>>(new Set());
  const [roleBlacklist,  setRoleBlacklist]  = useState<Set<string>>(new Set());
  const [userWhitelist,  setUserWhitelist]  = useState<string[]>([]);
  const [userBlacklist,  setUserBlacklist]  = useState<string[]>([]);
  const [userWlInput,    setUserWlInput]    = useState("");
  const [userBlInput,    setUserBlInput]    = useState("");
  const [confirmState, setConfirmState] = useState<{action: () => void; title: string; message: string} | null>(null);

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
      setChannelModes(d.channel_modes ?? {});
      setChannelNames(d.channel_names ?? {});
      setRoleWhitelist(new Set(d.role_whitelist ?? []));
      setRoleBlacklist(new Set(d.role_blacklist ?? []));
      setUserWhitelist(d.user_whitelist ?? []);
      setUserBlacklist(d.user_blacklist ?? []);
    }).catch(e => console.error("Failed to load Discord config", e));
  }, []);

  async function loadChannels() {
    setLoadingCh(true); setMsg("");
    try {
      const res = await api.getDiscordChannels();
      setChannels(res.channels ?? []);
      const nameMap: Record<string,string> = {};
      (res.channels ?? []).forEach((ch: {id:string;name:string}) => { nameMap[ch.id] = ch.name; });
      setChannelNames(prev => ({...prev, ...nameMap}));
      if ((res.channels ?? []).length === 0) setMsg("Keine Text-Channels gefunden");
    } catch (err: unknown) {
      setMsg(t("common.error") + ": " + (err instanceof Error ? err.message : String(err)));
    } finally { setLoadingCh(false); }
  }

  async function loadRoles() {
    setLoadingRoles(true); setMsg("");
    try {
      const res = await api.getDiscordRoles();
      setRoles(res.roles ?? []);
      if ((res.roles ?? []).length === 0) setMsg("Keine Rollen gefunden");
    } catch (err: unknown) {
      setMsg(t("common.error") + ": " + (err instanceof Error ? err.message : String(err)));
    } finally { setLoadingRoles(false); }
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
        channel_modes: channelModes,
        channel_names: channelNames,
        role_whitelist: [...roleWhitelist],
        role_blacklist: [...roleBlacklist],
        user_whitelist: userWhitelist,
        user_blacklist: userBlacklist,
      });
      setMsg(`✓ Bot "${res.bot_name}" verbunden`);
      setBotToken(""); setChangeToken(false);
      const updated = await api.getDiscord();
      setCfg(updated);
      setSelectedIds(new Set(updated.channel_ids ?? []));
      setChannelModes(updated.channel_modes ?? {});
      setChannelNames(updated.channel_names ?? {});
      setRoleWhitelist(new Set(updated.role_whitelist ?? []));
      setRoleBlacklist(new Set(updated.role_blacklist ?? []));
      setUserWhitelist(updated.user_whitelist ?? []);
      setUserBlacklist(updated.user_blacklist ?? []);
    } catch (err: unknown) {
      setMsg(t("common.error") + ": " + (err instanceof Error ? err.message : String(err)));
    } finally { setSaving(false); }
  }

  function handleDelete() {
    setConfirmState({
      title: t("confirm.titleDelete"),
      message: t("myAgent.discordDeleteConfirm"),
      action: async () => {
        await api.deleteDiscord();
        setCfg({ configured: false });
        setGuildId(""); setSelectedIds(new Set()); setChannels([]);
        setChannelModes({}); setChannelNames({}); setRoles([]);
        setRoleWhitelist(new Set()); setRoleBlacklist(new Set());
        setUserWhitelist([]); setUserBlacklist([]);
        setMsg("Bot entfernt");
      },
    });
  }

  async function handleTest() {
    setTesting(true); setMsg("");
    try {
      const res = await api.testDiscord();
      setMsg(res.ok ? `✓ Bot "${res.bot_name}" erreichbar` : `Fehler: ${res.error}`);
    } catch (err: unknown) {
      setMsg(t("common.error") + ": " + (err instanceof Error ? err.message : String(err)));
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
                  <span className="font-medium">#{channelNames[ch.id] ?? ch.name ?? ch.id}</span>
                  {selectedIds.has(ch.id) && (
                    <select value={channelModes[ch.id] ?? "rw"}
                      onChange={e => setChannelModes(prev => ({...prev, [ch.id]: e.target.value}))}
                      onClick={e => e.stopPropagation()}
                      className="ml-auto text-xs border rounded px-1 py-0.5 bg-background">
                      <option value="rw">Antworten</option>
                      <option value="ro">Nur lesen</option>
                    </select>
                  )}
                  <span className="text-muted-foreground font-mono text-xs ml-auto">{ch.id}</span>
                </label>
              ))}
            </div>
          ) : selectedIds.size > 0 ? (
            <div className="border rounded-md divide-y max-h-48 overflow-y-auto">
              {[...selectedIds].map(id => (
                <div key={id} className="flex items-center gap-2 px-3 py-2 text-xs border-b last:border-0">
                  <span className="font-medium">#{channelNames[id] ?? id}</span>
                  <select value={channelModes[id] ?? "rw"}
                    onChange={e => setChannelModes(prev => ({...prev, [id]: e.target.value}))}
                    className="ml-auto text-xs border rounded px-1 py-0.5 bg-background">
                    <option value="rw">Antworten</option>
                    <option value="ro">Nur lesen</option>
                  </select>
                  <button type="button" onClick={() => toggleChannel(id)} className="text-muted-foreground hover:text-destructive">×</button>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">
              {t("myAgent.discordNoChannels")}
            </p>
          )}
        </div>

        {/* Rollen-Filter */}
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <label className="text-xs font-medium">{t("myAgent.discordRoles")}</label>
            <button type="button" onClick={loadRoles} disabled={loadingRoles || !cfg?.configured}
              className="flex items-center gap-1 text-xs text-primary hover:underline disabled:opacity-40">
              {loadingRoles ? <RefreshCw className="h-3 w-3 animate-spin" /> : <RefreshCw className="h-3 w-3" />}
              {t("myAgent.discordLoadRoles")}
            </button>
          </div>
          {roles.length > 0 && (
            <div className="border rounded-md divide-y max-h-40 overflow-y-auto">
              {roles.map(r => (
                <div key={r.id} className="flex items-center gap-2 px-3 py-1.5 text-xs">
                  <span className="flex-1 font-medium">@{r.name}</span>
                  <label className="flex items-center gap-1 text-green-600">
                    <input type="checkbox" checked={roleWhitelist.has(r.id)}
                      onChange={() => setRoleWhitelist(prev => { const n = new Set(prev); n.has(r.id) ? n.delete(r.id) : n.add(r.id); return n; })} />
                    WL
                  </label>
                  <label className="flex items-center gap-1 text-destructive">
                    <input type="checkbox" checked={roleBlacklist.has(r.id)}
                      onChange={() => setRoleBlacklist(prev => { const n = new Set(prev); n.has(r.id) ? n.delete(r.id) : n.add(r.id); return n; })} />
                    BL
                  </label>
                </div>
              ))}
            </div>
          )}
          <p className="text-xs text-muted-foreground">{t("myAgent.discordRolesHint")}</p>
        </div>

        {/* User-Filter */}
        <div className="space-y-2">
          <label className="text-xs font-medium block">{t("myAgent.discordUserFilter")}</label>
          <div className="grid grid-cols-2 gap-3">
            {/* Whitelist */}
            <div>
              <p className="text-xs text-muted-foreground mb-1">{t("myAgent.discordUserWL")}</p>
              <div className="flex gap-1">
                <input value={userWlInput} onChange={e => setUserWlInput(e.target.value)}
                  onKeyDown={e => { if (e.key === "Enter" && userWlInput.trim()) { setUserWhitelist(p => [...p, userWlInput.trim()]); setUserWlInput(""); }}}
                  placeholder="User-ID" className="text-xs border rounded px-2 py-1 flex-1 bg-background" />
                <button type="button" onClick={() => { if (userWlInput.trim()) { setUserWhitelist(p => [...p, userWlInput.trim()]); setUserWlInput(""); }}}
                  className="text-xs px-2 py-1 border rounded hover:bg-accent">+</button>
              </div>
              <div className="mt-1 space-y-0.5">
                {userWhitelist.map(id => (
                  <div key={id} className="flex items-center gap-1 text-xs">
                    <span className="font-mono flex-1">{id}</span>
                    <button type="button" onClick={() => setUserWhitelist(p => p.filter(x => x !== id))} className="text-muted-foreground hover:text-destructive">×</button>
                  </div>
                ))}
              </div>
            </div>
            {/* Blacklist */}
            <div>
              <p className="text-xs text-muted-foreground mb-1">{t("myAgent.discordUserBL")}</p>
              <div className="flex gap-1">
                <input value={userBlInput} onChange={e => setUserBlInput(e.target.value)}
                  onKeyDown={e => { if (e.key === "Enter" && userBlInput.trim()) { setUserBlacklist(p => [...p, userBlInput.trim()]); setUserBlInput(""); }}}
                  placeholder="User-ID" className="text-xs border rounded px-2 py-1 flex-1 bg-background" />
                <button type="button" onClick={() => { if (userBlInput.trim()) { setUserBlacklist(p => [...p, userBlInput.trim()]); setUserBlInput(""); }}}
                  className="text-xs px-2 py-1 border rounded hover:bg-accent">+</button>
              </div>
              <div className="mt-1 space-y-0.5">
                {userBlacklist.map(id => (
                  <div key={id} className="flex items-center gap-1 text-xs">
                    <span className="font-mono flex-1">{id}</span>
                    <button type="button" onClick={() => setUserBlacklist(p => p.filter(x => x !== id))} className="text-muted-foreground hover:text-destructive">×</button>
                  </div>
                ))}
              </div>
            </div>
          </div>
          <p className="text-xs text-muted-foreground">{t("myAgent.discordUserHint")}</p>
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
    <ConfirmDialog
      open={!!confirmState}
      title={confirmState?.title || ""}
      message={confirmState?.message || ""}
      onConfirm={() => { confirmState?.action(); setConfirmState(null); }}
      onCancel={() => setConfirmState(null)}
      variant="danger"
    />
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
    voice_mode:            "echo",
    voice_name:            "de-DE-KatjaNeural",
  });
  const [cfgSaving, setCfgSaving] = useState(false);
  const [numInput,   setNumInput]   = useState("");
  const [blockInput, setBlockInput] = useState("");
  const [ownerInput, setOwnerInput] = useState("");
  const [voices, setVoices] = useState<{ id: string; label: string; lang: string }[]>([]);
  const [previewPlaying, setPreviewPlaying] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [confirmState, setConfirmState] = useState<{action: () => void; title: string; message: string} | null>(null);

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
          voice_mode:            s.voice_mode            ?? "echo",
          voice_name:            s.voice_name            ?? "de-DE-KatjaNeural",
        });
      }
      if (voices.length === 0) {
        api.get<{ voices: { id: string; label: string; lang: string }[] }>("/me/whatsapp/voices")
          .then(r => setVoices(r.voices || [])).catch(e => console.error("Failed to load WhatsApp voices", e));
      }
      if (s.status === "connected" || s.status === "bridge_unavailable" || s.status === "disconnected") {
        if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
      }
    } catch {}
  }

  async function saveCfg() {
    setCfgSaving(true); setMsg("");
    try {
      // Noch offene Eingaben automatisch übernehmen
      const finalCfg = {
        ...cfg,
        allowed_numbers: numInput.trim()   ? [...cfg.allowed_numbers,  numInput.trim()]   : cfg.allowed_numbers,
        blocked_numbers: blockInput.trim() ? [...cfg.blocked_numbers, blockInput.trim()] : cfg.blocked_numbers,
        owner_numbers:   ownerInput.trim() ? [...cfg.owner_numbers,   ownerInput.trim()] : cfg.owner_numbers,
      };
      if (numInput.trim())   { setCfg(finalCfg); setNumInput(""); }
      if (blockInput.trim()) { setCfg(finalCfg); setBlockInput(""); }
      if (ownerInput.trim()) { setCfg(finalCfg); setOwnerInput(""); }
      await api.updateWhatsAppConfig(finalCfg);
      setMsg(t("common.saved"));
      setTimeout(() => setMsg(""), 3000);
    } catch (e) { setMsg(e instanceof Error ? e.message : t("common.error")); }
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
      setMsg(t("common.error") + ": " + (err instanceof Error ? err.message : String(err)));
    } finally { setLoading(false); }
  }

  function handleDisconnect() {
    setConfirmState({
      title: t("confirm.titleDisconnect"),
      message: t("myAgent.whatsappDisconnect") + "?",
      action: async () => {
        try {
          await api.disconnectWhatsApp();
          setStatus({ configured: false, status: "disconnected", qr: null, phone: null });
          setMsg(t("myAgent.whatsappDisconnected2"));
          if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
        } catch (err: unknown) {
          setMsg(t("common.error") + ": " + (err instanceof Error ? err.message : String(err)));
        }
      },
    });
  }

  async function handleInstallChromium() {
    setLoading(true); setMsg(t("myAgent.whatsappChromiumInstalling"));
    try {
      const r = await api.installWhatsAppChromium();
      if (r.ok) {
        setMsg(t("myAgent.whatsappChromiumInstalled"));
        await fetchStatus();
      } else {
        setMsg(t("common.error") + ": " + (r.error ?? "unbekannt"));
      }
    } catch (err: unknown) {
      setMsg(t("common.error") + ": " + (err instanceof Error ? err.message : String(err)));
    } finally { setLoading(false); }
  }

  const connected   = status?.status === "connected";
  const waitingQr   = status?.status === "waiting_qr";
  const reconnecting = status?.status === "reconnecting" || status?.status === "connecting";
  const bridgeDown  = status?.status === "bridge_unavailable";
  const bridgeError = status?.status === "error" || !!status?.bridge_error;

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
        <div className={`flex flex-col gap-1 p-3 rounded-md text-xs border ${
          connected   ? "bg-green-50 border-green-200 text-green-700" :
          waitingQr   ? "bg-yellow-50 border-yellow-200 text-yellow-700" :
          bridgeDown || bridgeError ? "bg-red-50 border-red-200 text-red-700" :
                        "bg-muted border-border text-muted-foreground"
        }`}>
          <div className="flex items-center gap-2">
            {connected   ? <CheckCircle className="h-3.5 w-3.5" /> :
             waitingQr   ? <Sparkles className="h-3.5 w-3.5 animate-pulse" /> :
             bridgeDown || bridgeError ? <AlertCircle className="h-3.5 w-3.5" /> :
                           <WifiOff className="h-3.5 w-3.5" />}
            <span>
              {connected    ? t("myAgent.whatsappConnected", { phone: status.phone ? ` · +${status.phone}` : "" }) :
               waitingQr    ? t("myAgent.whatsappWaitingQr") :
               reconnecting ? t("myAgent.whatsappReconnecting") :
               bridgeDown   ? t("myAgent.whatsappBridgeDown") :
               bridgeError  ? t("myAgent.whatsappBridgeError") :
                              t("myAgent.whatsappDisconnected")}
            </span>
            {(waitingQr || reconnecting) && (
              <span className="ml-auto text-xs opacity-60 animate-pulse">●</span>
            )}
          </div>
          {bridgeError && status.bridge_error && (
            <p className="mt-1 font-mono text-xs opacity-80 break-all">{status.bridge_error}</p>
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
        {bridgeError && (
          <button onClick={handleInstallChromium} disabled={loading}
            className="flex items-center gap-2 px-4 py-2 text-sm bg-amber-600 text-white rounded-md hover:bg-amber-700 disabled:opacity-50 transition-colors">
            <Download className="h-3.5 w-3.5" />
            {loading ? t("myAgent.whatsappChromiumInstalling") : t("myAgent.whatsappInstallChromium")}
          </button>
        )}
        {!connected && !waitingQr && !reconnecting && (
          <button onClick={handleConnect} disabled={loading || bridgeError}
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

          {/* Voice-Einstellungen (#172) */}
          <div className="space-y-3 pt-3 border-t">
            <h3 className="text-sm font-semibold">Sprachnachrichten</h3>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1">
                <label className="text-xs text-muted-foreground">Voice-Modus</label>
                <select value={cfg.voice_mode} onChange={e => setCfg({ ...cfg, voice_mode: e.target.value })}
                  className="w-full px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary">
                  <option value="echo">Echo (Voice → Voice, Text → Text)</option>
                  <option value="always">Immer Voice-Antwort</option>
                  <option value="never">Nie Voice (immer Text)</option>
                </select>
              </div>
              <div className="space-y-1">
                <label className="text-xs text-muted-foreground">Stimme</label>
                <div className="flex gap-1.5">
                  <select value={cfg.voice_name} onChange={e => setCfg({ ...cfg, voice_name: e.target.value })}
                    className="flex-1 px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary">
                    {voices.length > 0 ? voices.map(v => (
                      <option key={v.id} value={v.id}>{v.label}</option>
                    )) : (
                      <option value={cfg.voice_name}>{cfg.voice_name}</option>
                    )}
                  </select>
                  <button type="button" onClick={async () => {
                    setPreviewPlaying(cfg.voice_name);
                    try {
                      const token = localStorage.getItem("hydrahive_token") || "";
                      const res = await fetch("/api/me/whatsapp/voice-preview", {
                        method: "POST",
                        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
                        body: JSON.stringify({ voice: cfg.voice_name }),
                      });
                      if (res.ok) {
                        const blob = await res.blob();
                        const url = URL.createObjectURL(blob);
                        const a = new Audio(url);
                        a.onended = () => { setPreviewPlaying(null); URL.revokeObjectURL(url); };
                        a.play();
                      }
                    } catch {} finally { setTimeout(() => setPreviewPlaying(null), 5000); }
                  }} disabled={previewPlaying !== null}
                    className="px-3 py-2 text-sm border rounded-md hover:bg-accent transition-colors disabled:opacity-50 shrink-0">
                    {previewPlaying ? "▶ ..." : "▶ Test"}
                  </button>
                </div>
              </div>
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
    <ConfirmDialog
      open={!!confirmState}
      title={confirmState?.title || ""}
      message={confirmState?.message || ""}
      onConfirm={() => { confirmState?.action(); setConfirmState(null); }}
      onCancel={() => setConfirmState(null)}
      variant="danger"
    />
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
    } catch (e) { setMsg(e instanceof Error ? e.message : t("common.error")); }
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

      {/* Info Block */}
      <div className="mb-6 rounded-xl border bg-muted/30 p-4 space-y-2">
        <h3 className="text-sm font-semibold">{t("heartbeat.infoTitle")}</h3>
        <p className="text-xs text-muted-foreground leading-relaxed">{t("heartbeat.infoText")}</p>
        <div className="text-xs text-muted-foreground space-y-1">
          <p><strong>Task-ID:</strong> {t("heartbeat.helpTaskId")}</p>
          <p><strong>Nachricht:</strong> {t("heartbeat.helpMessage")}</p>
          <p><strong>Cron:</strong> {t("heartbeat.helpCron")}</p>
          <p><strong>Intervall:</strong> {t("heartbeat.helpInterval")}</p>
        </div>
      </div>

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
  const [confirmState, setConfirmState] = useState<{action: () => void; title: string; message: string} | null>(null);

  useEffect(() => {
    api.getMail().then(d => {
      setCfg(d);
      setMailAddress(d.mail_address ?? "");
    }).catch(e => console.error("Failed to load mail config", e));
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
      setMsg(t("common.error") + ": " + (err instanceof Error ? err.message : String(err)));
    } finally { setSaving(false); }
  }

  function handleDelete() {
    setConfirmState({
      title: t("confirm.titleDelete"),
      message: t("myAgent.mailDeleteConfirm"),
      action: async () => {
        try {
          await api.deleteMail();
          setCfg({ configured: false, mail_address: "", smtp_host: "" });
          setMailAddress(""); setDomain(""); setSmtpHost(""); setSmtpPassword("");
          setMsg(t("myAgent.mailRemoved"));
        } catch (err: unknown) {
          setMsg(t("common.error") + ": " + (err instanceof Error ? err.message : String(err)));
        }
      },
    });
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
    <ConfirmDialog
      open={!!confirmState}
      title={confirmState?.title || ""}
      message={confirmState?.message || ""}
      onConfirm={() => { confirmState?.action(); setConfirmState(null); }}
      onCancel={() => setConfirmState(null)}
      variant="danger"
    />
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
  const [confirmState, setConfirmState] = useState<{action: () => void; title: string; message: string} | null>(null);

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
      alert(e?.message ?? t("common.error"));
    } finally {
      setLoading(false);
    }
  }

  function handleDisconnect() {
    setConfirmState({
      title: t("confirm.titleDisconnect"),
      message: t("myAgent.telegramDisconnectConfirm"),
      action: async () => {
        setLoading(true);
        try {
          await api.disconnectTelegram();
          setStatus({ configured: false, enabled: false, status: "stopped", bot_username: "" });
        } finally {
          setLoading(false);
        }
      },
    });
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
    <ConfirmDialog
      open={!!confirmState}
      title={confirmState?.title || ""}
      message={confirmState?.message || ""}
      onConfirm={() => { confirmState?.action(); setConfirmState(null); }}
      onCancel={() => setConfirmState(null)}
      variant="danger"
    />
    </div>
  );
}
