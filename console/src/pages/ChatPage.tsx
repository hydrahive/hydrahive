/**
 * ChatPage — Projekt-Chat (Chat v2, #727/#728).
 * Page-spezifisch: Projekt-Header, Swarm-Toggle, Typing/Broadcast-Sync,
 * EKG-Monitor, Info-Sidebar. Chat-Kern läuft über ChatShell.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { ArrowLeft, Bot, Network, History, Sparkles, PanelRightClose, PanelRightOpen, Activity } from "lucide-react";
import { EkgMonitor } from "@/components/EkgMonitor";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import { ChatShell } from "@/components/chat-v2/ChatShell";
import { buildChatV2Target, useHydraHiveRuntime } from "@/components/chat-v2/hydrahive-runtime";
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

  const target = useMemo(() => buildChatV2Target("project", id ?? ""), [id]);
  async function handleSlashCommand(cmd: string, args: string): Promise<boolean> {
    if (cmd === "/status") {
      runtime.pushSystemMessage(`Projekt: ${projectName}\nModell: ${bossModel.model ?? "unbekannt"}`);
      return true;
    }
    if (cmd === "/model") {
      runtime.pushSystemMessage(`Modell: ${bossModel.model ?? "unbekannt"}`);
      return true;
    }
    if (cmd === "/retry") {
      const last = [...runtime.messages].reverse().find(m => m.role === "user");
      if (!last) { runtime.pushSystemMessage("Keine Nachricht zum Wiederholen."); return true; }
      const text = last.content
        .filter((p) => p.type === "text")
        .map((p) => (p as { type: "text"; text: string }).text)
        .join(" ")
        .trim();
      if (text) void runtime.sendText(text);
      return true;
    }
    if (cmd === "/remember") {
      try {
        await api.post(`/projects/${id}/memory`, { filename: "user-notes", content: args, mode: "append" });
        runtime.pushSystemMessage("Gespeichert.");
      } catch (e) {
        runtime.pushSystemMessage(`Speichern fehlgeschlagen: ${e instanceof Error ? e.message : String(e)}`);
      }
      return true;
    }
    return false;
  }
  const runtime = useHydraHiveRuntime(target, { onSlashCommand: handleSlashCommand });

  // Subscribe fuer Typing-Indicator + Broadcast-Sync (#553)
  const handleBroadcast = useCallback((raw: Record<string, unknown>) => {
    if (runtime.isRunning) return;

    if (raw.text !== undefined) {
      runtime.pushBroadcastText(String(raw.text));
    } else if (raw.done) {
      const usage = raw.usage as { input?: number; output?: number; rounds?: number; cache_read?: number; cache_write?: number } | undefined;
      const model = raw.model as string | undefined;
      runtime.finishBroadcast({
        usage: usage ? {
          input: usage.input,
          output: usage.output,
          rounds: usage.rounds,
          cache_read: usage.cache_read,
          cache_write: usage.cache_write,
        } : undefined,
        model,
        isFallback: !!raw.is_fallback,
      });
    } else if (raw._user_message) {
      runtime.pushUserMessage(String(raw._user_message));
    }
  }, [runtime]);

  const subscribe = useProjectSubscribe(id, handleBroadcast);

  // Load project info
  useEffect(() => {
    if (!id) return;
    api.get<Record<string, unknown>>(`/projects/${id}`)
      .then(d => {
        const cfg = d.config as any;
        if (cfg?.identity?.name) setProjectName(cfg.identity.name);
        if (cfg?.chat?.show_swarm) setShowSwarm(true);
        // v2 projects store the effective LLM config directly on config.llm.
        // Legacy projects may still point to a boss agent.
        if (cfg?.llm?.model) {
          setBossModel(cfg.llm);
          return;
        }
        const bossId = cfg?.agents?.boss;
        if (bossId) {
          api.get<Record<string, unknown>>(`/agents/${bossId}`)
            .then(a => { const llm = (a as any)?.config?.llm; if (llm) setBossModel(llm); })
            .catch(() => {});
        }
      }).catch(() => {});
  }, [id]);

  // Auto-Resume: letzte bekannte Session-ID aus localStorage reaktivieren.
  // runtime.resumeSession ruft api.resumeProjectSession und ersetzt Messages;
  // läuft bewusst nach runtime's initialem history-Load (race egal).
  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    try {
      const lastSid = localStorage.getItem(`hh_lastsess_/api/projects/${id}/session/history`);
      if (lastSid) {
        runtime.resumeSession(lastSid).catch(() => {/* Session evtl. gelöscht */});
      }
    } catch { /* localStorage nicht verfügbar */ }
    return () => { cancelled = true; void cancelled; };
  }, [id]);

  // v2 (#602): 3s-Polling entfernt — Real-Time Sync via useProjectSubscribe.
  // Recovery-Polling NUR wenn SSE-Verbindung tot ist (10s Fallback).
  useEffect(() => {
    if (!id || runtime.isRunning) return;
    if (subscribe.isConnected) return;
    const poll = setInterval(() => {
      void runtime.reloadHistory();
    }, 10000);
    return () => clearInterval(poll);
  }, [id, runtime.isRunning, subscribe.isConnected, runtime.reloadHistory]);

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
          <button onClick={runtime.toggleHistory}
            className={`p-1.5 rounded-md transition-colors ${runtime.showHistory ? "bg-accent text-accent-foreground" : "hover:bg-accent text-muted-foreground"}`}
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

        {/* Shortcut-Chips */}
        <div className="flex flex-wrap gap-2 px-4 pt-3">
          {SLASH_COMMANDS.map((cmd) => (
            <button key={cmd.cmd}
              type="button"
              onClick={() => runtime.aui.composer().setText(`${cmd.cmd} `)}
              className="rounded-full border border-border/70 bg-card px-3 py-1 text-[11px] text-muted-foreground transition hover:border-primary/40 hover:text-foreground">
              {cmd.cmd}
            </button>
          ))}
        </div>

        <div className="flex-1 min-h-0">
          <ChatShell
            runtime={runtime}
            hideHeader
            typingUsers={Array.from(subscribe.typingUsers.entries()).filter(([, active]) => active).map(([user]) => user)}
            onComposerActivity={handleTyping}
          />
        </div>
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
                    <p className="text-xs text-muted-foreground">{runtime.isRunning ? t("chat.streamingBuilding", { defaultValue: "Antwort wird generiert …" }) : t("chat.streamingIdle", { defaultValue: "Bereit" })}</p>
                  </div>
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
                  <p className="mt-1 font-medium">{runtime.messages.length} {runtime.messages.length === 1 ? "Nachricht" : "Nachrichten"}</p>
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
