/**
 * AgentChatPage — Agent-Chat mit Debug-Konsole (#491 refactored)
 *
 * Nutzt shared ChatView + useChatStream Hook.
 * Page-spezifisch: Agent-Header, Debug-Panel, History-Panel, Info-Sidebar.
 */
import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { ArrowLeft, Bot, Clock, Bug, Zap, Cpu, X, Sparkles, Terminal, PanelRightClose, PanelRightOpen } from "lucide-react";
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
  const [agentModel, setAgentModel] = useState<{ model?: string; temperature?: number }>({});
  const [agentTools, setAgentTools] = useState<string[]>([]);

  // Debug
  const [showDebug, setShowDebug] = useState(false);
  const [showSidebar, setShowSidebar] = useState(true);

  // Slash commands
  const SLASH_COMMANDS = [
    { cmd: "/help",          desc: t("slashCommands.help") },
    { cmd: "/clear",         desc: t("slashCommands.clear") },
    { cmd: "/compact",       desc: t("slashCommands.compact") },
    { cmd: "/model",         desc: t("slashCommands.model") },
    { cmd: "/retry",         desc: t("slashCommands.retry") },
    { cmd: "/remember",      desc: t("slashCommands.remember") },
    { cmd: "/history",       desc: t("slashCommands.history") },
    { cmd: "/skill list",    desc: "Installierte + verfügbare Skills anzeigen" },
    { cmd: "/skill install", desc: "Skill aus Catalog installieren: /skill install <name>" },
    { cmd: "/skill run",     desc: "Skill einmalig in nächste Nachricht injizieren: /skill run <name>" },
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
      // #658: Skill-Bedienoberfläche
      //   /skill list                     — installierte + Catalog-Skills anzeigen
      //   /skill install <name>           — aus curated Catalog installieren
      //   /skill run <name>               — CLIENT-SIDE one-shot context injection:
      //                                     Skill-Body wird in den Composer geprependet;
      //                                     NICHT persistent, NICHT im Backend-State.
      //                                     Input wird beim nächsten Submit automatisch gelöscht.
      if (cmd === "/skill") {
        const [sub, ...rest] = (args || "").trim().split(/\s+/);
        const name = rest.join(" ").trim();
        if (sub === "list") {
          (async () => {
            try {
              const [installed, catalog] = await Promise.all([
                api.get<{ skills: Array<{ filename?: string; skill?: string; scope?: string }> }>(`/agents/${id}/skills`),
                api.get<{ skills: Array<{ name: string; skill?: string; scope?: string; description?: string }>; errors: Array<{ name: string; error: string }> }>(`/skills/catalog`),
              ]);
              const inst = installed.skills?.map(s => `• ${s.skill || s.filename} (${s.scope || "on-demand"})`).join("\n") || "— keine —";
              const cat  = catalog.skills?.map(s => `• ${s.name} — ${s.description || s.skill || ""}`).join("\n") || "— Catalog leer —";
              const errs = catalog.errors?.length
                ? `\n\n⚠ Catalog-Fehler:\n${catalog.errors.map(e => `• ${e.name}: ${e.error}`).join("\n")}` : "";
              chat.setMessages(ms => [...ms, mkMsg("system",
                `Installierte Skills:\n${inst}\n\nVerfügbar im Catalog:\n${cat}${errs}`,
              )]);
            } catch (e: any) {
              chat.setMessages(ms => [...ms, mkMsg("system", `Skill-Liste nicht abrufbar: ${e?.message || e}`)]);
            }
          })();
          return true;
        }
        if (sub === "install") {
          if (!name) {
            chat.setMessages(ms => [...ms, mkMsg("system", "Nutzung: /skill install <name>")]);
            return true;
          }
          (async () => {
            try {
              await api.post(`/agents/${id}/skills/install`, { source: "catalog", name });
              chat.setMessages(ms => [...ms, mkMsg("system", `Skill '${name}' installiert.`)]);
            } catch (e: any) {
              chat.setMessages(ms => [...ms, mkMsg("system", `Install fehlgeschlagen: ${e?.message || e}`)]);
            }
          })();
          return true;
        }
        if (sub === "run") {
          // Option B: "<name>" allein → Skill in Composer laden.
          //           "<name> <frage>" → Skill-Prefix + Frage direkt senden.
          // Client-side one-shot: kein Backend-State, keine persistente Aktivierung.
          const runName = rest[0]?.trim() || "";
          const question = rest.slice(1).join(" ").trim();
          if (!runName) {
            chat.setMessages(ms => [...ms, mkMsg("system", "Nutzung: /skill run <name> [frage]")]);
            return true;
          }
          (async () => {
            try {
              const list = await api.get<{ skills: Array<{ filename?: string; skill?: string; content?: string }> }>(`/agents/${id}/skills`);
              const skill = list.skills?.find(s => s.filename === runName || s.skill === runName);
              if (!skill) {
                chat.setMessages(ms => [...ms, mkMsg("system", `Skill '${runName}' nicht installiert. Erst '/skill install ${runName}' ausführen.`)]);
                return;
              }
              const body = skill.content || "";
              const prefix = `[Active skill: ${runName}]\n${body}\n---\n`;
              if (question) {
                chat.send(prefix + question);
              } else {
                chat.setInput(prefix);
                chat.setMessages(ms => [...ms, mkMsg("system",
                  `Skill "${runName}" wurde in das Eingabefeld geladen. Ergänze deine Frage und sende die Nachricht.`,
                )]);
              }
            } catch (e: any) {
              chat.setMessages(ms => [...ms, mkMsg("system", `Skill-Laden fehlgeschlagen: ${e?.message || e}`)]);
            }
          })();
          return true;
        }
        chat.setMessages(ms => [...ms, mkMsg("system", "Unbekannter /skill-Subcommand. Verfügbar: list, install <name>, run <name>.")]);
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
        if (cfg?.tools && Array.isArray(cfg.tools)) setAgentTools(cfg.tools.map((t: any) => typeof t === "string" ? t : t.name ?? ""));
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
    <section className="flex h-full min-h-0 overflow-hidden">
      {/* Main Column */}
      <div className="flex flex-1 flex-col min-h-0 min-w-0 overflow-hidden">
        {/* Header */}
        <div className="flex items-center gap-3 px-4 py-3 border-b flex-shrink-0">
          <button onClick={() => navigate("/projects")} className="p-1.5 rounded-md hover:bg-accent transition-colors">
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
          <button onClick={() => setShowSidebar(s => !s)}
            className={`hidden lg:block p-1.5 rounded-md transition-colors ${showSidebar ? "bg-accent text-accent-foreground" : "hover:bg-accent text-muted-foreground"}`}
            title="Info-Panel">
            {showSidebar ? <PanelRightClose className="h-4 w-4" /> : <PanelRightOpen className="h-4 w-4" />}
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
            onConfirmTool={async (toolCallId, decision) => {
              // #641-Followup: Agent-Endpoint, gleicher Pending-Store wie bei
              // ChatPage. session_id aus pendingConfirms-Eintrag, Fallback
              // auf currentSessionId.
              const pc = chat.pendingConfirms.find(p => p.tool_call_id === toolCallId);
              const sid = pc?.session_id || chat.currentSessionId;
              if (!id || !sid) {
                console.warn("[#641] confirmTool: agent_id oder session_id fehlt");
                return;
              }
              try {
                await api.confirmToolCallAgent(id, sid, toolCallId, decision);
                chat.removePendingConfirm(toolCallId);
              } catch (err) {
                const msg = err instanceof Error
                  ? err.message
                  : (typeof err === "string" ? err : JSON.stringify(err));
                console.error(`[#641] confirmToolAgent(${decision}, ${toolCallId}) failed: ${msg}`);
                chat.setError(`Tool-Bestätigung fehlgeschlagen: ${msg}`);
              }
            }}
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

            {/* Agent-Info */}
            <div className="rounded-2xl border bg-background/75 p-4">
              <p className="text-[0.65rem] uppercase tracking-[0.16em] text-muted-foreground">Agent</p>
              <div className="mt-3 space-y-3 text-sm">
                <div className="rounded-2xl bg-secondary/40 px-3 py-3">
                  <p className="text-[0.65rem] uppercase tracking-[0.16em] text-muted-foreground">Name</p>
                  <p className="mt-1 font-medium">{agentName}</p>
                  <p className="font-mono text-xs text-muted-foreground">{id}</p>
                </div>
                <div className="rounded-2xl bg-secondary/40 px-3 py-3">
                  <p className="text-[0.65rem] uppercase tracking-[0.16em] text-muted-foreground">{t("chat.bossModel", { defaultValue: "Modell" })}</p>
                  <p className="mt-1 break-all font-medium">{agentModel.model ?? t("chat.notConfigured", { defaultValue: "Nicht konfiguriert" })}</p>
                  <p className="text-xs text-muted-foreground">Temp: {agentModel.temperature ?? "—"}</p>
                </div>
                <div className="rounded-2xl bg-secondary/40 px-3 py-3">
                  <p className="text-[0.65rem] uppercase tracking-[0.16em] text-muted-foreground">{t("chat.history", { defaultValue: "Verlauf" })}</p>
                  <p className="mt-1 font-medium">{chat.messages.length} {chat.messages.length === 1 ? "Nachricht" : "Nachrichten"}</p>
                </div>
              </div>
            </div>

            {/* Tools */}
            {agentTools.length > 0 && (
              <div className="rounded-2xl border bg-background/75 p-4">
                <p className="text-[0.65rem] uppercase tracking-[0.16em] text-muted-foreground">Tools ({agentTools.length})</p>
                <div className="mt-3 flex flex-wrap gap-1.5">
                  {agentTools.map(tool => (
                    <span key={tool} className="inline-flex items-center gap-1 rounded-lg border border-primary/20 bg-primary/5 px-2 py-0.5 text-[10px] text-primary/70 font-mono">
                      <Terminal className="h-2.5 w-2.5" />
                      {tool}
                    </span>
                  ))}
                </div>
              </div>
            )}

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
    </section>
  );
}
