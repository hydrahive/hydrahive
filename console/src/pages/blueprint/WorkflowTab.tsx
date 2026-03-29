import { useEffect, useState } from "react";
import { Save, Loader2, Bot, ChevronDown, ChevronRight } from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

interface AgentEntry {
  id: string;
  identity: string;
  tools: string[];
}

// All known tools with descriptions
const ALL_TOOLS: { id: string; label: string; description: string; category: string }[] = [
  { id: "file_read",      label: "file_read",      description: "Dateien lesen",                      category: "Dateisystem" },
  { id: "file_write",     label: "file_write",     description: "Dateien schreiben",                   category: "Dateisystem" },
  { id: "shell_exec",     label: "shell_exec",     description: "Shell-Befehle ausführen",             category: "System" },
  { id: "web_search",     label: "web_search",     description: "Web-Suche",                           category: "Web" },
  { id: "web_fetch",      label: "web_fetch",      description: "Webseiten abrufen",                   category: "Web" },
  { id: "read_memory",    label: "read_memory",    description: "Agent-Memory lesen",                  category: "Memory" },
  { id: "write_memory",   label: "write_memory",   description: "Agent-Memory schreiben",              category: "Memory" },
  { id: "create_skill",   label: "create_skill",   description: "Skills erstellen/speichern",          category: "Skills" },
  { id: "list_skills",    label: "list_skills",    description: "Skills auflisten",                    category: "Skills" },
  { id: "delete_skill",   label: "delete_skill",   description: "Skills löschen",                      category: "Skills" },
  { id: "read_handoff",   label: "read_handoff",   description: "AgentLink Handoff lesen",             category: "AgentLink" },
  { id: "write_handoff",  label: "write_handoff",  description: "AgentLink Handoff schreiben",         category: "AgentLink" },
  { id: "send_message",   label: "send_message",   description: "Nachricht an User senden",            category: "Kommunikation" },
  { id: "http_request",   label: "http_request",   description: "HTTP-Anfragen senden",                category: "Web" },
  { id: "git_commit",     label: "git_commit",     description: "Git-Commit erstellen",                category: "Git" },
  { id: "git_push",       label: "git_push",       description: "Git-Push ausführen",                  category: "Git" },
  { id: "schedule_task",  label: "schedule_task",  description: "Aufgaben zeitgesteuert planen",       category: "System" },
];

const CATEGORIES = [...new Set(ALL_TOOLS.map(t => t.category))];

export function WorkflowTab() {
  const [agents,  setAgents]  = useState<AgentEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving,  setSaving]  = useState<Record<string, boolean>>({});
  const [toast,   setToast]   = useState<string | null>(null);
  const [local,   setLocal]   = useState<Record<string, string[]>>({});
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [catOpen, setCatOpen] = useState<Record<string, boolean>>(() =>
    Object.fromEntries(CATEGORIES.map(c => [c, true]))
  );

  useEffect(() => {
    api.get<Record<string, any>>("/agents")
      .then(ad => {
        const entries = Object.entries(ad)
          .filter(([id]) => !id.startsWith("personal_"))
          .map(([id, v]: [string, any]) => ({
            id,
            identity: v.config?.identity || id,
            tools: v.config?.tools || [],
          }));
        setAgents(entries);
        const init: Record<string, string[]> = {};
        entries.forEach(a => { init[a.id] = [...a.tools]; });
        setLocal(init);
      })
      .finally(() => setLoading(false));
  }, []);

  function toggleTool(agentId: string, toolId: string) {
    setLocal(prev => {
      const cur = prev[agentId] ?? [];
      return {
        ...prev,
        [agentId]: cur.includes(toolId) ? cur.filter(t => t !== toolId) : [...cur, toolId],
      };
    });
  }

  async function saveAgent(agentId: string) {
    setSaving(s => ({ ...s, [agentId]: true }));
    try {
      await api.updateAgent(agentId, { tools: local[agentId] });
      setToast(`${agentId} gespeichert`);
      setTimeout(() => setToast(null), 2500);
    } catch (e) {
      setToast(e instanceof Error ? e.message : "Fehler");
    } finally {
      setSaving(s => ({ ...s, [agentId]: false }));
    }
  }

  if (loading) return <div className="flex items-center justify-center h-full"><Loader2 className="h-8 w-8 animate-spin text-white/30" /></div>;

  return (
    <div className="flex flex-col h-full overflow-y-auto">
      <div className="flex items-center justify-between px-6 py-3 border-b border-white/10 shrink-0">
        <p className="text-xs text-white/40">Tool-Berechtigungen pro Agent konfigurieren.</p>
        {toast && <span className="text-sm text-indigo-300">{toast}</span>}
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {agents.map(agent => {
          const agentTools = local[agent.id] ?? [];
          const isOpen = !collapsed[agent.id];
          const isSaving = saving[agent.id];

          return (
            <div key={agent.id} className="rounded-xl border border-white/10 bg-zinc-900/60 overflow-hidden">
              {/* Header */}
              <div className="flex items-center justify-between px-4 py-3 bg-zinc-900 border-b border-white/10 cursor-pointer"
                onClick={() => setCollapsed(c => ({ ...c, [agent.id]: !c[agent.id] }))}>
                <div className="flex items-center gap-2">
                  {isOpen ? <ChevronDown className="h-4 w-4 text-white/40" /> : <ChevronRight className="h-4 w-4 text-white/40" />}
                  <Bot className="h-4 w-4 text-blue-400" />
                  <span className="font-medium text-white text-sm">{agent.identity}</span>
                  <span className="text-[0.65rem] text-white/30">{agentTools.length} Tools aktiv</span>
                </div>
                {isOpen && (
                  <button onClick={e => { e.stopPropagation(); saveAgent(agent.id); }}
                    disabled={isSaving}
                    className="flex items-center gap-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 px-3 py-1.5 text-xs text-white transition-colors">
                    {isSaving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />}
                    {isSaving ? "Speichere…" : "Speichern"}
                  </button>
                )}
              </div>

              {isOpen && (
                <div className="p-3 space-y-3">
                  {CATEGORIES.map(cat => {
                    const catTools = ALL_TOOLS.filter(t => t.category === cat);
                    const isCatOpen = catOpen[cat] !== false;
                    return (
                      <div key={cat}>
                        <button
                          onClick={() => setCatOpen(c => ({ ...c, [cat]: !isCatOpen }))}
                          className="flex items-center gap-1.5 mb-1.5 text-[0.65rem] font-bold uppercase tracking-widest text-white/30 hover:text-white/50 transition-colors">
                          {isCatOpen ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
                          {cat}
                        </button>
                        {isCatOpen && (
                          <div className="grid grid-cols-2 gap-1 pl-4">
                            {catTools.map(tool => (
                              <label key={tool.id} className="flex items-start gap-2 cursor-pointer group rounded-lg px-2 py-1.5 hover:bg-white/5 transition-colors">
                                <input type="checkbox"
                                  checked={agentTools.includes(tool.id)}
                                  onChange={() => toggleTool(agent.id, tool.id)}
                                  className="mt-0.5 rounded border-white/20 bg-zinc-800 accent-indigo-500 shrink-0" />
                                <div>
                                  <p className={cn("text-xs font-mono", agentTools.includes(tool.id) ? "text-white" : "text-white/40 group-hover:text-white/60")}>{tool.label}</p>
                                  <p className="text-[0.6rem] text-white/25">{tool.description}</p>
                                </div>
                              </label>
                            ))}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
        {agents.length === 0 && (
          <p className="text-white/25 text-sm text-center mt-20">Keine Agenten vorhanden</p>
        )}
      </div>
    </div>
  );
}
