/**
 * ChatPage — Projekt-Chat mit Worker-Swarm-Anzeige (#491 refactored)
 *
 * Nutzt shared ChatView + useChatStream Hook.
 * Page-spezifisch: Projekt-Header, Swarm-Toggle, History, Live-Polling, Info-Sidebar.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { ArrowLeft, Bot, Network, History, X, RotateCcw, Plus, Sparkles, Terminal, PanelRightClose, PanelRightOpen, Activity } from "lucide-react";
import { EkgMonitor } from "@/components/EkgMonitor";
import { useTranslation } from "react-i18next";
import { api, type SessionPreview, type SessionFull } from "@/lib/api";
import { ChatView } from "@/components/ChatView";
import { useChatStream, mkMsg } from "@/hooks/useChatStream";
import { useProjectSubscribe } from "@/hooks/useProjectSubscribe";

export function ChatPage() {
  const { t } = useTranslation();
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  // Project info
  const [projectName, setProjectName] = useState(id ?? "");
  const [bossModel, setBossModel] = useState<{ model?: string; temperature?: number }>({});
  const [showSwarm, setShowSwarm] = useState(false);
  const [showSidebar, setShowSidebar] = useState(true);
  const [showMonitor, setShowMonitor] = useState(false);

  // History
  const [historyList, setHistoryList] = useState<SessionPreview[]>([]);

  const broadcastBuf = useRef<string>("");
  const broadcastMsgId = useRef<string | null>(null);
  const typingDebounce = useRef<ReturnType<typeof setTimeout> | null>(null);

  const handleTyping = useCallback((active: boolean) => {
    if (!id) return;
    // Debounce: max alle 2s ein POST
    if (typingDebounce.current && active) return;
    api.post(`/projects/${id}/typing`, { active }).catch(() => {});
    if (active) {
      typingDebounce.current = setTimeout(() => { typingDebounce.current = null; }, 2000);
    } else {
      if (typingDebounce.current) { clearTimeout(typingDebounce.current); typingDebounce.current = null; }
    }
  }, [id]);

  const SLASH_COMMANDS = [
    { cmd: "/help",     desc: t("slashCommands.help") },
    { cmd: "/clear",    desc: t("slashCommands.clear") },
    { cmd: "/status",   desc: t("slashCommands.status") },
    { cmd: "/retry",    desc: t("slashCommands.retry") },
    { cmd: "/model",    desc: t("slashCommands.model") },
    { cmd: "/remember", desc: t("slashCommands.remember") },
  ];

  const chat = useChatStream({
    streamEndpoint: `/api/projects/${id}/message/stream`,
    historyEndpoint: `/api/projects/${id}/session/history`,
    onSlashCommand: (cmd, args) => {
      if (cmd === "/status") {
        chat.setMessages(ms => [...ms, mkMsg("system", `Projekt: ${projectName}\nModell: ${bossModel.model ?? "unbekannt"}`)]);
        return true;
      }
      if (cmd === "/model") {
        chat.setMessages(ms => [...ms, mkMsg("system", `Modell: ${bossModel.model ?? "unbekannt"}`)]);
        return true;
      }
      if (cmd === "/retry") {
        const lastUser = [...chat.messages].reverse().find(m => m.role === "user");
        if (lastUser) chat.send(lastUser.content);
        return true;
      }
      if (cmd === "/remember") {
        api.post(`/projects/${id}/memory`, { filename: "user-notes", content: args, mode: "append" }).catch(() => {});
        chat.setMessages(ms => [...ms, mkMsg("system", "Gespeichert.")]);
        return true;
      }
      return false;
    },
  });

  // Subscribe fuer Typing-Indicator + Broadcast-Sync (#553)
  const handleBroadcast = useCallback((raw: Record<string, unknown>) => {
    if (chat.sending) return;

    if (raw.text !== undefined) {
      if (!broadcastMsgId.current) {
        broadcastMsgId.current = `broadcast-${Date.now()}`;
        broadcastBuf.current = "";
        chat.setMessages(ms => [...ms, mkMsg("assistant", "")]);
      }
      broadcastBuf.current += String(raw.text);
      const txt = broadcastBuf.current;
      chat.setMessages(ms => {
        const last = ms[ms.length - 1];
        if (last && last.role === "assistant") {
          return [...ms.slice(0, -1), { ...last, content: txt }];
        }
        return ms;
      });
    } else if (raw.done) {
      broadcastMsgId.current = null;
      broadcastBuf.current = "";
    } else if (raw._user_message) {
      chat.setMessages(ms => [...ms, mkMsg("user", String(raw._user_message))]);
    }
  }, [chat.sending]);

  const subscribe = useProjectSubscribe(id, handleBroadcast);

  // Load project info + history
  useEffect(() => {
    if (!id) return;
    api.get<Record<string, unknown>>(`/projects/${id}`)
      .then(d => {
        const cfg = d.config as any;
        if (cfg?.identity?.name) setProjectName(cfg.identity.name);
        if (cfg?.chat?.show_swarm) setShowSwarm(true);
        const bossId = cfg?.agents?.boss;
        if (bossId) {
          api.get<Record<string, unknown>>(`/agents/${bossId}`)
            .then(a => { const llm = (a as any)?.config?.llm; if (llm) setBossModel(llm); })
            .catch(() => {});
        }
      }).catch(() => {});
    chat.loadHistory();
  }, [id]);

  // Live-Sync: History alle 3s refreshen wenn nicht am Streamen
  useEffect(() => {
    if (!id || chat.sending) return;
    const poll = setInterval(() => {
      chat.loadHistory();
    }, 3000);
    return () => clearInterval(poll);
  }, [id, chat.sending]);

  // Past Sessions laden wenn History-Panel geöffnet wird
  useEffect(() => {
    if (!chat.showHistory || !id) return;
    api.listProjectSessions(id, 30)
      .then(d => setHistoryList(d.sessions || []))
      .catch(() => {});
  }, [chat.showHistory, id]);

  async function resumePastSession(sid: string) {
    if (!id) return;
    try {
      const d = await api.resumeProjectSession(id, sid);
      const msgs = (d.messages || [])
        .filter((m: any) => (m.role === "user" || m.role === "assistant") && !(m.role === "assistant" && !m.content))
        .map((m: any) => mkMsg(m.role as "user" | "assistant", m.content));
      chat.setMessages(msgs);
      chat.setViewSession(null);
      chat.setShowHistory(false);
    } catch {}
  }

  return (
    <section className="flex h-full min-h-0 overflow-hidden">
      {/* Main Column: Header + Content */}
      <div className="flex flex-1 flex-col min-h-0 min-w-0 overflow-hidden">
        {/* Header */}
        <div className="flex items-center gap-3 px-4 py-3 border-b flex-shrink-0">
          <button onClick={() => navigate("/projects")} className="p-1.5 rounded-md hover:bg-accent transition-colors">
            <ArrowLeft className="h-4 w-4" />
          </button>
          <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center flex-shrink-0">
            <Bot className="h-4 w-4 text-primary" />
          </div>
          <div className="flex-1 min-w-0">
            <h1 className="text-sm font-semibold truncate">{projectName}</h1>
            <p className="text-xs text-muted-foreground font-mono">{bossModel.model ?? id}</p>
          </div>
          <button onClick={() => setShowSwarm(s => !s)}
            className={`p-1.5 rounded-md transition-colors ${showSwarm ? "bg-accent text-accent-foreground" : "hover:bg-accent text-muted-foreground"}`}
            title="Worker-Swarm">
            <Network className="h-4 w-4" />
          </button>
          <button onClick={() => { chat.setShowHistory(h => !h); chat.setViewSession(null); }}
            className={`p-1.5 rounded-md transition-colors ${chat.showHistory ? "bg-accent text-accent-foreground" : "hover:bg-accent text-muted-foreground"}`}
            title="Chat-Verlauf">
            <History className="h-4 w-4" />
          </button>
          <button onClick={() => setShowMonitor(true)}
            className="p-1.5 rounded-md transition-colors hover:bg-accent text-muted-foreground hover:text-emerald-500"
            title="EKG Monitor">
            <Activity className="h-4 w-4" />
          </button>
          <button onClick={() => setShowSidebar(s => !s)}
            className={`hidden lg:block p-1.5 rounded-md transition-colors ${showSidebar ? "bg-accent text-accent-foreground" : "hover:bg-accent text-muted-foreground"}`}
            title="Info-Panel">
            {showSidebar ? <PanelRightClose className="h-4 w-4" /> : <PanelRightOpen className="h-4 w-4" />}
          </button>
        </div>

        {/* History Panel — Session-Liste */}
        {chat.showHistory && !chat.viewSession && (
          <div className="flex-1 overflow-y-auto border-b bg-muted/20">
            <div className="flex items-center justify-between px-4 py-2 border-b">
              <span className="text-xs font-medium text-muted-foreground">{t("chat.pastSessions", { defaultValue: "Vergangene Sessions" })}</span>
              <button onClick={() => { chat.setShowHistory(false); chat.setViewSession(null); }} className="p-1 rounded hover:bg-accent"><X className="h-3.5 w-3.5" /></button>
            </div>
            {historyList.length === 0 ? (
              <p className="text-xs text-muted-foreground text-center py-6">Keine vergangenen Sessions</p>
            ) : (
              <div className="divide-y max-h-full overflow-y-auto">
                {historyList.map(s => (
                  <div key={s.id} className="flex items-stretch hover:bg-accent/50 transition-colors">
                    <button onClick={() => {
                      api.get<SessionFull>(`/projects/${id}/sessions/${s.id}`)
                        .then(d => {
                          const msgs = d.messages.map((m: any) => mkMsg(m.role, m.content));
                          chat.setViewSession({ id: d.id, messages: msgs, startedAt: d.started_at });
                        }).catch(() => {});
                    }}
                      className="flex-1 text-left px-4 py-3 text-xs">
                      <div className="font-medium">{s.preview || "(leer)"}</div>
                      <div className="text-muted-foreground">{s.message_count} Messages · {new Date(s.started_at).toLocaleDateString("de-DE")}</div>
                    </button>
                    <button onClick={() => resumePastSession(s.id)}
                      title="Chat fortsetzen"
                      className="flex items-center px-3 text-primary hover:bg-primary/10 border-l transition-colors flex-shrink-0">
                      <RotateCcw className="h-3.5 w-3.5" />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* History Panel — Session-Ansicht */}
        {chat.viewSession && (
          <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
            <div className="flex items-center justify-between px-4 py-2 border-b flex-shrink-0">
              <span className="text-xs text-muted-foreground">
                Session vom {new Date(chat.viewSession.startedAt).toLocaleString("de-DE")}
              </span>
              <div className="flex gap-2 flex-shrink-0">
                <button onClick={() => { chat.setViewSession(null); chat.setShowHistory(true); }}
                  className="flex items-center gap-1 px-2 py-1 rounded hover:bg-accent transition-colors text-muted-foreground text-xs">
                  <ArrowLeft className="h-3 w-3" /> Zurück
                </button>
                <button onClick={() => resumePastSession(chat.viewSession!.id)}
                  className="flex items-center gap-1 px-2 py-1 rounded bg-primary text-primary-foreground hover:bg-primary/90 transition-colors text-xs">
                  <RotateCcw className="h-3 w-3" /> Fortsetzen
                </button>
                <button onClick={() => { chat.setViewSession(null); chat.setShowHistory(false); }}
                  className="flex items-center gap-1 px-2 py-1 rounded border hover:bg-accent transition-colors text-muted-foreground text-xs">
                  <Plus className="h-3 w-3" /> Neuer Chat
                </button>
              </div>
            </div>
            <div className="mx-4 mt-3 flex items-center gap-2 rounded-xl border bg-amber-500/10 px-3 py-2 text-xs text-amber-600 dark:text-amber-400">
              <span>{t("chat.historyReadOnly", { defaultValue: "Du siehst eine vergangene Session — nur lesen." })}</span>
            </div>
            <div className="flex-1 overflow-y-auto p-4 space-y-3">
              {chat.viewSession.messages.map((m, i) => (
                <div key={i} className={`text-sm ${m.role === "user" ? "text-right" : ""}`}>
                  <div className={`inline-block max-w-[85%] rounded-lg px-3 py-2 ${
                    m.role === "user" ? "bg-primary text-primary-foreground" : "bg-muted"
                  }`}>
                    <pre className="whitespace-pre-wrap font-sans text-xs">{m.content}</pre>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Chat */}
        {!chat.showHistory && !chat.viewSession && (
          <ChatView
            {...chat}
            t={t}
            showWorkers={showSwarm}
            slashCommands={SLASH_COMMANDS}
            typingUsers={subscribe.typingUsers}
            onTyping={handleTyping}
          />
        )}
      </div>

      {/* Info-Sidebar (Desktop only) */}
      {showSidebar && (
        <aside className="hidden lg:flex flex-col w-80 flex-shrink-0 border-l bg-muted/10 p-4 overflow-y-auto">
          <div className="space-y-4">
            {/* Live Panel */}
            <div className="rounded-2xl border bg-background/75 p-4">
              <p className="text-[0.65rem] uppercase tracking-[0.16em] text-muted-foreground">{t("chat.livePanel", { defaultValue: "Live" })}</p>
              <div className="mt-3 space-y-3">
                <div className="flex items-start gap-3">
                  <span className="rounded-2xl bg-primary/12 p-2 text-primary"><Sparkles className="h-4 w-4" /></span>
                  <div>
                    <p className="text-sm font-medium">{t("chat.streaming", { defaultValue: "Streaming" })}</p>
                    <p className="text-xs text-muted-foreground">{chat.sending ? t("chat.streamingBuilding", { defaultValue: "Antwort wird generiert …" }) : t("chat.streamingIdle", { defaultValue: "Bereit" })}</p>
                  </div>
                </div>
                <div className="rounded-2xl border bg-background/75 px-3 py-3">
                  <p className="text-[0.65rem] uppercase tracking-[0.16em] text-muted-foreground">{t("chat.activeTool", { defaultValue: "Aktives Tool" })}</p>
                  {chat.activeTool ? (
                    <div className="mt-2 space-y-1">
                      <p className="text-sm font-medium text-primary flex items-center gap-1"><Terminal className="h-3 w-3" />{chat.activeTool.name}</p>
                      <p className="break-all text-xs text-muted-foreground">{chat.activeTool.detail || t("chat.noToolDetail", { defaultValue: "Keine Details" })}</p>
                    </div>
                  ) : (
                    <p className="mt-2 text-xs text-muted-foreground">{t("chat.noTool", { defaultValue: "Kein Tool aktiv" })}</p>
                  )}
                </div>
              </div>
            </div>

            {/* Projekt-Info */}
            <div className="rounded-2xl border bg-background/75 p-4">
              <p className="text-[0.65rem] uppercase tracking-[0.16em] text-muted-foreground">{t("chat.projectPanel", { defaultValue: "Projekt" })}</p>
              <div className="mt-3 space-y-3 text-sm">
                <div className="rounded-2xl bg-secondary/40 px-3 py-3">
                  <p className="text-[0.65rem] uppercase tracking-[0.16em] text-muted-foreground">{t("chat.projectPanel", { defaultValue: "Projekt" })}</p>
                  <p className="mt-1 font-medium">{projectName}</p>
                  <p className="font-mono text-xs text-muted-foreground">{id}</p>
                </div>
                <div className="rounded-2xl bg-secondary/40 px-3 py-3">
                  <p className="text-[0.65rem] uppercase tracking-[0.16em] text-muted-foreground">{t("chat.bossModel", { defaultValue: "Modell" })}</p>
                  <p className="mt-1 break-all font-medium">{bossModel.model ?? t("chat.notConfigured", { defaultValue: "Nicht konfiguriert" })}</p>
                  <p className="text-xs text-muted-foreground">Temp: {bossModel.temperature ?? "—"}</p>
                </div>
                <div className="rounded-2xl bg-secondary/40 px-3 py-3">
                  <p className="text-[0.65rem] uppercase tracking-[0.16em] text-muted-foreground">{t("chat.history", { defaultValue: "Verlauf" })}</p>
                  <p className="mt-1 font-medium">{chat.messages.length} {chat.messages.length === 1 ? "Nachricht" : "Nachrichten"}</p>
                </div>
              </div>
            </div>

            {/* Shortcuts */}
            <div className="rounded-2xl border bg-background/75 p-4">
              <p className="text-[0.65rem] uppercase tracking-[0.16em] text-muted-foreground">{t("chat.shortcuts", { defaultValue: "Shortcuts" })}</p>
              <div className="mt-3 space-y-2">
                {SLASH_COMMANDS.map(c => (
                  <div key={c.cmd} className="rounded-2xl border bg-background/70 px-3 py-2">
                    <p className="font-mono text-xs text-primary">{c.cmd}</p>
                    <p className="mt-1 text-xs text-muted-foreground">{c.desc}</p>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </aside>
      )}
      {/* EKG Monitor Overlay */}
      {showMonitor && id && <EkgMonitor projectId={id} onClose={() => setShowMonitor(false)} />}
    </section>
  );
}
