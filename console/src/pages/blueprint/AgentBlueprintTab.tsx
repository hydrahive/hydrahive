import "@xyflow/react/dist/style.css";
import { useCallback, useEffect, useState } from "react";
import {
  ReactFlow, ReactFlowProvider, Background, Controls, MiniMap,
  addEdge, useNodesState, useEdgesState, useReactFlow,
  Handle, Position, BackgroundVariant, Panel,
  type Connection, type Edge, type Node,
} from "@xyflow/react";
import { GitBranch, KeyRound, BookOpen, Brain, Shield, Bot, Save, Loader2, X } from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

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

function AgentProfileNode({ data, selected }: { data: any; selected: boolean }) {
  return (
    <div className={cn("min-w-[180px] rounded-xl border-2 px-3 py-2.5 shadow-lg select-none bg-zinc-800/80 border-zinc-400/40", selected && "ring-2 ring-white/25")}>
      <div className="flex items-center gap-1.5 mb-1">
        <Bot className="h-3 w-3 text-zinc-300" />
        <span className="text-[0.55rem] font-bold uppercase tracking-widest text-zinc-400">Agent-Profil</span>
      </div>
      <p className="text-sm font-medium text-white leading-tight">{data.label || "Agent"}</p>
      <Handle type="target" position={Position.Left} id="in" style={{ background: "#a1a1aa", border: "2px solid #52525b", width: 10, height: 10 }} />
    </div>
  );
}

const NODE_TYPES = {
  repository:   RepoNode         as any,
  credential:   CredentialNode   as any,
  skill:        SkillNode        as any,
  memory:       MemoryNode       as any,
  toolpolicy:   ToolPolicyNode   as any,
  agentprofile: AgentProfileNode as any,
};

// ── Palette ───────────────────────────────────────────────────────────────────

const PALETTE_ITEMS = [
  { type: "repository",   label: "Repository",   icon: GitBranch, color: "text-blue-400" },
  { type: "credential",   label: "Credential",   icon: KeyRound,  color: "text-orange-400" },
  { type: "skill",        label: "Skill",        icon: BookOpen,  color: "text-purple-400" },
  { type: "memory",       label: "Memory",       icon: Brain,     color: "text-teal-400" },
  { type: "toolpolicy",   label: "Tool-Policy",  icon: Shield,    color: "text-green-400" },
  { type: "agentprofile", label: "Agent-Profil", icon: Bot,       color: "text-zinc-300" },
];

// ── Properties Panel ──────────────────────────────────────────────────────────

function PropertiesPanel({ node, onChange, onDelete }: {
  node: Node | null;
  onChange: (id: string, data: any) => void;
  onDelete: (id: string) => void;
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

      {n.type === "repository" && <>
        <div>
          <label className="block text-[0.65rem] text-white/40 mb-1">URL</label>
          <input value={cfg.url || ""} onChange={e => updCfg({ url: e.target.value })}
            placeholder="https://gitea.intern/owner/repo"
            className="w-full rounded-lg bg-zinc-800 border border-white/10 px-2.5 py-1.5 text-xs text-white font-mono focus:outline-none focus:border-blue-500/60" />
        </div>
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="block text-[0.65rem] text-white/40 mb-1">Branch</label>
            <input value={cfg.branch || "main"} onChange={e => updCfg({ branch: e.target.value })}
              className="w-full rounded-lg bg-zinc-800 border border-white/10 px-2 py-1.5 text-xs text-white font-mono focus:outline-none" />
          </div>
          <div>
            <label className="block text-[0.65rem] text-white/40 mb-1">Pfad</label>
            <input value={cfg.path || "/"} onChange={e => updCfg({ path: e.target.value })}
              className="w-full rounded-lg bg-zinc-800 border border-white/10 px-2 py-1.5 text-xs text-white font-mono focus:outline-none" />
          </div>
        </div>
      </>}

      {n.type === "credential" && <>
        <div>
          <label className="block text-[0.65rem] text-white/40 mb-1">Config-Key</label>
          <input value={cfg.key || ""} onChange={e => updCfg({ key: e.target.value })}
            placeholder="gitea_token"
            className="w-full rounded-lg bg-zinc-800 border border-white/10 px-2.5 py-1.5 text-xs text-white font-mono focus:outline-none focus:border-orange-500/60" />
        </div>
        <div>
          <label className="block text-[0.65rem] text-white/40 mb-1">Quelle</label>
          <select value={cfg.source || "config"} onChange={e => updCfg({ source: e.target.value })}
            className="w-full rounded-lg bg-zinc-800 border border-white/10 px-2.5 py-1.5 text-sm text-white focus:outline-none">
            <option value="config">HydraHive Config</option>
            <option value="vaultwarden">Vaultwarden</option>
            <option value="env">Umgebungsvariable</option>
          </select>
        </div>
      </>}

      {n.type === "skill" && <>
        <div>
          <label className="block text-[0.65rem] text-white/40 mb-1">Skill-Datei</label>
          <input value={cfg.file || ""} onChange={e => updCfg({ file: e.target.value })}
            placeholder="python_expert.qmd"
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
        <label className="flex items-center gap-2 cursor-pointer">
          <input type="checkbox" checked={cfg.always || false} onChange={e => updCfg({ always: e.target.checked })}
            className="rounded accent-teal-500" />
          <span className="text-xs text-white/60">Immer laden (ignoriert BM25)</span>
        </label>
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
        <div>
          <label className="block text-[0.65rem] text-white/40 mb-1">Hinweis (optional)</label>
          <input value={cfg.note || ""} onChange={e => updCfg({ note: e.target.value })}
            placeholder="z.B. nur safe-mode"
            className="w-full rounded-lg bg-zinc-800 border border-white/10 px-2.5 py-1.5 text-xs text-white focus:outline-none" />
        </div>
      </>}
    </div>
  );
}

// ── Inner Component ───────────────────────────────────────────────────────────

interface AgentEntry { id: string; identity: string }

function AgentBlueprintInner({ agents }: { agents: AgentEntry[] }) {
  const [selectedAgentId, setSelectedAgentId] = useState<string>(agents[0]?.id ?? "");
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [selectedNode, setSelectedNode]  = useState<Node | null>(null);
  const [saving,  setSaving]  = useState(false);
  const [loading, setLoading] = useState(false);
  const [toast,   setToast]   = useState<string | null>(null);
  const rf = useReactFlow();

  useEffect(() => {
    if (!selectedAgentId) return;
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
  }, [selectedAgentId, setNodes, setEdges, rf]);

  const onConnect = useCallback((c: Connection) => {
    setEdges(es => addEdge({
      ...c, animated: true,
      style: { stroke: "#6366f1", strokeWidth: 2 },
    } as Edge, es));
  }, [setEdges]);

  function addNode(type: string) {
    const defaults: Record<string, string> = {
      repository: "Repo", credential: "Token", skill: "Skill",
      memory: "Memory", toolpolicy: "Tool", agentprofile: agents.find(a => a.id === selectedAgentId)?.identity || "Agent",
    };
    const id = `${type}-${Date.now()}`;
    const cnt = nodes.length;
    setNodes(ns => [...ns, {
      id, type, position: { x: 80 + cnt * 25, y: 80 + cnt * 18 },
      data: { label: defaults[type] || type },
    }]);
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
      setToast("Gespeichert — Cache invalidiert");
      setTimeout(() => setToast(null), 3000);
    } catch (e) {
      setToast(e instanceof Error ? e.message : "Fehler");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex h-full flex-col">
      {/* Toolbar */}
      <div className="flex items-center gap-2 px-4 py-2 border-b border-white/10 shrink-0 flex-wrap">
        <select value={selectedAgentId} onChange={e => setSelectedAgentId(e.target.value)}
          className="rounded-lg bg-zinc-900 border border-white/15 px-2.5 py-1.5 text-sm text-white focus:outline-none">
          {agents.map(a => <option key={a.id} value={a.id}>{a.identity}</option>)}
        </select>
        <div className="h-4 w-px bg-white/10" />
        {PALETTE_ITEMS.map(item => (
          <button key={item.type} onClick={() => addNode(item.type)}
            className="flex items-center gap-1.5 rounded-lg bg-zinc-900 hover:bg-zinc-800 border border-white/10 px-2.5 py-1.5 text-xs text-white transition-colors">
            <item.icon className={cn("h-3 w-3", item.color)} />
            {item.label}
          </button>
        ))}
        <div className="flex-1" />
        {toast && <span className="text-xs text-indigo-300">{toast}</span>}
        <button onClick={save} disabled={saving}
          className="flex items-center gap-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 px-3 py-1.5 text-sm text-white transition-colors">
          {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
          {saving ? "Speichere…" : "Speichern"}
        </button>
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
              memory: "#2dd4bf", toolpolicy: "#4ade80", agentprofile: "#a1a1aa",
            }[n.type ?? ""] ?? "#6366f1")} />
            {nodes.length === 0 && !loading && (
              <Panel position="top-center" style={{ marginTop: 60 }}>
                <p className="text-white/20 text-sm pointer-events-none">Nodes über die Toolbar hinzufügen und mit dem Agenten verdrahten</p>
              </Panel>
            )}
          </ReactFlow>
        </div>
        <div className="w-64 shrink-0 border-l border-white/10 bg-zinc-900/50 flex flex-col">
          <div className="px-3 py-2 border-b border-white/10">
            <p className="text-[0.65rem] font-bold uppercase tracking-wider text-white/30">Eigenschaften</p>
          </div>
          <div className="flex-1 overflow-y-auto">
            <PropertiesPanel node={selectedNode} onChange={updateNodeData} onDelete={deleteNode} />
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
  if (agents.length === 0) return <div className="flex items-center justify-center h-full text-white/25 text-sm">Keine Agenten vorhanden</div>;

  return (
    <ReactFlowProvider>
      <AgentBlueprintInner agents={agents} />
    </ReactFlowProvider>
  );
}
