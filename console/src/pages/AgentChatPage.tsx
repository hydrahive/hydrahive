/**
 * AgentChatPage — Agent-Chat mit Debug-Konsole (#491 refactored)
 *
 * Nutzt shared ChatView + useChatStream Hook.
 * Page-spezifisch: Agent-Header, Debug-Panel, History-Panel, Info-Sidebar.
 */
import { useEffect, useMemo, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { ArrowLeft, Bot, Clock, Bug, Zap, Cpu, X, Sparkles, Terminal, PanelRightClose, PanelRightOpen } from "lucide-react";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import { ChatShell } from "@/components/chat-v2/ChatShell";
import { buildChatV2Target, useHydraHiveRuntime } from "@/components/chat-v2/hydrahive-runtime";

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

  const target = useMemo(() => buildChatV2Target("agent", id ?? ""), [id]);
  async function handleSlashCommand(cmd: string, args: string): Promise<boolean> {
    if (cmd === "/compact") {
      try {
        await api.post(`/agents/${id}/session/compact`, {});
        runtime.resetMessages([]);
        runtime.pushSystemMessage(t("slashCommands.compactDone", { defaultValue: "Session kompaktiert." }));
      } catch (e) {
        runtime.pushSystemMessage(`Compact fehlgeschlagen: ${e instanceof Error ? e.message : String(e)}`);
      }
      return true;
    }
    if (cmd === "/model") {
      runtime.pushSystemMessage(`Modell: ${agentModel.model ?? "unbekannt"}`);
      return true;
    }
    if (cmd === "/history") {
      runtime.toggleHistory();
      return true;
    }
    if (cmd === "/remember") {
      try {
        await api.post(`/agents/${id}/memory`, { filename: "user-notes", content: args, mode: "append" });
        runtime.pushSystemMessage("Gespeichert.");
      } catch (e) {
        runtime.pushSystemMessage(`Speichern fehlgeschlagen: ${e instanceof Error ? e.message : String(e)}`);
      }
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
    // #658: Skill-Bedienoberfläche
    if (cmd === "/skill") {
      const [sub, ...rest] = (args || "").trim().split(/\s+/);
      const name = rest.join(" ").trim();
      if (sub === "list") {
        // #659: Multi-Layer-View (effective, available, errors).
        type ShadowOrigin = { source: string; skill?: string; scope?: string };
        type EffectiveSkill = { name: string; effective: ShadowOrigin; shadows?: ShadowOrigin[] };
        type AvailableSkill = { name: string; source: string; skill?: string; scope?: string };
        try {
          const layered = await api.get<{
            skills: EffectiveSkill[];
            available: AvailableSkill[];
            errors?: Array<{ name: string; source: string; error: string }>;
          }>(`/agents/${id}/skills?layers=all`);
          const groups: Record<string, string[]> = { agent: [], project: [], user: [] };
          (layered.skills || []).forEach(s => {
            const src = s.effective.source;
            const scope = s.effective.scope || "on-demand";
            const shadowSrcs = (s.shadows || []).map(x => x.source);
            const suffix = shadowSrcs.length ? ` [shadows: ${shadowSrcs.join(", ")}]` : "";
            if (groups[src]) groups[src].push(`• ${s.name} (${scope})${suffix}`);
          });
          const effLines = (label: string, key: string) =>
            groups[key].length ? `${label}:\n${groups[key].join("\n")}` : "";
          const effective = [
            effLines("Effective — agent", "agent"),
            effLines("Effective — project", "project"),
            effLines("Effective — user", "user"),
          ].filter(Boolean).join("\n\n") || "— keine installierten Skills —";
          const availLines = (layered.available || [])
            .map(a => `• ${a.name} (${a.scope || "on-demand"})`).join("\n");
          const catSection = availLines
            ? `\n\nCatalog / available (nicht automatisch aktiv — '/skill install <name>'):\n${availLines}`
            : "";
          const resolverErrs = (layered.errors || []).length
            ? `\n\n⚠ Resolver-Fehler:\n${layered.errors!.map(e => `• ${e.name} (${e.source}): ${e.error}`).join("\n")}` : "";
          runtime.pushSystemMessage(`${effective}${catSection}${resolverErrs}`);
        } catch (e) {
          runtime.pushSystemMessage(`Skill-Liste nicht abrufbar: ${e instanceof Error ? e.message : String(e)}`);
        }
        return true;
      }
      if (sub === "install") {
        if (!name) { runtime.pushSystemMessage("Nutzung: /skill install <name>"); return true; }
        try {
          await api.post(`/agents/${id}/skills/install`, { source: "catalog", name });
          runtime.pushSystemMessage(`Skill '${name}' installiert.`);
        } catch (e) {
          runtime.pushSystemMessage(`Install fehlgeschlagen: ${e instanceof Error ? e.message : String(e)}`);
        }
        return true;
      }
      if (sub === "run") {
        // Client-side one-shot: kein Backend-State, keine persistente Aktivierung.
        const runName = rest[0]?.trim() || "";
        const question = rest.slice(1).join(" ").trim();
        if (!runName) { runtime.pushSystemMessage("Nutzung: /skill run <name> [frage]"); return true; }
        try {
          type EffectiveItem = { name: string; effective: { source: string }; content?: string };
          type AvailableItem = { name: string };
          const list = await api.get<{ skills: EffectiveItem[]; available: AvailableItem[] }>(
            `/agents/${id}/skills?layers=all`
          );
          const hit = list.skills?.find(s => s.name === runName);
          if (hit) {
            const body = hit.content || "";
            const prefix = `[Active skill: ${runName}]\n${body}\n---\n`;
            if (question) {
              void runtime.sendText(prefix + question);
            } else {
              runtime.aui.composer().setText(prefix);
              runtime.pushSystemMessage(
                `Skill "${runName}" wurde in das Eingabefeld geladen. Ergänze deine Frage und sende die Nachricht.`
              );
            }
            return true;
          }
          const avail = list.available?.find(a => a.name === runName);
          if (avail) {
            runtime.pushSystemMessage(
              `Skill '${runName}' ist nur im Catalog. Erst '/skill install ${runName}' ausführen.`
            );
            return true;
          }
          runtime.pushSystemMessage(
            `Skill '${runName}' nicht gefunden. Mit '/skill install <name>' aus dem Catalog installieren.`
          );
        } catch (e) {
          runtime.pushSystemMessage(`Skill-Laden fehlgeschlagen: ${e instanceof Error ? e.message : String(e)}`);
        }
        return true;
      }
      runtime.pushSystemMessage("Unbekannter /skill-Subcommand. Verfügbar: list, install <name>, run <name>.");
      return true;
    }
    return false;
  }
  const runtime = useHydraHiveRuntime(target, { onSlashCommand: handleSlashCommand });

  // Load agent info on mount (history/sessions laden über runtime selbst).
  useEffect(() => {
    if (!id) return;
    api.get<Record<string, unknown>>(`/agents/${id}`)
      .then(a => {
        const cfg = (a as any)?.config;
        if (cfg?.identity) setAgentName(cfg.identity);
        if (cfg?.llm) setAgentModel(cfg.llm);
        if (cfg?.tools && Array.isArray(cfg.tools)) setAgentTools(cfg.tools.map((t: any) => typeof t === "string" ? t : t.name ?? ""));
      }).catch(() => {});
  }, [id]);

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
          <button onClick={runtime.toggleHistory}
            className={`p-1.5 rounded-md transition-colors ${runtime.showHistory ? "bg-accent text-accent-foreground" : "hover:bg-accent text-muted-foreground"}`}
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
              <span className="text-muted-foreground">{runtime.debugEvents.length} Events</span>
            </div>
            {runtime.debugEvents.length === 0 && <span className="text-muted-foreground">Sende eine Nachricht um Debug-Events zu sehen...</span>}
            {runtime.debugEvents.map((evt, i) => {
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
          <ChatShell runtime={runtime} hideHeader />
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
                  <p className="mt-1 font-medium">{runtime.messages.length} {runtime.messages.length === 1 ? "Nachricht" : "Nachrichten"}</p>
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
