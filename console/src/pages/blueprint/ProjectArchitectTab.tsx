import "@xyflow/react/dist/style.css";
import { useCallback, useEffect, useState } from "react";
import {
  ReactFlow, ReactFlowProvider, Background, Controls, MiniMap,
  addEdge, useNodesState, useEdgesState, useReactFlow,
  Handle, Position, BackgroundVariant, Panel,
  type Connection, type Edge, type Node,
} from "@xyflow/react";
import { Crown, Bot, Plus, Save, Loader2, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

interface Project { id: string; name: string; boss: string; workers: string[] }
interface Agent   { id: string; identity: string }

// ── Boss node ──────────────────────────────────────────────────────────────
function BossNode({ data, selected }: { data: { label: string }; selected: boolean }) {
  return (
    <div className={cn("min-w-[160px] rounded-xl border-2 px-3 py-2.5 shadow-lg select-none bg-yellow-950/60 border-yellow-500/60", selected && "ring-2 ring-white/25")}>
      <div className="flex items-center gap-1.5 mb-1">
        <Crown className="h-3 w-3 text-yellow-400" />
        <span className="text-[0.55rem] font-bold uppercase tracking-widest text-yellow-400">Boss</span>
      </div>
      <p className="text-sm font-medium text-white leading-tight">{data.label}</p>
      <Handle type="target" position={Position.Left} id="input"
        style={{ background: "#eab308", border: "2px solid #a16207", width: 10, height: 10 }} />
      <Handle type="source" position={Position.Right} id="output"
        style={{ background: "#eab308", border: "2px solid #a16207", width: 10, height: 10 }} />
    </div>
  );
}

// ── Worker node ────────────────────────────────────────────────────────────
function WorkerNode({ data, selected }: { data: { label: string }; selected: boolean }) {
  return (
    <div className={cn("min-w-[160px] rounded-xl border-2 px-3 py-2.5 shadow-lg select-none bg-blue-950/60 border-blue-500/60", selected && "ring-2 ring-white/25")}>
      <div className="flex items-center gap-1.5 mb-1">
        <Bot className="h-3 w-3 text-blue-400" />
        <span className="text-[0.55rem] font-bold uppercase tracking-widest text-blue-400">Worker</span>
      </div>
      <p className="text-sm font-medium text-white leading-tight">{data.label}</p>
      <Handle type="source" position={Position.Right} id="output"
        style={{ background: "#3b82f6", border: "2px solid #1d4ed8", width: 10, height: 10 }} />
    </div>
  );
}

const NODE_TYPES = { bossNode: BossNode as any, workerNode: WorkerNode as any };

function buildGraph(project: Project, agents: Agent[]) {
  const getName = (id: string) => agents.find(a => a.id === id)?.identity || id;
  const nodes: Node[] = [
    { id: `boss-${project.boss}`, type: "bossNode", position: { x: 300, y: 200 }, data: { label: getName(project.boss) } },
    ...project.workers.map((w, i) => ({
      id: `worker-${w}`, type: "workerNode",
      position: { x: 30, y: 80 + i * 90 },
      data: { label: getName(w) },
    })),
  ];
  const edges: Edge[] = project.workers.map(w => ({
    id: `e-${w}`, source: `worker-${w}`, target: `boss-${project.boss}`,
    sourceHandle: "output", targetHandle: "input",
    animated: true, style: { stroke: "#6366f1", strokeWidth: 2 },
  }));
  return { nodes, edges };
}

function ArchitectInner({ projects, agents }: { projects: Project[]; agents: Agent[] }) {
  const [selectedProjectId, setSelectedProjectId] = useState<string>(projects[0]?.id ?? "");
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [saving, setSaving] = useState(false);
  const [toast, setToast]   = useState<string | null>(null);
  const rf = useReactFlow();

  const project = projects.find(p => p.id === selectedProjectId);

  useEffect(() => {
    if (!project) return;
    const { nodes: n, edges: e } = buildGraph(project, agents);
    setNodes(n);
    setEdges(e);
    setTimeout(() => rf.fitView({ padding: 0.2 }), 50);
  }, [selectedProjectId, project, agents, setNodes, setEdges, rf]);

  const onConnect = useCallback((c: Connection) => setEdges(es => addEdge(
    { ...c, animated: true } as Edge, es
  ).map(e => e.id === `reactflow__edge-${c.source}${c.sourceHandle}-${c.target}${c.targetHandle}`
    ? { ...e, style: { stroke: "#6366f1", strokeWidth: 2 } } : e
  )), [setEdges]);

  function addWorker(agentId: string) {
    if (!project) return;
    const name = agents.find(a => a.id === agentId)?.identity || agentId;
    const nodeId = `worker-${agentId}`;
    if (nodes.find(n => n.id === nodeId)) return;
    const bossNode = nodes.find(n => n.type === "bossNode");
    const y = nodes.filter(n => n.type === "workerNode").length * 90 + 80;
    const newNode: Node = { id: nodeId, type: "workerNode", position: { x: 30, y }, data: { label: name } };
    const newEdge: Edge = { id: `e-${agentId}`, source: nodeId, target: bossNode?.id ?? "", sourceHandle: "output", targetHandle: "input", animated: true, style: { stroke: "#6366f1", strokeWidth: 2 } };
    setNodes(ns => [...ns, newNode]);
    setEdges(es => [...es, newEdge]);
  }

  async function save() {
    if (!project) return;
    setSaving(true);
    try {
      const workerIds = nodes.filter(n => n.type === "workerNode").map(n => n.id.replace("worker-", ""));
      const bossId    = nodes.find(n => n.type === "bossNode")?.id.replace("boss-", "") ?? project.boss;
      await api.updateProject(project.id, { boss: bossId, workers: workerIds });
      setToast("Gespeichert"); setTimeout(() => setToast(null), 2500);
    } catch (e) {
      setToast(e instanceof Error ? e.message : "Fehler");
    } finally {
      setSaving(false);
    }
  }

  const currentWorkerIds = nodes.filter(n => n.type === "workerNode").map(n => n.id.replace("worker-", ""));
  const addableAgents = agents.filter(a => a.id !== project?.boss && !currentWorkerIds.includes(a.id));
  const isDark = typeof document !== "undefined" && document.documentElement.classList.contains("dark");

  return (
    <div className="flex h-full flex-col">
      {/* Toolbar */}
      <div className="flex items-center gap-2 px-4 py-2 border-b border-white/10 shrink-0 flex-wrap">
        <select value={selectedProjectId} onChange={e => setSelectedProjectId(e.target.value)}
          className="rounded-lg bg-zinc-900 border border-white/15 px-2.5 py-1.5 text-sm text-white focus:outline-none">
          {projects.filter(p => !p.id.startsWith("personal_")).map(p => (
            <option key={p.id} value={p.id}>{p.name || p.id}</option>
          ))}
        </select>
        {addableAgents.length > 0 && (
          <select defaultValue="" onChange={e => { if (e.target.value) addWorker(e.target.value); e.target.value = ""; }}
            className="rounded-lg bg-zinc-900 border border-white/15 px-2.5 py-1.5 text-sm text-white focus:outline-none">
            <option value="">+ Worker hinzufügen</option>
            {addableAgents.map(a => <option key={a.id} value={a.id}>{a.identity}</option>)}
          </select>
        )}
        <div className="flex-1" />
        {toast && <span className="text-sm text-indigo-300">{toast}</span>}
        <button onClick={save} disabled={saving}
          className="flex items-center gap-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 px-3 py-1.5 text-sm text-white transition-colors">
          {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
          {saving ? "Speichere…" : "Speichern"}
        </button>
      </div>

      {/* Canvas */}
      <div className="flex-1 relative">
        <ReactFlow nodes={nodes} edges={edges} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
          onConnect={onConnect} nodeTypes={NODE_TYPES} colorMode={isDark ? "dark" : "light"} fitView>
          <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="rgba(255,255,255,0.05)" />
          <Controls />
          <MiniMap nodeColor={n => n.type === "bossNode" ? "#eab308" : "#3b82f6"} />
          {!project && (
            <Panel position="top-center" style={{ marginTop: 40 }}>
              <p className="text-white/25 text-base pointer-events-none">Kein Projekt ausgewählt</p>
            </Panel>
          )}
        </ReactFlow>
      </div>
    </div>
  );
}

export function ProjectArchitectTab() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [agents,   setAgents]   = useState<Agent[]>([]);
  const [loading,  setLoading]  = useState(true);

  useEffect(() => {
    Promise.all([
      api.get<Record<string, any>>("/projects"),
      api.get<Record<string, any>>("/agents"),
    ]).then(([pd, ad]) => {
      setProjects(Object.entries(pd).map(([id, v]) => ({
        id, name: v.name || id,
        boss: v.config?.agents?.boss || v.boss || "",
        workers: v.config?.agents?.workers || v.workers || [],
      })));
      setAgents(Object.entries(ad).filter(([id]) => !id.startsWith("personal_")).map(([id, v]) => ({
        id, identity: v.config?.identity || id,
      })));
    }).finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="flex items-center justify-center h-full"><Loader2 className="h-8 w-8 animate-spin text-white/30" /></div>;

  return (
    <ReactFlowProvider>
      <ArchitectInner projects={projects} agents={agents} />
    </ReactFlowProvider>
  );
}
