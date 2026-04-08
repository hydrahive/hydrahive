/**
 * ChatPage — Projekt-Chat mit Worker-Swarm-Anzeige (#491 refactored)
 *
 * Nutzt shared ChatView + useChatStream Hook.
 * Page-spezifisch: Projekt-Header, Swarm-Toggle, History, Live-Polling.
 */
import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { ArrowLeft, Bot, Network, History, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { api, type SessionPreview, type SessionFull } from "@/lib/api";
import { ChatView } from "@/components/ChatView";
import { useChatStream, mkMsg } from "@/hooks/useChatStream";

export function ChatPage() {
  const { t } = useTranslation();
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  // Project info
  const [projectName, setProjectName] = useState(id ?? "");
  const [bossModel, setBossModel] = useState<{ model?: string }>({});
  const [showSwarm, setShowSwarm] = useState(false);

  // History
  const [historyList, setHistoryList] = useState<SessionPreview[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);

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

  return (
    <div className="flex flex-col h-full min-h-0 overflow-hidden">
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
      </div>

      {/* History Panel */}
      {chat.showHistory && (
        <div className="flex-1 overflow-y-auto border-b bg-muted/20">
          <div className="flex items-center justify-between px-4 py-2 border-b">
            <span className="text-xs font-medium text-muted-foreground">{t("chat.pastSessions", { defaultValue: "Vergangene Sessions" })}</span>
            <button onClick={() => { chat.setShowHistory(false); chat.setViewSession(null); }} className="p-1 rounded hover:bg-accent"><X className="h-3.5 w-3.5" /></button>
          </div>
          <div className="divide-y">
            {historyList.map(s => (
              <button key={s.id} onClick={() => {
                api.get<SessionFull>(`/projects/${id}/sessions/${s.id}`)
                  .then(d => {
                    const msgs = d.messages.map((m: any) => mkMsg(m.role, m.content));
                    chat.setViewSession({ id: d.id, messages: msgs, startedAt: d.started_at });
                  }).catch(() => {});
              }}
                className="w-full text-left px-4 py-2 hover:bg-accent/50 text-xs">
                <div className="font-medium">{s.preview || "(leer)"}</div>
                <div className="text-muted-foreground">{s.message_count} Messages · {new Date(s.started_at).toLocaleDateString("de-DE")}</div>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Chat */}
      {!chat.showHistory && (
        <ChatView
          {...chat}
          t={t}
          showWorkers={showSwarm}
          slashCommands={SLASH_COMMANDS}
        />
      )}
    </div>
  );
}
