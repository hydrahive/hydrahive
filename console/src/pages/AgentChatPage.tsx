/**
 * AgentChatPage — Agent-Chat mit Debug-Konsole (#491 refactored)
 *
 * Nutzt shared ChatView + useChatStream Hook.
 * Page-spezifisch: Agent-Header, Debug-Panel, History-Panel.
 */
import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { ArrowLeft, Bot, Clock, Bug, Zap, Cpu, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { api, type SessionPreview } from "@/lib/api";
import { ChatView } from "@/components/ChatView";
import { useChatStream, mkMsg, type ChatMessage } from "@/hooks/useChatStream";

export function AgentChatPage() {
  const { t } = useTranslation();
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  // Agent info
  const [agentName, setAgentName] = useState(id ?? "");
  const [agentModel, setAgentModel] = useState<{ model?: string }>({});

  // Debug
  const [showDebug, setShowDebug] = useState(false);

  // Slash commands
  const SLASH_COMMANDS = [
    { cmd: "/help",     desc: t("slashCommands.help") },
    { cmd: "/clear",    desc: t("slashCommands.clear") },
    { cmd: "/compact",  desc: t("slashCommands.compact") },
    { cmd: "/model",    desc: t("slashCommands.model") },
    { cmd: "/retry",    desc: t("slashCommands.retry") },
    { cmd: "/remember", desc: t("slashCommands.remember") },
    { cmd: "/history",  desc: t("slashCommands.history") },
  ];

  // Chat hook
  const chat = useChatStream({
    streamEndpoint: `/api/agents/${id}/message/stream`,
    historyEndpoint: `/api/agents/${id}/session/history`,
    onSlashCommand: (cmd, args) => {
      if (cmd === "/compact") {
        api.post(`/agents/${id}/session/compact`, {}).then(() => {
          chat.setMessages([mkMsg("system", t("slashCommands.compactDone", { defaultValue: "Session kompaktiert." }))]);
        }).catch(() => {});
        return true;
      }
      if (cmd === "/model") {
        chat.setMessages(ms => [...ms, mkMsg("system", `Modell: ${agentModel.model ?? "unbekannt"}`)]);
        return true;
      }
      if (cmd === "/history") {
        chat.setShowHistory(h => !h);
        return true;
      }
      if (cmd === "/remember") {
        api.post(`/agents/${id}/memory`, { filename: "user-notes", content: args, mode: "append" }).catch(() => {});
        chat.setMessages(ms => [...ms, mkMsg("system", "Gespeichert.")]);
        return true;
      }
      if (cmd === "/retry") {
        const lastUser = [...chat.messages].reverse().find(m => m.role === "user");
        if (lastUser) chat.send(lastUser.content);
        return true;
      }
      return false;
    },
  });

  // Load agent info + history on mount
  useEffect(() => {
    if (!id) return;
    api.get<Record<string, unknown>>(`/agents/${id}`)
      .then(a => {
        const cfg = (a as any)?.config;
        if (cfg?.identity) setAgentName(cfg.identity);
        if (cfg?.llm) setAgentModel(cfg.llm);
      }).catch(() => {});
    chat.loadHistory();
  }, [id]);

  // Load sessions when history panel opens
  useEffect(() => {
    if (!chat.showHistory || !id) return;
    api.get<{ sessions: SessionPreview[] }>(`/agents/${id}/sessions?limit=30`)
      .then(d => chat.setSessions(d.sessions)).catch(() => {});
  }, [chat.showHistory, id]);

  return (
    <div className="flex flex-col h-full min-h-0 overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3 border-b flex-shrink-0">
        <button onClick={() => navigate("/agents")} className="p-1.5 rounded-md hover:bg-accent transition-colors">
          <ArrowLeft className="h-4 w-4" />
        </button>
        <div className="w-7 h-7 rounded-full bg-primary/10 flex items-center justify-center flex-shrink-0">
          <Bot className="h-4 w-4 text-primary" />
        </div>
        <div className="flex-1 min-w-0">
          <h1 className="text-sm font-semibold truncate">{agentName}</h1>
          <p className="text-xs text-muted-foreground font-mono">{agentModel.model ?? id}</p>
        </div>
        <button onClick={() => setShowDebug(d => !d)}
          className={`p-1.5 rounded-md transition-colors ${showDebug ? "bg-orange-500/15 text-orange-500" : "hover:bg-accent text-muted-foreground"}`}
          title="Debug-Konsole">
          <Bug className="h-4 w-4" />
        </button>
        <button onClick={() => { chat.setShowHistory(h => !h); chat.setViewSession(null); }}
          className={`p-1.5 rounded-md transition-colors ${chat.showHistory ? "bg-accent text-accent-foreground" : "hover:bg-accent text-muted-foreground"}`}
          title="Chat-Verlauf">
          <Clock className="h-4 w-4" />
        </button>
      </div>

      {/* Debug Console (#371) */}
      {showDebug && (
        <div className="border-b bg-[#0d0d0d] text-[#d4d4d4] max-h-48 overflow-y-auto px-4 py-3 font-mono text-xs space-y-1.5">
          <div className="flex items-center justify-between mb-2">
            <span className="flex items-center gap-1.5 text-orange-400 font-semibold"><Bug className="h-3 w-3" /> Debug-Konsole</span>
            <span className="text-muted-foreground">{chat.debugEvents.length} Events</span>
          </div>
          {chat.debugEvents.length === 0 && <span className="text-muted-foreground">Sende eine Nachricht um Debug-Events zu sehen...</span>}
          {chat.debugEvents.map((evt, i) => {
            const time = new Date(evt.ts).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
            if (evt.type === "context_info") {
              const d = evt.data as Record<string, number>;
              return (
                <div key={i} className="flex flex-wrap items-center gap-2">
                  <span className="text-muted-foreground w-16">{time}</span>
                  <Cpu className="h-3 w-3 text-blue-400" />
                  <span className="text-blue-400">Context:</span>
                  <span>System {(d.system_tokens ?? 0).toLocaleString()}</span><span className="text-muted-foreground">+</span>
                  <span>History {(d.history_tokens ?? 0).toLocaleString()}</span><span className="text-muted-foreground">+</span>
                  <span>Tools {(d.tool_tokens ?? 0).toLocaleString()}</span><span className="text-muted-foreground">=</span>
                  <span className="text-white font-medium">{((d.system_tokens ?? 0) + (d.history_tokens ?? 0) + (d.tool_tokens ?? 0)).toLocaleString()} Tokens</span>
                </div>
              );
            }
            if (evt.type === "tool_call") return (
              <div key={i} className="flex items-center gap-2">
                <span className="text-muted-foreground w-16">{time}</span>
                <Zap className="h-3 w-3 text-yellow-400" />
                <span className="text-yellow-400">{String((evt.data as any).tool_call)}</span>
                <span className="text-muted-foreground truncate max-w-[60%]">{String((evt.data as any).tool_detail || "")}</span>
              </div>
            );
            if (evt.type === "done") {
              const u = (evt.data as any).usage;
              return (
                <div key={i} className="flex flex-wrap items-center gap-2">
                  <span className="text-muted-foreground w-16">{time}</span>
                  <span className="text-green-400 font-medium">Done</span>
                  {u && <span>↑{u.input?.toLocaleString()} ↓{u.output?.toLocaleString()}</span>}
                  {u?.cache_read > 0 && <span className="text-green-500">{u.cache_read.toLocaleString()} cached</span>}
                  {(evt.data as any).is_fallback && <span className="text-orange-400">Fallback: {String((evt.data as any).model)}</span>}
                </div>
              );
            }
            if (evt.type === "error") return (
              <div key={i} className="flex items-center gap-2">
                <span className="text-muted-foreground w-16">{time}</span>
                <span className="text-red-400 font-medium">Error: {String((evt.data as any).error)}</span>
              </div>
            );
            return null;
          })}
        </div>
      )}

      {/* History Panel */}
      {chat.showHistory && (
        <div className="flex-1 overflow-y-auto border-b bg-muted/20">
          <div className="flex items-center justify-between px-4 py-2 border-b">
            <span className="text-xs font-medium text-muted-foreground">{t("chat.pastSessions", { defaultValue: "Vergangene Sessions" })}</span>
            <button onClick={() => { chat.setShowHistory(false); chat.setViewSession(null); }} className="p-1 rounded hover:bg-accent"><X className="h-3.5 w-3.5" /></button>
          </div>
          <div className="divide-y">
            {chat.sessions.map(s => (
              <button key={s.id} onClick={() => {
                api.get<{ id: string; messages: any[]; started_at: string }>(`/agents/${id}/sessions/${s.id}`)
                  .then(d => {
                    const msgs = d.messages.filter((m: any) => !(m.role === "assistant" && !m.content)).map((m: any) => mkMsg(m.role, m.content));
                    chat.setViewSession({ id: d.id, messages: msgs, startedAt: d.started_at });
                  }).catch(() => {});
              }}
                className="w-full text-left px-4 py-2 hover:bg-accent/50 text-xs">
                <div className="font-medium">{s.preview || "(leer)"}</div>
                <div className="text-muted-foreground">{s.message_count} Messages · {new Date(s.started_at).toLocaleDateString("de-DE")}</div>
              </button>
            ))}
          </div>
          {chat.viewSession && (
            <div className="border-t px-4 py-2 bg-muted/30">
              <button onClick={() => {
                api.post(`/agents/${id}/sessions/${chat.viewSession!.id}/resume`, {}).then(() => {
                  chat.loadHistory();
                  chat.setViewSession(null);
                  chat.setShowHistory(false);
                }).catch(() => {});
              }} className="text-xs text-primary hover:underline">{t("chat.resumeSession", { defaultValue: "Diese Session fortsetzen" })}</button>
            </div>
          )}
        </div>
      )}

      {/* Chat (hidden when viewing history) */}
      {!chat.showHistory && (
        <ChatView
          {...chat}
          t={t}
          slashCommands={SLASH_COMMANDS}
        />
      )}
    </div>
  );
}
