import "@xyflow/react/dist/style.css";
import { useCallback, useEffect, useState } from "react";
import {
  ReactFlow, ReactFlowProvider, Background, Controls, MiniMap,
  addEdge, useNodesState, useEdgesState, useReactFlow,
  Handle, Position, BackgroundVariant, Panel,
  type Connection, type Edge, type Node,
} from "@xyflow/react";
import {
  Play, GitBranch, Database, Square, Save, Loader2,
  ChevronDown, ChevronRight, Plus, X,
} from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

// ── Node-Typen ────────────────────────────────────────────────────────────────

function StepNode({ data, selected }: { data: any; selected: boolean }) {
  return (
    <div className={cn("min-w-[180px] max-w-[220px] rounded-xl border-2 px-3 py-2.5 shadow-lg select-none bg-indigo-950/60 border-indigo-500/60", selected && "ring-2 ring-white/25")}>
      <div className="flex items-center gap-1.5 mb-1">
        <Play className="h-3 w-3 text-indigo-400" />
        <span className="text-[0.55rem] font-bold uppercase tracking-widest text-indigo-400">Schritt</span>
      </div>
      <p className="text-sm font-medium text-white leading-tight">{data.label || "Schritt"}</p>
      {data.description && <p className="text-[0.65rem] text-white/40 mt-0.5 leading-tight">{data.description}</p>}
      <Handle type="target" position={Position.Left} id="in" style={{ background: "#818cf8", border: "2px solid #4338ca", width: 10, height: 10 }} />
      <Handle type="source" position={Position.Right} id="out" style={{ background: "#818cf8", border: "2px solid #4338ca", width: 10, height: 10 }} />
    </div>
  );
}

function DatasourceNode({ data, selected }: { data: any; selected: boolean }) {
  return (
    <div className={cn("min-w-[180px] max-w-[220px] rounded-xl border-2 px-3 py-2.5 shadow-lg select-none bg-emerald-950/60 border-emerald-500/60", selected && "ring-2 ring-white/25")}>
      <div className="flex items-center gap-1.5 mb-1">
        <Database className="h-3 w-3 text-emerald-400" />
        <span className="text-[0.55rem] font-bold uppercase tracking-widest text-emerald-400">Datenquelle</span>
      </div>
      <p className="text-sm font-medium text-white leading-tight">{data.label || "Datenquelle"}</p>
      {(data.config?.url || data.config?.path) && (
        <p className="text-[0.6rem] text-emerald-400/60 mt-0.5 font-mono truncate">{data.config?.url || data.config?.path}</p>
      )}
      <Handle type="target" position={Position.Left} id="in" style={{ background: "#34d399", border: "2px solid #065f46", width: 10, height: 10 }} />
      <Handle type="source" position={Position.Right} id="out" style={{ background: "#34d399", border: "2px solid #065f46", width: 10, height: 10 }} />
    </div>
  );
}

function BranchNode({ data, selected }: { data: any; selected: boolean }) {
  return (
    <div className={cn("min-w-[180px] max-w-[220px] rounded-xl border-2 px-3 py-2.5 shadow-lg select-none bg-amber-950/60 border-amber-500/60", selected && "ring-2 ring-white/25")}>
      <div className="flex items-center gap-1.5 mb-1">
        <GitBranch className="h-3 w-3 text-amber-400" />
        <span className="text-[0.55rem] font-bold uppercase tracking-widest text-amber-400">Entscheidung</span>
      </div>
      <p className="text-sm font-medium text-white leading-tight">{data.label || "Bedingung?"}</p>
      <Handle type="target" position={Position.Left} id="in" style={{ background: "#fbbf24", border: "2px solid #92400e", width: 10, height: 10 }} />
      <Handle type="source" position={Position.Right} id="yes" style={{ background: "#4ade80", border: "2px solid #166534", width: 10, height: 10, top: "35%" }} />
      <Handle type="source" position={Position.Right} id="no" style={{ background: "#f87171", border: "2px solid #991b1b", width: 10, height: 10, top: "65%" }} />
      <div className="absolute right-[-42px] top-[26%] text-[0.55rem] text-green-400 select-none">Ja</div>
      <div className="absolute right-[-42px] top-[54%] text-[0.55rem] text-red-400 select-none">Nein</div>
    </div>
  );
}

function EndNode({ data, selected }: { data: any; selected: boolean }) {
  return (
    <div className={cn("min-w-[140px] rounded-xl border-2 px-3 py-2.5 shadow-lg select-none bg-zinc-800/80 border-zinc-500/60", selected && "ring-2 ring-white/25")}>
      <div className="flex items-center gap-1.5 mb-1">
        <Square className="h-3 w-3 text-zinc-400" />
        <span className="text-[0.55rem] font-bold uppercase tracking-widest text-zinc-400">Ende</span>
      </div>
      <p className="text-sm font-medium text-white/70 leading-tight">{data.label || "Antwort ausgeben"}</p>
      <Handle type="target" position={Position.Left} id="in" style={{ background: "#a1a1aa", border: "2px solid #52525b", width: 10, height: 10 }} />
    </div>
  );
}

const NODE_TYPES = {
  step:       StepNode       as any,
  datasource: DatasourceNode as any,
  branch:     BranchNode     as any,
  end:        EndNode        as any,
};

// ── Palette ───────────────────────────────────────────────────────────────────

const PALETTE_ITEMS = [
  { type: "step",       label: "Schritt",      icon: Play,       color: "text-indigo-400", desc: "Aufgabe / Aktion" },
  { type: "datasource", label: "Datenquelle",  icon: Database,   color: "text-emerald-400", desc: "Git, API, Datei…" },
  { type: "branch",     label: "Entscheidung", icon: GitBranch,  color: "text-amber-400",  desc: "Ja / Nein Verzweigung" },
  { type: "end",        label: "Ende",         icon: Square,     color: "text-zinc-400",   desc: "Workflow abgeschlossen" },
];

const DS_TYPES = ["git", "github", "api", "context7", "file", "url", "confluence", "notion"];

// ── Properties Panel ──────────────────────────────────────────────────────────

function PropertiesPanel({ node, onChange, onDelete }: {
  node: Node | null;
  onChange: (id: string, data: any) => void;
  onDelete: (id: string) => void;
}) {
  if (!node) return (
    <div className="flex flex-col items-center justify-center h-full text-white/20 text-xs gap-2 p-4">
      <p>Node auswählen um Eigenschaften zu bearbeiten</p>
    </div>
  );

  const activeNode = node;
  const d = activeNode.data as any;
  const cfg = d.config || {};

  function upd(patch: any) { onChange(activeNode.id, { ...d, ...patch }); }
  function updCfg(patch: any) { upd({ config: { ...cfg, ...patch } }); }

  return (
    <div className="p-3 space-y-3 overflow-y-auto">
      <div className="flex items-center justify-between">
        <span className="text-xs font-bold text-white/50 uppercase tracking-wider">{activeNode.type}</span>
        <button onClick={() => onDelete(activeNode.id)} className="p-1 rounded text-red-400 hover:bg-red-500/15 transition-colors" title="Node löschen"><X className="h-3.5 w-3.5" /></button>
      </div>

      <div>
        <label className="block text-[0.65rem] text-white/40 mb-1">Bezeichnung</label>
        <input value={d.label || ""} onChange={e => upd({ label: e.target.value })}
          className="w-full rounded-lg bg-zinc-800 border border-white/10 px-2.5 py-1.5 text-sm text-white focus:outline-none focus:border-indigo-500/60" />
      </div>

      {activeNode.type !== "end" && activeNode.type !== "branch" && (
        <div>
          <label className="block text-[0.65rem] text-white/40 mb-1">Beschreibung</label>
          <textarea value={d.description || ""} onChange={e => upd({ description: e.target.value })}
            rows={3}
            className="w-full rounded-lg bg-zinc-800 border border-white/10 px-2.5 py-1.5 text-sm text-white focus:outline-none focus:border-indigo-500/60 resize-none" />
        </div>
      )}

      {activeNode.type === "datasource" && (
        <>
          <div>
            <label className="block text-[0.65rem] text-white/40 mb-1">Typ</label>
            <select value={cfg.type || ""} onChange={e => updCfg({ type: e.target.value })}
              className="w-full rounded-lg bg-zinc-800 border border-white/10 px-2.5 py-1.5 text-sm text-white focus:outline-none">
              <option value="">– wählen –</option>
              {DS_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-[0.65rem] text-white/40 mb-1">URL / Pfad / Repo</label>
            <input value={cfg.url || cfg.path || ""} onChange={e => updCfg({ url: e.target.value, path: undefined })}
              placeholder="https://github.com/owner/repo"
              className="w-full rounded-lg bg-zinc-800 border border-white/10 px-2.5 py-1.5 text-sm text-white focus:outline-none focus:border-emerald-500/60 font-mono text-xs" />
          </div>
          {(cfg.type === "api" || cfg.type === "context7") && activeNode.type === "datasource" && (
            <div>
              <label className="block text-[0.65rem] text-white/40 mb-1">Kontext-Hinweis (optional)</label>
              <input value={cfg.hint || ""} onChange={e => updCfg({ hint: e.target.value })}
                placeholder="z.B. 'lese letzte 7 Tage'"
                className="w-full rounded-lg bg-zinc-800 border border-white/10 px-2.5 py-1.5 text-sm text-white focus:outline-none" />
            </div>
          )}
        </>
      )}

      {activeNode.type === "branch" && (
        <>
          <div>
            <label className="block text-[0.65rem] text-white/40 mb-1">Bedingung / Frage</label>
            <input value={d.label || ""} onChange={e => upd({ label: e.target.value })}
              placeholder="z.B. 'Fehler gefunden?'"
              className="w-full rounded-lg bg-zinc-800 border border-white/10 px-2.5 py-1.5 text-sm text-white focus:outline-none focus:border-amber-500/60" />
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="block text-[0.65rem] text-green-400/60 mb-1">Ja-Label</label>
              <input value={cfg.yes_label || "Ja"} onChange={e => updCfg({ yes_label: e.target.value })}
                className="w-full rounded-lg bg-zinc-800 border border-white/10 px-2 py-1.5 text-xs text-white focus:outline-none" />
            </div>
            <div>
              <label className="block text-[0.65rem] text-red-400/60 mb-1">Nein-Label</label>
              <input value={cfg.no_label || "Nein"} onChange={e => updCfg({ no_label: e.target.value })}
                className="w-full rounded-lg bg-zinc-800 border border-white/10 px-2 py-1.5 text-xs text-white focus:outline-none" />
            </div>
          </div>
        </>
      )}
    </div>
  );
}

// ── Inner Component ───────────────────────────────────────────────────────────

interface Project { id: string; name: string }

function WorkflowInner({ projects }: { projects: Project[] }) {
  const [selectedProjectId, setSelectedProjectId] = useState<string>(projects[0]?.id ?? "");
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [selectedNode, setSelectedNode] = useState<Node | null>(null);
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(false);
  const [toast, setToast]   = useState<string | null>(null);
  const rf = useReactFlow();

  // Load workflow when project changes
  useEffect(() => {
    if (!selectedProjectId) return;
    setLoading(true);
    setSelectedNode(null);
    api.get<{ nodes: any[]; edges: any[] }>(`/projects/${selectedProjectId}/workflow`)
      .then(wf => {
        setNodes(wf.nodes || []);
        setEdges(wf.edges || []);
        setTimeout(() => rf.fitView({ padding: 0.2 }), 50);
      })
      .catch(() => { setNodes([]); setEdges([]); })
      .finally(() => setLoading(false));
  }, [selectedProjectId, setNodes, setEdges, rf]);

  const onConnect = useCallback((c: Connection) => {
    const label = (c.sourceHandle === "yes") ? "Ja" : (c.sourceHandle === "no") ? "Nein" : "";
    setEdges(es => addEdge({
      ...c,
      animated: true,
      label,
      style: { stroke: "#6366f1", strokeWidth: 2 },
      labelStyle: { fill: "#a5b4fc", fontSize: 10 },
      labelBgStyle: { fill: "#1e1b4b" },
    } as Edge, es));
  }, [setEdges]);

  function addNode(type: string) {
    const id = `${type}-${Date.now()}`;
    const defaultLabels: Record<string, string> = {
      step: "Schritt", datasource: "Datenquelle", branch: "Bedingung?", end: "Antwort ausgeben",
    };
    const count = nodes.length;
    const newNode: Node = {
      id, type,
      position: { x: 100 + count * 30, y: 100 + count * 20 },
      data: { label: defaultLabels[type] || type },
    };
    setNodes(ns => [...ns, newNode]);
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
    if (!selectedProjectId) return;
    setSaving(true);
    try {
      await api.put(`/projects/${selectedProjectId}/workflow`, { nodes, edges });
      setToast("Gespeichert"); setTimeout(() => setToast(null), 2500);
    } catch (e) {
      setToast(e instanceof Error ? e.message : "Fehler");
    } finally {
      setSaving(false);
    }
  }

  const filteredProjects = projects.filter(p => !p.id.startsWith("personal_"));

  return (
    <div className="flex h-full flex-col">
      {/* Toolbar */}
      <div className="flex items-center gap-2 px-4 py-2 border-b border-white/10 shrink-0 flex-wrap">
        <select value={selectedProjectId} onChange={e => setSelectedProjectId(e.target.value)}
          className="rounded-lg bg-zinc-900 border border-white/15 px-2.5 py-1.5 text-sm text-white focus:outline-none">
          {filteredProjects.map(p => <option key={p.id} value={p.id}>{p.name || p.id}</option>)}
        </select>
        <div className="h-4 w-px bg-white/10" />
        {PALETTE_ITEMS.map(item => (
          <button key={item.type} onClick={() => addNode(item.type)}
            className="flex items-center gap-1.5 rounded-lg bg-zinc-900 hover:bg-zinc-800 border border-white/10 px-2.5 py-1.5 text-xs text-white transition-colors"
            title={item.desc}>
            <item.icon className={cn("h-3 w-3", item.color)} />
            {item.label}
          </button>
        ))}
        <div className="flex-1" />
        {toast && <span className="text-sm text-indigo-300">{toast}</span>}
        <button onClick={save} disabled={saving}
          className="flex items-center gap-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 px-3 py-1.5 text-sm text-white transition-colors">
          {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
          {saving ? "Speichere…" : "Speichern"}
        </button>
      </div>

      {/* Canvas + Properties */}
      <div className="flex flex-1 overflow-hidden">
        {/* Canvas */}
        <div className="flex-1 relative">
          {loading && (
            <div className="absolute inset-0 z-10 flex items-center justify-center bg-zinc-950/60">
              <Loader2 className="h-6 w-6 animate-spin text-white/30" />
            </div>
          )}
          <ReactFlow
            nodes={nodes} edges={edges}
            onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            nodeTypes={NODE_TYPES}
            colorMode="dark"
            fitView
            onNodeClick={(_, node) => setSelectedNode(node)}
            onPaneClick={() => setSelectedNode(null)}
          >
            <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="rgba(255,255,255,0.05)" />
            <Controls />
            <MiniMap nodeColor={n => {
              if (n.type === "step") return "#818cf8";
              if (n.type === "datasource") return "#34d399";
              if (n.type === "branch") return "#fbbf24";
              return "#71717a";
            }} />
            {nodes.length === 0 && !loading && (
              <Panel position="top-center" style={{ marginTop: 60 }}>
                <p className="text-white/20 text-sm pointer-events-none">Nodes über die Toolbar hinzufügen</p>
              </Panel>
            )}
          </ReactFlow>
        </div>

        {/* Properties Panel */}
        <div className="w-64 shrink-0 border-l border-white/10 bg-zinc-900/50 flex flex-col">
          <div className="px-3 py-2 border-b border-white/10">
            <p className="text-[0.65rem] font-bold uppercase tracking-wider text-white/30">Eigenschaften</p>
          </div>
          <div className="flex-1 overflow-y-auto">
            <PropertiesPanel
              node={selectedNode}
              onChange={updateNodeData}
              onDelete={deleteNode}
            />
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Export ────────────────────────────────────────────────────────────────────

export function WorkflowTab() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading,  setLoading]  = useState(true);

  useEffect(() => {
    api.get<Record<string, any>>("/projects")
      .then(pd => setProjects(
        Object.entries(pd)
          .filter(([id]) => !id.startsWith("personal_"))
          .map(([id, v]) => ({ id, name: v.name || id }))
      ))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="flex items-center justify-center h-full"><Loader2 className="h-8 w-8 animate-spin text-white/30" /></div>;

  return (
    <ReactFlowProvider>
      <WorkflowInner projects={projects} />
    </ReactFlowProvider>
  );
}
