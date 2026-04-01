import "@xyflow/react/dist/style.css";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ReactFlow, ReactFlowProvider, Background, Controls, MiniMap,
  addEdge, useNodesState, useEdgesState, useReactFlow,
  Handle, Position, BackgroundVariant, Panel,
  type Connection, type Edge, type Node,
} from "@xyflow/react";
import {
  GitBranch, KeyRound, BookOpen, Brain, Shield, Bot, Save, Loader2, X,
  Wrench, Server, Puzzle, Cpu, PlusCircle, Rocket, Sparkles, ChevronDown,
} from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useTranslation } from "react-i18next";

// ── Node-Typen ────────────────────────────────────────────────────────────────

function RepoNode({ data, selected }: { data: any; selected: boolean }) {
  return (
    <div className={cn("min-w-[180px] max-w-[240px] rounded-xl border-2 px-3 py-2.5 shadow-lg select-none bg-blue-950/60 border-blue-500/60", selected && "ring-2 ring-white/25")}>
      <div className="flex items-center gap-1.5 mb-1">
        <GitBranch className="h-3 w-3 text-blue-400" />
        <span className="text-[0.55rem] font-bold uppercase tracking-widest text-blue-400">Repository</span>
      </div>
      <p className="text-sm font-medium text-white leading-tight">{data.label || "Repo"}</p>
      {data.config?.url && <p className="text-[0.6rem] text-blue-400/60 mt-0.5 font-mono truncate">{data.config.url}</p>}
      <Handle type="source" position={Position.Right} id="out" style={{ background: "#60a5fa", border: "2px solid #1d4ed8", width: 10, height: 10 }} />
    </div>
  );
}

function CredentialNode({ data, selected }: { data: any; selected: boolean }) {
  return (
    <div className={cn("min-w-[160px] max-w-[220px] rounded-xl border-2 px-3 py-2.5 shadow-lg select-none bg-orange-950/60 border-orange-500/60", selected && "ring-2 ring-white/25")}>
      <div className="flex items-center gap-1.5 mb-1">
        <KeyRound className="h-3 w-3 text-orange-400" />
        <span className="text-[0.55rem] font-bold uppercase tracking-widest text-orange-400">Credential</span>
      </div>
      <p className="text-sm font-medium text-white leading-tight">{data.label || "Token"}</p>
      {data.config?.key && <p className="text-[0.6rem] text-orange-400/60 mt-0.5 font-mono">{data.config.key}</p>}
      <Handle type="source" position={Position.Right} id="out" style={{ background: "#fb923c", border: "2px solid #9a3412", width: 10, height: 10 }} />
    </div>
  );
}

function SkillNode({ data, selected }: { data: any; selected: boolean }) {
  return (
    <div className={cn("min-w-[160px] max-w-[220px] rounded-xl border-2 px-3 py-2.5 shadow-lg select-none bg-purple-950/60 border-purple-500/60", selected && "ring-2 ring-white/25")}>
      <div className="flex items-center gap-1.5 mb-1">
        <BookOpen className="h-3 w-3 text-purple-400" />
        <span className="text-[0.55rem] font-bold uppercase tracking-widest text-purple-400">Skill</span>
      </div>
      <p className="text-sm font-medium text-white leading-tight">{data.label || "Skill"}</p>
      {data.config?.file && <p className="text-[0.6rem] text-purple-400/60 mt-0.5 font-mono">{data.config.file}</p>}
      <Handle type="source" position={Position.Right} id="out" style={{ background: "#c084fc", border: "2px solid #6b21a8", width: 10, height: 10 }} />
    </div>
  );
}

function MemoryNode({ data, selected }: { data: any; selected: boolean }) {
  return (
    <div className={cn("min-w-[160px] max-w-[220px] rounded-xl border-2 px-3 py-2.5 shadow-lg select-none bg-teal-950/60 border-teal-500/60", selected && "ring-2 ring-white/25")}>
      <div className="flex items-center gap-1.5 mb-1">
        <Brain className="h-3 w-3 text-teal-400" />
        <span className="text-[0.55rem] font-bold uppercase tracking-widest text-teal-400">Memory</span>
      </div>
      <p className="text-sm font-medium text-white leading-tight">{data.label || "Memory"}</p>
      {data.config?.file && <p className="text-[0.6rem] text-teal-400/60 mt-0.5 font-mono">{data.config.file}</p>}
      <Handle type="source" position={Position.Right} id="out" style={{ background: "#2dd4bf", border: "2px solid #0f766e", width: 10, height: 10 }} />
    </div>
  );
}

function ToolPolicyNode({ data, selected }: { data: any; selected: boolean }) {
  const allowed = data.config?.allowed !== false;
  return (
    <div className={cn("min-w-[160px] max-w-[220px] rounded-xl border-2 px-3 py-2.5 shadow-lg select-none", selected && "ring-2 ring-white/25",
      allowed ? "bg-green-950/60 border-green-500/60" : "bg-red-950/60 border-red-500/60")}>
      <div className="flex items-center gap-1.5 mb-1">
        <Shield className={cn("h-3 w-3", allowed ? "text-green-400" : "text-red-400")} />
        <span className={cn("text-[0.55rem] font-bold uppercase tracking-widest", allowed ? "text-green-400" : "text-red-400")}>
          Tool {allowed ? "✓" : "✗"}
        </span>
      </div>
      <p className="text-sm font-medium text-white leading-tight">{data.label || "Tool Policy"}</p>
      {data.config?.tool && <p className={cn("text-[0.6rem] mt-0.5 font-mono", allowed ? "text-green-400/60" : "text-red-400/60")}>{data.config.tool}</p>}
      <Handle type="source" position={Position.Right} id="out" style={{ background: allowed ? "#4ade80" : "#f87171", border: "2px solid #166534", width: 10, height: 10 }} />
    </div>
  );
}

function ToolNode({ data, selected }: { data: any; selected: boolean }) {
  return (
    <div className={cn("min-w-[140px] max-w-[200px] rounded-xl border-2 px-3 py-2 shadow-lg select-none bg-cyan-950/60 border-cyan-500/60", selected && "ring-2 ring-white/25")}>
      <div className="flex items-center gap-1.5 mb-0.5">
        <Wrench className="h-3 w-3 text-cyan-400" />
        <span className="text-[0.55rem] font-bold uppercase tracking-widest text-cyan-400">Tool</span>
      </div>
      <p className="text-xs font-medium text-white leading-tight font-mono">{data.label || "tool"}</p>
      <Handle type="source" position={Position.Right} id="out" style={{ background: "#22d3ee", border: "2px solid #0e7490", width: 10, height: 10 }} />
    </div>
  );
}

function McpNode({ data, selected }: { data: any; selected: boolean }) {
  return (
    <div className={cn("min-w-[160px] max-w-[220px] rounded-xl border-2 px-3 py-2.5 shadow-lg select-none bg-pink-950/60 border-pink-500/60", selected && "ring-2 ring-white/25")}>
      <div className="flex items-center gap-1.5 mb-1">
        <Server className="h-3 w-3 text-pink-400" />
        <span className="text-[0.55rem] font-bold uppercase tracking-widest text-pink-400">MCP Server</span>
      </div>
      <p className="text-sm font-medium text-white leading-tight">{data.label || "MCP"}</p>
      <Handle type="source" position={Position.Right} id="out" style={{ background: "#f472b6", border: "2px solid #9d174d", width: 10, height: 10 }} />
    </div>
  );
}

function PluginNode({ data, selected }: { data: any; selected: boolean }) {
  return (
    <div className={cn("min-w-[160px] max-w-[220px] rounded-xl border-2 px-3 py-2.5 shadow-lg select-none bg-amber-950/60 border-amber-500/60", selected && "ring-2 ring-white/25")}>
      <div className="flex items-center gap-1.5 mb-1">
        <Puzzle className="h-3 w-3 text-amber-400" />
        <span className="text-[0.55rem] font-bold uppercase tracking-widest text-amber-400">Plugin</span>
      </div>
      <p className="text-sm font-medium text-white leading-tight">{data.label || "Plugin"}</p>
      <Handle type="source" position={Position.Right} id="out" style={{ background: "#fbbf24", border: "2px solid #92400e", width: 10, height: 10 }} />
    </div>
  );
}

function AgentProfileNode({ data, selected }: { data: any; selected: boolean }) {
  const isNew = data.config?.isNew;
  return (
    <div className={cn("min-w-[220px] rounded-xl border-2 px-3 py-2.5 shadow-lg select-none",
      isNew ? "bg-indigo-950/70 border-indigo-400/60" : "bg-zinc-800/80 border-zinc-400/40",
      selected && "ring-2 ring-white/25")}>
      <div className="flex items-center gap-1.5 mb-1">
        <Bot className={cn("h-3 w-3", isNew ? "text-indigo-300" : "text-zinc-300")} />
        <span className={cn("text-[0.55rem] font-bold uppercase tracking-widest", isNew ? "text-indigo-400" : "text-zinc-400")}>
          {isNew ? "Neuer Agent" : "Agent"}
        </span>
      </div>
      <p className="text-sm font-medium text-white leading-tight">{data.label || "Agent"}</p>
      {data.config?.model && <p className="text-[0.6rem] text-indigo-400/50 mt-0.5 font-mono">{data.config.model}</p>}
      {data.config?.type && <p className="text-[0.55rem] text-white/30 mt-0.5">{data.config.type}</p>}
      <Handle type="target" position={Position.Left} id="in" style={{ background: isNew ? "#818cf8" : "#a1a1aa", border: "2px solid #52525b", width: 10, height: 10 }} />
    </div>
  );
}

const NODE_TYPES = {
  repository:   RepoNode         as any,
  credential:   CredentialNode   as any,
  skill:        SkillNode        as any,
  memory:       MemoryNode       as any,
  toolpolicy:   ToolPolicyNode   as any,
  tool:         ToolNode         as any,
  mcp:          McpNode          as any,
  plugin:       PluginNode       as any,
  agentprofile: AgentProfileNode as any,
};

// ── Palette ───────────────────────────────────────────────────────────────────

const PALETTE_ITEMS = [
  { type: "tool",         label: "Tool",         icon: Wrench,    color: "text-cyan-400" },
  { type: "skill",        label: "Skill",        icon: BookOpen,  color: "text-purple-400" },
  { type: "memory",       label: "Memory",       icon: Brain,     color: "text-teal-400" },
  { type: "mcp",          label: "MCP Server",   icon: Server,    color: "text-pink-400" },
  { type: "plugin",       label: "Plugin",       icon: Puzzle,    color: "text-amber-400" },
  { type: "repository",   label: "Repository",   icon: GitBranch, color: "text-blue-400" },
  { type: "credential",   label: "Credential",   icon: KeyRound,  color: "text-orange-400" },
  { type: "toolpolicy",   label: "Tool-Policy",  icon: Shield,    color: "text-green-400" },
];

const MODELS = [
  "claude-sonnet-4-6",
  "claude-opus-4-6",
  "claude-haiku-4-5-20251001",
  "gpt-4.1",
  "gpt-4.1-mini",
  "ollama/llama3.3",
  "ollama/qwen3",
  "ollama/gemma3",
];

// ── Properties Panel ──────────────────────────────────────────────────────────

function PropertiesPanel({ node, onChange, onDelete, availableTools, availableMcp, availablePlugins }: {
  node: Node | null;
  onChange: (id: string, data: any) => void;
  onDelete: (id: string) => void;
  availableTools: string[];
  availableMcp: string[];
  availablePlugins: string[];
}) {
  if (!node) return (
    <div className="flex items-center justify-center h-full text-white/20 text-xs p-4 text-center">
      Node auswählen um Eigenschaften zu bearbeiten
    </div>
  );

  const n   = node;
  const d   = n.data as any;
  const cfg = d.config || {};
  const upd    = (patch: any) => onChange(n.id, { ...d, ...patch });
  const updCfg = (patch: any) => upd({ config: { ...cfg, ...patch } });

  return (
    <div className="p-3 space-y-3 overflow-y-auto">
      <div className="flex items-center justify-between">
        <span className="text-xs font-bold text-white/40 uppercase tracking-wider">{n.type}</span>
        <button onClick={() => onDelete(n.id)} className="p-1 rounded text-red-400 hover:bg-red-500/15 transition-colors"><X className="h-3.5 w-3.5" /></button>
      </div>

      <div>
        <label className="block text-[0.65rem] text-white/40 mb-1">Bezeichnung</label>
        <input value={d.label || ""} onChange={e => upd({ label: e.target.value })}
          className="w-full rounded-lg bg-zinc-800 border border-white/10 px-2.5 py-1.5 text-sm text-white focus:outline-none focus:border-indigo-500/60" />
      </div>

      {/* Agent-Profil Properties */}
      {n.type === "agentprofile" && <>
        <div>
          <label className="block text-[0.65rem] text-white/40 mb-1">Agent-ID</label>
          <input value={cfg.agentId || ""} onChange={e => updCfg({ agentId: e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, "") })}
            placeholder="mein-agent"
            className="w-full rounded-lg bg-zinc-800 border border-white/10 px-2.5 py-1.5 text-xs text-white font-mono focus:outline-none focus:border-indigo-500/60" />
        </div>
        <div>
          <label className="block text-[0.65rem] text-white/40 mb-1">Typ</label>
          <select value={cfg.type || "specialist"} onChange={e => updCfg({ type: e.target.value })}
            className="w-full rounded-lg bg-zinc-800 border border-white/10 px-2.5 py-1.5 text-sm text-white focus:outline-none">
            <option value="boss">Boss</option>
            <option value="worker">Worker</option>
            <option value="specialist">Specialist</option>
          </select>
        </div>
        <div>
          <label className="block text-[0.65rem] text-white/40 mb-1">LLM Model</label>
          <select value={cfg.model || "claude-sonnet-4-6"} onChange={e => updCfg({ model: e.target.value })}
            className="w-full rounded-lg bg-zinc-800 border border-white/10 px-2.5 py-1.5 text-xs text-white font-mono focus:outline-none">
            {MODELS.map(m => <option key={m} value={m}>{m}</option>)}
          </select>
        </div>
        <div>
          <label className="block text-[0.65rem] text-white/40 mb-1">Soul / Persönlichkeit</label>
          <textarea value={cfg.soul || ""} onChange={e => updCfg({ soul: e.target.value })}
            rows={4} placeholder="Du bist ein hilfreicher Assistent..."
            className="w-full rounded-lg bg-zinc-800 border border-white/10 px-2.5 py-1.5 text-xs text-white focus:outline-none focus:border-indigo-500/60 resize-none" />
        </div>
      </>}

      {/* Tool Properties */}
      {n.type === "tool" && <>
        <div>
          <label className="block text-[0.65rem] text-white/40 mb-1">Tool-ID</label>
          <select value={cfg.toolId || ""} onChange={e => { updCfg({ toolId: e.target.value }); upd({ label: e.target.value }); }}
            className="w-full rounded-lg bg-zinc-800 border border-white/10 px-2.5 py-1.5 text-xs text-white font-mono focus:outline-none">
            <option value="">— wählen —</option>
            {availableTools.map(t => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>
      </>}

      {/* MCP Properties */}
      {n.type === "mcp" && <>
        <div>
          <label className="block text-[0.65rem] text-white/40 mb-1">MCP Server</label>
          <select value={cfg.serverId || ""} onChange={e => { updCfg({ serverId: e.target.value }); upd({ label: e.target.value }); }}
            className="w-full rounded-lg bg-zinc-800 border border-white/10 px-2.5 py-1.5 text-xs text-white font-mono focus:outline-none">
            <option value="">— wählen —</option>
            {availableMcp.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>
      </>}

      {/* Plugin Properties */}
      {n.type === "plugin" && <>
        <div>
          <label className="block text-[0.65rem] text-white/40 mb-1">Plugin</label>
          <select value={cfg.pluginId || ""} onChange={e => { updCfg({ pluginId: e.target.value }); upd({ label: e.target.value }); }}
            className="w-full rounded-lg bg-zinc-800 border border-white/10 px-2.5 py-1.5 text-xs text-white font-mono focus:outline-none">
            <option value="">— wählen —</option>
            {availablePlugins.map(p => <option key={p} value={p}>{p}</option>)}
          </select>
        </div>
      </>}

      {n.type === "repository" && <>
        <div>
          <label className="block text-[0.65rem] text-white/40 mb-1">URL</label>
          <input value={cfg.url || ""} onChange={e => updCfg({ url: e.target.value })}
            placeholder="https://gitea.intern/owner/repo"
            className="w-full rounded-lg bg-zinc-800 border border-white/10 px-2.5 py-1.5 text-xs text-white font-mono focus:outline-none focus:border-blue-500/60" />
        </div>
      </>}

      {n.type === "credential" && <>
        <div>
          <label className="block text-[0.65rem] text-white/40 mb-1">Config-Key</label>
          <input value={cfg.key || ""} onChange={e => updCfg({ key: e.target.value })}
            placeholder="gitea_token"
            className="w-full rounded-lg bg-zinc-800 border border-white/10 px-2.5 py-1.5 text-xs text-white font-mono focus:outline-none focus:border-orange-500/60" />
        </div>
      </>}

      {n.type === "skill" && <>
        <div>
          <label className="block text-[0.65rem] text-white/40 mb-1">Skill-Datei</label>
          <input value={cfg.file || ""} onChange={e => updCfg({ file: e.target.value })}
            placeholder="python_expert.md"
            className="w-full rounded-lg bg-zinc-800 border border-white/10 px-2.5 py-1.5 text-xs text-white font-mono focus:outline-none focus:border-purple-500/60" />
        </div>
      </>}

      {n.type === "memory" && <>
        <div>
          <label className="block text-[0.65rem] text-white/40 mb-1">Datei / Ordner</label>
          <input value={cfg.file || ""} onChange={e => updCfg({ file: e.target.value })}
            placeholder="project_notes.md"
            className="w-full rounded-lg bg-zinc-800 border border-white/10 px-2.5 py-1.5 text-xs text-white font-mono focus:outline-none focus:border-teal-500/60" />
        </div>
      </>}

      {n.type === "toolpolicy" && <>
        <div>
          <label className="block text-[0.65rem] text-white/40 mb-1">Tool-ID</label>
          <input value={cfg.tool || ""} onChange={e => updCfg({ tool: e.target.value })}
            placeholder="shell_exec"
            className="w-full rounded-lg bg-zinc-800 border border-white/10 px-2.5 py-1.5 text-xs text-white font-mono focus:outline-none" />
        </div>
        <div className="flex items-center gap-3">
          <label className={cn("flex items-center gap-2 cursor-pointer px-3 py-1.5 rounded-lg border text-xs transition-colors",
            cfg.allowed !== false ? "bg-green-950/60 border-green-500/40 text-green-300" : "bg-zinc-800 border-white/10 text-white/30")}
            onClick={() => updCfg({ allowed: true })}>
            <Shield className="h-3 w-3" /> Erlaubt
          </label>
          <label className={cn("flex items-center gap-2 cursor-pointer px-3 py-1.5 rounded-lg border text-xs transition-colors",
            cfg.allowed === false ? "bg-red-950/60 border-red-500/40 text-red-300" : "bg-zinc-800 border-white/10 text-white/30")}
            onClick={() => updCfg({ allowed: false })}>
            <X className="h-3 w-3" /> Gesperrt
          </label>
        </div>
      </>}
    </div>
  );
}

// ── Inner Component ───────────────────────────────────────────────────────────

interface AgentEntry { id: string; identity: string }

function AgentBlueprintInner({ agents }: { agents: AgentEntry[] }) {
  const { t } = useTranslation();
  const [selectedAgentId, setSelectedAgentId] = useState<string>(agents[0]?.id ?? "");
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [selectedNode, setSelectedNode]  = useState<Node | null>(null);
  const [saving,  setSaving]  = useState(false);
  const [loading, setLoading] = useState(false);
  const [toast,   setToast]   = useState<string | null>(null);
  const [isNewMode, setIsNewMode] = useState(false);
  const [creating, setCreating]   = useState(false);
  const [availableTools, setAvailableTools]     = useState<string[]>([]);
  const [availableMcp, setAvailableMcp]         = useState<string[]>([]);
  const [availablePlugins, setAvailablePlugins] = useState<string[]>([]);
  const [showPalette, setShowPalette]           = useState(false);
  const rf = useReactFlow();

  // Verfügbare Tools, MCP-Server, Plugins laden
  useEffect(() => {
    api.get<{name:string}[]>("/tools").then(tools => {
      setAvailableTools(tools.map(t => (t as any).name || (t as any).id || "").filter(Boolean).sort());
    }).catch(() => {});
    api.get<{servers:{id:string}[]}>("/mcp/servers").then(d => {
      setAvailableMcp((d.servers || []).map(s => s.id));
    }).catch(() => {});
    api.pluginsList().then(d => {
      setAvailablePlugins(d.plugins.filter(p => p.enabled).map(p => p.id));
    }).catch(() => {});
  }, []);

  // Blueprint laden wenn Agent ausgewählt wird
  useEffect(() => {
    if (!selectedAgentId || isNewMode) return;
    setLoading(true);
    setSelectedNode(null);
    api.get<{ nodes: any[]; edges: any[] }>(`/agents/${selectedAgentId}/workflow-blueprint`)
      .then(wf => {
        setNodes(wf.nodes || []);
        setEdges(wf.edges || []);
        setTimeout(() => rf.fitView({ padding: 0.2 }), 50);
      })
      .catch(() => { setNodes([]); setEdges([]); })
      .finally(() => setLoading(false));
  }, [selectedAgentId, setNodes, setEdges, rf, isNewMode]);

  const onConnect = useCallback((c: Connection) => {
    setEdges(es => addEdge({
      ...c, animated: true,
      style: { stroke: "#6366f1", strokeWidth: 2 },
    } as Edge, es));
  }, [setEdges]);

  function addNode(type: string, label?: string) {
    const defaults: Record<string, string> = {
      repository: "Repo", credential: "Token", skill: "Skill",
      memory: "Memory", toolpolicy: "Tool Policy", tool: "tool",
      mcp: "MCP Server", plugin: "Plugin",
      agentprofile: "Neuer Agent",
    };
    const id = `${type}-${Date.now()}`;
    const cnt = nodes.length;
    const config: any = {};
    if (type === "agentprofile") {
      config.isNew = isNewMode;
      config.type = "specialist";
      config.model = "claude-sonnet-4-6";
    }
    setNodes(ns => [...ns, {
      id, type, position: { x: 80 + cnt * 25, y: 80 + cnt * 18 },
      data: { label: label || defaults[type] || type, config },
    }]);
  }

  function startNewAgent() {
    setIsNewMode(true);
    setSelectedAgentId("");
    setSelectedNode(null);
    setNodes([{
      id: "agent-new",
      type: "agentprofile",
      position: { x: 400, y: 200 },
      data: {
        label: "Neuer Agent",
        config: { isNew: true, type: "specialist", model: "claude-sonnet-4-6", soul: "" },
      },
    }]);
    setEdges([]);
    setTimeout(() => rf.fitView({ padding: 0.3 }), 50);
  }

  function cancelNewAgent() {
    setIsNewMode(false);
    setSelectedAgentId(agents[0]?.id ?? "");
  }

  function updateNodeData(nodeId: string, data: any) {
    setNodes(ns => ns.map(n => n.id === nodeId ? { ...n, data } : n));
    setSelectedNode(prev => prev?.id === nodeId ? { ...prev, data } : prev);
  }

  function deleteNode(nodeId: string) {
    setNodes(ns => ns.filter(n => n.id !== nodeId));
    setEdges(es => es.filter(e => e.source !== nodeId && e.target !== nodeId));
    setSelectedNode(null);
  }

  async function save() {
    if (!selectedAgentId) return;
    setSaving(true);
    try {
      await api.put(`/agents/${selectedAgentId}/workflow-blueprint`, { nodes, edges });
      setToast(t("common.saved"));
      setTimeout(() => setToast(null), 3000);
    } catch (e) {
      setToast(e instanceof Error ? e.message : t("common.error"));
    } finally {
      setSaving(false);
    }
  }

  async function createAgent() {
    // Agent-Node finden
    const agentNode = nodes.find(n => n.type === "agentprofile");
    if (!agentNode) { setToast("Kein Agent-Node gefunden"); return; }
    const cfg = (agentNode.data as any).config || {};
    const agentId = cfg.agentId?.trim();
    if (!agentId) { setToast("Agent-ID fehlt"); return; }
    const identity = (agentNode.data as any).label?.trim() || agentId;

    // Verbundene Nodes sammeln
    const connectedIds = new Set(
      edges.filter(e => e.target === agentNode.id).map(e => e.source)
    );
    const connected = nodes.filter(n => connectedIds.has(n.id));

    const tools = connected
      .filter(n => n.type === "tool")
      .map(n => (n.data as any).config?.toolId)
      .filter(Boolean);

    const mcpServers = connected
      .filter(n => n.type === "mcp")
      .map(n => (n.data as any).config?.serverId)
      .filter(Boolean);

    setCreating(true);
    try {
      await api.post("/agents", {
        id: agentId,
        type: cfg.type || "specialist",
        identity,
        model: cfg.model || "claude-sonnet-4-6",
        soul: cfg.soul || "",
        tools: tools.length > 0 ? tools : ["file_read", "web_search", "read_memory", "write_memory"],
        mcp_servers: mcpServers,
      });

      // Plugins zuweisen
      const pluginIds = connected
        .filter(n => n.type === "plugin")
        .map(n => (n.data as any).config?.pluginId)
        .filter(Boolean);
      if (pluginIds.length > 0) {
        await api.pluginAgentSet(agentId, pluginIds).catch(() => {});
      }

      // Blueprint speichern
      await api.put(`/agents/${agentId}/workflow-blueprint`, { nodes, edges }).catch(() => {});

      setToast(`Agent "${identity}" erstellt!`);
      setIsNewMode(false);
      setTimeout(() => setToast(null), 4000);
      // Agent zur Liste hinzufügen und auswählen
      agents.push({ id: agentId, identity });
      setSelectedAgentId(agentId);
    } catch (e: any) {
      setToast(e.message || "Fehler beim Erstellen");
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="flex h-full flex-col">
      {/* Toolbar */}
      <div className="flex items-center gap-2 px-4 py-2 border-b border-white/10 shrink-0 flex-wrap">
        {!isNewMode ? (
          <>
            <select value={selectedAgentId} onChange={e => setSelectedAgentId(e.target.value)}
              className="rounded-lg bg-zinc-900 border border-white/15 px-2.5 py-1.5 text-sm text-white focus:outline-none">
              {agents.map(a => <option key={a.id} value={a.id}>{a.identity}</option>)}
            </select>
            <button onClick={startNewAgent}
              className="flex items-center gap-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-700 px-3 py-1.5 text-xs text-white transition-colors">
              <PlusCircle className="h-3.5 w-3.5" /> Neuer Agent
            </button>
            {selectedAgentId && !selectedAgentId.startsWith("personal_") && (
              <button onClick={async () => {
                const name = agents.find(a => a.id === selectedAgentId)?.identity || selectedAgentId;
                if (!confirm(`Agent "${name}" wirklich löschen?`)) return;
                try {
                  await api.delete(`/agents/${selectedAgentId}`);
                  const idx = agents.findIndex(a => a.id === selectedAgentId);
                  if (idx >= 0) agents.splice(idx, 1);
                  setSelectedAgentId(agents[0]?.id ?? "");
                  setToast(`Agent "${name}" gelöscht`);
                  setTimeout(() => setToast(null), 3000);
                } catch (e: any) { setToast(e.message); }
              }}
                className="flex items-center gap-1.5 rounded-lg border border-red-500/40 px-2.5 py-1.5 text-xs text-red-400 hover:bg-red-500/15 transition-colors">
                <X className="h-3 w-3" /> Löschen
              </button>
            )}
          </>
        ) : (
          <>
            <span className="flex items-center gap-1.5 text-sm text-indigo-300 font-medium">
              <Sparkles className="h-4 w-4" /> Agent-Builder
            </span>
            <button onClick={cancelNewAgent}
              className="flex items-center gap-1.5 rounded-lg border border-white/15 px-2.5 py-1.5 text-xs text-white/60 hover:text-white transition-colors">
              <X className="h-3 w-3" /> Abbrechen
            </button>
          </>
        )}
        <div className="h-4 w-px bg-white/10" />
        {/* Palette Toggle */}
        <button onClick={() => setShowPalette(p => !p)}
          className="flex items-center gap-1 rounded-lg bg-zinc-900 border border-white/10 px-2.5 py-1.5 text-xs text-white hover:bg-zinc-800 transition-colors">
          <ChevronDown className={cn("h-3 w-3 transition-transform", showPalette && "rotate-180")} /> Palette
        </button>
        {showPalette && PALETTE_ITEMS.map(item => (
          <button key={item.type} onClick={() => addNode(item.type)}
            className="flex items-center gap-1.5 rounded-lg bg-zinc-900 hover:bg-zinc-800 border border-white/10 px-2.5 py-1.5 text-xs text-white transition-colors">
            <item.icon className={cn("h-3 w-3", item.color)} />
            {item.label}
          </button>
        ))}
        <div className="flex-1" />
        {toast && <span className="text-xs text-indigo-300">{toast}</span>}
        {isNewMode ? (
          <button onClick={createAgent} disabled={creating}
            className="flex items-center gap-1.5 rounded-lg bg-green-600 hover:bg-green-700 disabled:opacity-50 px-3 py-1.5 text-sm text-white transition-colors">
            {creating ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Rocket className="h-3.5 w-3.5" />}
            {creating ? "Erstelle..." : "Agent erstellen"}
          </button>
        ) : (
          <button onClick={save} disabled={saving}
            className="flex items-center gap-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 px-3 py-1.5 text-sm text-white transition-colors">
            {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
            {saving ? t("common.saving") : t("common.save")}
          </button>
        )}
      </div>

      {/* Canvas + Properties */}
      <div className="flex flex-1 overflow-hidden">
        <div className="flex-1 relative">
          {loading && (
            <div className="absolute inset-0 z-10 flex items-center justify-center bg-zinc-950/60">
              <Loader2 className="h-6 w-6 animate-spin text-white/30" />
            </div>
          )}
          <ReactFlow
            nodes={nodes} edges={edges}
            onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
            onConnect={onConnect} nodeTypes={NODE_TYPES}
            colorMode="dark" fitView
            onNodeClick={(_, n) => setSelectedNode(n)}
            onPaneClick={() => setSelectedNode(null)}
          >
            <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="rgba(255,255,255,0.05)" />
            <Controls />
            <MiniMap nodeColor={n => ({
              repository: "#60a5fa", credential: "#fb923c", skill: "#c084fc",
              memory: "#2dd4bf", toolpolicy: "#4ade80", tool: "#22d3ee",
              mcp: "#f472b6", plugin: "#fbbf24", agentprofile: "#818cf8",
            }[n.type ?? ""] ?? "#6366f1")} />
            {nodes.length === 0 && !loading && !isNewMode && (
              <Panel position="top-center" style={{ marginTop: 60 }}>
                <p className="text-white/20 text-sm pointer-events-none">Nodes über die Palette hinzufügen und mit dem Agenten verdrahten</p>
              </Panel>
            )}
            {isNewMode && nodes.length === 1 && (
              <Panel position="top-center" style={{ marginTop: 60 }}>
                <div className="text-center space-y-1 pointer-events-none">
                  <p className="text-indigo-300/60 text-sm">Agent-Node auswählen und Eigenschaften rechts konfigurieren</p>
                  <p className="text-white/20 text-xs">Dann Tools, Skills, MCP-Server über die Palette hinzufügen und verbinden</p>
                </div>
              </Panel>
            )}
          </ReactFlow>
        </div>
        <div className="w-64 shrink-0 border-l border-white/10 bg-zinc-900/50 flex flex-col">
          <div className="px-3 py-2 border-b border-white/10">
            <p className="text-[0.65rem] font-bold uppercase tracking-wider text-white/30">Eigenschaften</p>
          </div>
          <div className="flex-1 overflow-y-auto">
            <PropertiesPanel
              node={selectedNode}
              onChange={updateNodeData}
              onDelete={deleteNode}
              availableTools={availableTools}
              availableMcp={availableMcp}
              availablePlugins={availablePlugins}
            />
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Export ────────────────────────────────────────────────────────────────────

export function AgentBlueprintTab() {
  const [agents,  setAgents]  = useState<AgentEntry[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.get<Record<string, any>>("/agents")
      .then(ad => setAgents(
        Object.entries(ad)
          .filter(([id]) => !id.startsWith("personal_"))
          .map(([id, v]) => ({ id, identity: v.config?.identity || id }))
      ))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="flex items-center justify-center h-full"><Loader2 className="h-8 w-8 animate-spin text-white/30" /></div>;

  return (
    <ReactFlowProvider>
      <AgentBlueprintInner agents={agents} />
    </ReactFlowProvider>
  );
}
