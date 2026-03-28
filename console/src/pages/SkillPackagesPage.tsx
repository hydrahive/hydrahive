import "@xyflow/react/dist/style.css";
import React, { useCallback, useEffect, useState } from "react";
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  Controls,
  MiniMap,
  addEdge,
  useNodesState,
  useEdgesState,
  useReactFlow,
  Handle,
  Position,
  BackgroundVariant,
  Panel,
  type Connection,
  type Edge,
  type Node,
  type NodeTypes,
} from "@xyflow/react";
import {
  Package,
  Cpu,
  GitBranch,
  Link2,
  ArrowDownToLine,
  Plus,
  Save,
  Trash2,
  ToggleLeft,
  ToggleRight,
} from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

// ── Types ──────────────────────────────────────────────────────────────────
interface SkillNodeData {
  subtype: string;
  label: string;
  params: Record<string, unknown>;
  [key: string]: unknown; // React Flow requires index signature
}

interface SkillPackage {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  nodes: Node<SkillNodeData>[];
  edges: Edge[];
}

type SNode = Node<SkillNodeData>;

// ── Default params per subtype ─────────────────────────────────────────────
function defaultParams(subtype: string): Record<string, unknown> {
  switch (subtype) {
    case "skill":      return { skill_id: "", label: "" };
    case "condition":  return { condition: "", label: "" };
    case "dependency": return { package_id: "", label: "" };
    case "output":     return { label: "" };
    default:           return {};
  }
}

// ── Palette definitions ────────────────────────────────────────────────────
const PALETTE = [
  {
    group: "Knoten",
    items: [
      { type: "skillNode",      subtype: "skill",      label: "Skill",       icon: Cpu,              color: "blue"   as const },
      { type: "conditionNode",  subtype: "condition",  label: "Bedingung",   icon: GitBranch,        color: "yellow" as const },
      { type: "dependencyNode", subtype: "dependency", label: "Abhängigkeit",icon: Link2,            color: "purple" as const },
      { type: "outputNode",     subtype: "output",     label: "Ausgabe",     icon: ArrowDownToLine,  color: "green"  as const },
    ],
  },
];

// ── Custom node components ─────────────────────────────────────────────────
function SkillNodeComp({ data, selected }: { data: SkillNodeData; selected: boolean }) {
  return (
    <div className={cn(
      "min-w-[185px] rounded-xl border-2 px-3 py-2.5 shadow-lg select-none",
      "border-blue-500/60 bg-blue-950/50",
      selected && "ring-2 ring-white/25"
    )}>
      <Handle
        type="target"
        position={Position.Top}
        id="input"
        style={{ background: "#3b82f6", border: "2px solid #1d4ed8", width: 10, height: 10 }}
      />
      <div className="flex items-center gap-1.5 mb-1">
        <Cpu className="h-3 w-3 text-blue-400" />
        <span className="text-[0.55rem] font-bold uppercase tracking-widest text-blue-400">Skill</span>
      </div>
      <p className="text-sm font-medium text-white leading-tight">{data.label || "—"}</p>
      {!!data.params.skill_id && (
        <p className="text-xs text-blue-300/60 mt-0.5 truncate">{data.params.skill_id as string}</p>
      )}
      <Handle
        type="source"
        position={Position.Bottom}
        id="output"
        style={{ background: "#3b82f6", border: "2px solid #1d4ed8", width: 10, height: 10 }}
      />
    </div>
  );
}

function ConditionNodeComp({ data, selected }: { data: SkillNodeData; selected: boolean }) {
  return (
    <div className={cn(
      "min-w-[185px] rounded-xl border-2 px-3 py-2.5 shadow-lg select-none",
      "border-yellow-500/60 bg-yellow-950/50",
      selected && "ring-2 ring-white/25"
    )}>
      <Handle
        type="target"
        position={Position.Top}
        id="input"
        style={{ background: "#eab308", border: "2px solid #a16207", width: 10, height: 10 }}
      />
      <div className="flex items-center gap-1.5 mb-1">
        <GitBranch className="h-3 w-3 text-yellow-400" />
        <span className="text-[0.55rem] font-bold uppercase tracking-widest text-yellow-400">Bedingung</span>
      </div>
      <p className="text-sm font-medium text-white leading-tight">{data.label || "—"}</p>
      {!!data.params.condition && (
        <p className="text-xs text-yellow-300/60 mt-0.5 truncate">{(data.params.condition as string).slice(0, 30)}</p>
      )}
      <div className="relative mt-2 h-8">
        <Handle
          type="source"
          position={Position.Bottom}
          id="true"
          style={{ left: "30%", background: "#22c55e", border: "2px solid #16a34a", width: 10, height: 10 }}
        />
        <span className="absolute left-[22%] bottom-[-14px] text-[9px] text-green-400 font-semibold leading-none">ja</span>
        <Handle
          type="source"
          position={Position.Bottom}
          id="false"
          style={{ left: "70%", background: "#ef4444", border: "2px solid #b91c1c", width: 10, height: 10 }}
        />
        <span className="absolute left-[64%] bottom-[-14px] text-[9px] text-red-400 font-semibold leading-none">nein</span>
      </div>
    </div>
  );
}

function DependencyNodeComp({ data, selected }: { data: SkillNodeData; selected: boolean }) {
  return (
    <div className={cn(
      "min-w-[185px] rounded-xl border-2 px-3 py-2.5 shadow-lg select-none",
      "border-purple-500/60 bg-purple-950/50",
      selected && "ring-2 ring-white/25"
    )}>
      <Handle
        type="target"
        position={Position.Top}
        id="input"
        style={{ background: "#a855f7", border: "2px solid #7e22ce", width: 10, height: 10 }}
      />
      <div className="flex items-center gap-1.5 mb-1">
        <Link2 className="h-3 w-3 text-purple-400" />
        <span className="text-[0.55rem] font-bold uppercase tracking-widest text-purple-400">Abhängigkeit</span>
      </div>
      <p className="text-sm font-medium text-white leading-tight">{data.label || "—"}</p>
      {!!data.params.package_id && (
        <p className="text-xs text-purple-300/60 mt-0.5 truncate">{data.params.package_id as string}</p>
      )}
      <Handle
        type="source"
        position={Position.Bottom}
        id="output"
        style={{ background: "#a855f7", border: "2px solid #7e22ce", width: 10, height: 10 }}
      />
    </div>
  );
}

function OutputNodeComp({ data, selected }: { data: SkillNodeData; selected: boolean }) {
  return (
    <div className={cn(
      "min-w-[185px] rounded-xl border-2 px-3 py-2.5 shadow-lg select-none",
      "border-green-500/60 bg-green-950/50",
      selected && "ring-2 ring-white/25"
    )}>
      <Handle
        type="target"
        position={Position.Top}
        id="input"
        style={{ background: "#22c55e", border: "2px solid #16a34a", width: 10, height: 10 }}
      />
      <div className="flex items-center gap-1.5 mb-1">
        <ArrowDownToLine className="h-3 w-3 text-green-400" />
        <span className="text-[0.55rem] font-bold uppercase tracking-widest text-green-400">Ausgabe</span>
      </div>
      <p className="text-sm font-medium text-white leading-tight">{data.label || "—"}</p>
    </div>
  );
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const NODE_TYPES: NodeTypes = {
  skillNode:      SkillNodeComp as any,
  conditionNode:  ConditionNodeComp as any,
  dependencyNode: DependencyNodeComp as any,
  outputNode:     OutputNodeComp as any,
};

// ── Node Palette (left sidebar) ────────────────────────────────────────────
function NodePalette() {
  const onDragStart = (event: React.DragEvent, item: { type: string; subtype: string; label: string }) => {
    event.dataTransfer.setData("application/skill-node", JSON.stringify(item));
    event.dataTransfer.effectAllowed = "move";
  };

  const colorMap = {
    blue:   "border-blue-500/40 bg-blue-950/30 hover:bg-blue-950/60 text-blue-300",
    yellow: "border-yellow-500/40 bg-yellow-950/30 hover:bg-yellow-950/60 text-yellow-300",
    purple: "border-purple-500/40 bg-purple-950/30 hover:bg-purple-950/60 text-purple-300",
    green:  "border-green-500/40 bg-green-950/30 hover:bg-green-950/60 text-green-300",
  };

  return (
    <div className="w-44 shrink-0 overflow-y-auto border-r border-white/10 bg-[hsl(var(--sidebar-bg,220_15%_8%))] p-3 flex flex-col gap-4">
      <p className="text-[0.6rem] font-semibold uppercase tracking-[0.18em] text-white/30 px-1">Knoten-Palette</p>
      {PALETTE.map(group => (
        <div key={group.group}>
          <p className="text-[0.55rem] uppercase tracking-widest text-white/35 mb-1.5 px-1">{group.group}</p>
          <div className="flex flex-col gap-1.5">
            {group.items.map(item => {
              const Icon = item.icon;
              return (
                <div
                  key={item.subtype}
                  className={cn(
                    "flex items-center gap-2 rounded-lg border px-2 py-1.5 text-xs",
                    "cursor-grab active:cursor-grabbing transition-colors",
                    colorMap[item.color]
                  )}
                  draggable
                  onDragStart={e => onDragStart(e, item)}
                >
                  <Icon className="h-3.5 w-3.5 shrink-0" />
                  <span className="truncate leading-tight">{item.label}</span>
                </div>
              );
            })}
          </div>
        </div>
      ))}
      <div className="mt-auto pt-3 border-t border-white/10">
        <p className="text-[0.55rem] text-white/20 leading-relaxed px-1">
          Knoten auf die Canvas ziehen, dann verbinden und speichern.
        </p>
      </div>
    </div>
  );
}

// ── Properties Panel (right sidebar) ──────────────────────────────────────
interface PropsPanelProps {
  node: SNode;
  onChange: (params: Record<string, unknown>) => void;
  onDelete: () => void;
}

function PropertiesPanel({ node, onChange, onDelete }: PropsPanelProps) {
  const d = node.data;
  const p = d.params;

  return (
    <div className="w-56 shrink-0 border-l border-white/10 bg-[hsl(var(--sidebar-bg,220_15%_8%))] p-4 flex flex-col gap-4 overflow-y-auto">
      <div>
        <p className="text-[0.55rem] font-bold uppercase tracking-widest text-white/30 mb-1">Eigenschaften</p>
        <p className="text-sm font-semibold text-white">{d.subtype}</p>
      </div>

      {/* Label — available on all node types */}
      <div>
        <label className="block text-xs text-white/50 mb-1">Label</label>
        <input
          type="text"
          placeholder="Bezeichnung"
          value={(p.label as string) || ""}
          onChange={e => onChange({ ...p, label: e.target.value })}
          className="w-full rounded-lg bg-white/5 border border-white/15 px-2 py-1.5 text-sm text-white placeholder-white/20 focus:outline-none focus:border-white/30"
        />
      </div>

      {/* Skill: skill_id */}
      {d.subtype === "skill" && (
        <div>
          <label className="block text-xs text-white/50 mb-1">Skill-ID</label>
          <input
            type="text"
            placeholder="z.B. summarize_text"
            value={(p.skill_id as string) || ""}
            onChange={e => onChange({ ...p, skill_id: e.target.value })}
            className="w-full rounded-lg bg-white/5 border border-white/15 px-2 py-1.5 text-sm text-white placeholder-white/20 focus:outline-none focus:border-white/30"
          />
          <p className="text-[10px] text-white/25 mt-1">QMD-Skill-Bezeichner aus den Agent-Skills.</p>
        </div>
      )}

      {/* Condition: condition expression */}
      {d.subtype === "condition" && (
        <div>
          <label className="block text-xs text-white/50 mb-1">Bedingung</label>
          <textarea
            rows={3}
            placeholder="z.B. result.confidence > 0.8"
            value={(p.condition as string) || ""}
            onChange={e => onChange({ ...p, condition: e.target.value })}
            className="w-full rounded-lg bg-white/5 border border-white/15 px-2 py-1.5 text-sm text-white placeholder-white/20 focus:outline-none focus:border-white/30 resize-none"
          />
          <p className="text-[10px] text-white/25 mt-1">Ausdruck der zu true oder false ausgewertet wird.</p>
        </div>
      )}

      {/* Dependency: package_id */}
      {d.subtype === "dependency" && (
        <div>
          <label className="block text-xs text-white/50 mb-1">Paket-ID</label>
          <input
            type="text"
            placeholder="UUID des Abhängigkeits-Pakets"
            value={(p.package_id as string) || ""}
            onChange={e => onChange({ ...p, package_id: e.target.value })}
            className="w-full rounded-lg bg-white/5 border border-white/15 px-2 py-1.5 text-sm text-white placeholder-white/20 focus:outline-none focus:border-white/30"
          />
          <p className="text-[10px] text-white/25 mt-1">Muss vor diesem Paket ausgeführt werden.</p>
        </div>
      )}

      {/* Output: info only */}
      {d.subtype === "output" && (
        <p className="text-xs text-white/35 leading-relaxed">
          Ausgabe-Knoten — sammelt das Ergebnis des Pakets.
        </p>
      )}

      <div className="mt-auto pt-3 border-t border-white/10">
        <button type="button" onClick={onDelete}
          className="flex items-center gap-1.5 text-xs text-red-400/60 hover:text-red-400 transition-colors"
        >
          <Trash2 className="h-3.5 w-3.5" />
          Knoten entfernen
        </button>
      </div>
    </div>
  );
}

// ── Main page ──────────────────────────────────────────────────────────────
export function SkillPackagesPage() {
  return (
    <ReactFlowProvider>
      <SkillPackagesPageInner />
    </ReactFlowProvider>
  );
}

let _nSeq = 0;
function genId(type: string) { return `${type}-${++_nSeq}-${Date.now()}`; }

function SkillPackagesPageInner() {
  const [packages, setPackages]       = useState<SkillPackage[]>([]);
  const [activePkgId, setActiveId]    = useState<string | null>(null);
  const [pkgName, setPkgName]         = useState("Neues Paket");
  const [pkgDescription, setPkgDesc]  = useState("");
  const [pkgEnabled, setEnabled]      = useState(true);
  const [saving, setSaving]           = useState(false);
  const [toast, setToast]             = useState<string | null>(null);

  const [nodes, setNodes, onNodesChange] = useNodesState<SNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [selectedId, setSelectedId]     = useState<string | null>(null);

  const rf = useReactFlow();

  const showToast = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(null), 3000);
  };

  // Load packages
  useEffect(() => {
    api.get<SkillPackage[]>("/admin/skill-packages")
      .then(setPackages)
      .catch(() => {});
  }, []);

  const loadPackage = (pkg: SkillPackage) => {
    setActiveId(pkg.id);
    setPkgName(pkg.name);
    setPkgDesc(pkg.description || "");
    setEnabled(pkg.enabled);
    setNodes((pkg.nodes || []) as SNode[]);
    setEdges(pkg.edges || []);
    setSelectedId(null);
  };

  const newPackage = () => {
    setActiveId(null);
    setPkgName("Neues Paket");
    setPkgDesc("");
    setEnabled(true);
    setNodes([]);
    setEdges([]);
    setSelectedId(null);
  };

  const savePackage = async () => {
    setSaving(true);
    try {
      const payload = {
        name: pkgName,
        description: pkgDescription,
        enabled: pkgEnabled,
        nodes: nodes.map(n => ({ id: n.id, type: n.type, position: n.position, data: n.data })),
        edges: edges.map(e => ({
          id: e.id, source: e.source, target: e.target,
          sourceHandle: e.sourceHandle ?? null,
          targetHandle: e.targetHandle ?? null,
        })),
      };
      if (activePkgId) {
        const updated = await api.put<SkillPackage>(`/admin/skill-packages/${activePkgId}`, payload);
        setPackages(ps => ps.map(p => p.id === activePkgId ? updated : p));
      } else {
        const created = await api.post<SkillPackage>("/admin/skill-packages", payload);
        setPackages(ps => [...ps, created]);
        setActiveId(created.id);
      }
      showToast("Gespeichert ✓");
    } catch (e) {
      showToast(e instanceof Error ? e.message : "Fehler beim Speichern");
    } finally {
      setSaving(false);
    }
  };

  const deletePackage = async () => {
    if (!activePkgId || !confirm(`Paket "${pkgName}" wirklich löschen?`)) return;
    try {
      await api.delete(`/admin/skill-packages/${activePkgId}`);
      setPackages(ps => ps.filter(p => p.id !== activePkgId));
      newPackage();
      showToast("Gelöscht");
    } catch (e) {
      showToast(e instanceof Error ? e.message : "Fehler");
    }
  };

  const toggleEnabled = () => {
    setEnabled(e => !e);
  };

  const onConnect = useCallback((c: Connection) => {
    setEdges(es => addEdge({
      ...c,
      animated: true,
      style: { stroke: "#6366f1", strokeWidth: 2 },
    }, es));
  }, [setEdges]);

  const onDrop = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    const raw = event.dataTransfer.getData("application/skill-node");
    if (!raw) return;
    const { type, subtype, label } = JSON.parse(raw) as { type: string; subtype: string; label: string };
    const position = rf.screenToFlowPosition({ x: event.clientX, y: event.clientY });
    setNodes(ns => [...ns, {
      id: genId(type),
      type,
      position,
      data: { subtype, label, params: defaultParams(subtype) },
    } as SNode]);
  }, [rf, setNodes]);

  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
  }, []);

  const onNodeClick = useCallback((_: React.MouseEvent, node: Node) => {
    setSelectedId(node.id);
  }, []);

  const onPaneClick = useCallback(() => setSelectedId(null), []);

  const selectedNode = nodes.find(n => n.id === selectedId) as SNode | undefined;

  const updateParams = (params: Record<string, unknown>) => {
    if (!selectedId) return;
    setNodes(ns => ns.map(n =>
      n.id === selectedId ? { ...n, data: { ...n.data, params, label: (params.label as string) || n.data.label } } : n
    ) as SNode[]);
  };

  const deleteSelected = () => {
    if (!selectedId) return;
    setNodes(ns => (ns as SNode[]).filter(n => n.id !== selectedId));
    setEdges(es => es.filter(e => e.source !== selectedId && e.target !== selectedId));
    setSelectedId(null);
  };

  const isDark = typeof document !== "undefined" && document.documentElement.classList.contains("dark");

  return (
    <div className="flex h-full flex-col">
      {/* ── Top bar ── */}
      <div className="flex flex-wrap items-center gap-2 border-b border-white/10 px-4 py-2.5 shrink-0">
        <Package className="h-5 w-5 text-indigo-400 shrink-0" />
        <h1 className="text-base font-semibold text-white mr-1">Skill-Pakete</h1>

        {/* Package selector */}
        <select
          value={activePkgId || ""}
          onChange={e => {
            const pkg = packages.find(p => p.id === e.target.value);
            if (pkg) loadPackage(pkg); else newPackage();
          }}
          className="rounded-lg bg-white/5 border border-white/15 px-2.5 py-1.5 text-sm text-white focus:outline-none focus:border-indigo-500/50"
        >
          <option value="">— Neues Paket —</option>
          {packages.map(p => (
            <option key={p.id} value={p.id}>{p.name}{p.enabled ? "" : " (inaktiv)"}</option>
          ))}
        </select>

        {/* Name */}
        <input
          type="text"
          value={pkgName}
          onChange={e => setPkgName(e.target.value)}
          placeholder="Paket-Name"
          className="rounded-lg bg-white/5 border border-white/15 px-2.5 py-1.5 text-sm text-white placeholder-white/25 focus:outline-none focus:border-indigo-500/50 w-40"
        />

        {/* Description */}
        <input
          type="text"
          value={pkgDescription}
          onChange={e => setPkgDesc(e.target.value)}
          placeholder="Beschreibung (optional)"
          className="rounded-lg bg-white/5 border border-white/15 px-2.5 py-1.5 text-sm text-white placeholder-white/25 focus:outline-none focus:border-indigo-500/50 w-52"
        />

        {/* Toggle */}
        <button type="button" onClick={toggleEnabled}
          className={cn(
            "flex items-center gap-1.5 text-sm px-2.5 py-1.5 rounded-lg border transition-colors",
            pkgEnabled
              ? "border-green-500/40 bg-green-950/30 text-green-400 hover:bg-green-950/50"
              : "border-white/15 bg-white/5 text-white/35 hover:bg-white/10"
          )}
        >
          {pkgEnabled ? <ToggleRight className="h-4 w-4" /> : <ToggleLeft className="h-4 w-4" />}
          {pkgEnabled ? "Aktiv" : "Inaktiv"}
        </button>

        <div className="flex-1" />

        <button type="button" onClick={newPackage}
          className="flex items-center gap-1.5 rounded-lg border border-white/15 bg-white/5 px-2.5 py-1.5 text-sm text-white hover:bg-white/10 transition-colors"
        >
          <Plus className="h-3.5 w-3.5" />
          Neu
        </button>

        <button type="button" onClick={savePackage} disabled={saving}
          className="flex items-center gap-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 px-3 py-1.5 text-sm text-white transition-colors"
        >
          <Save className="h-3.5 w-3.5" />
          {saving ? "Speichere…" : "Speichern"}
        </button>

        {activePkgId && (
          <button type="button" onClick={deletePackage}
            className="flex items-center gap-1.5 rounded-lg border border-red-500/40 bg-red-950/20 px-2.5 py-1.5 text-sm text-red-400 hover:bg-red-950/40 transition-colors"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        )}
      </div>

      {/* ── Toast ── */}
      {toast && (
        <div className="px-4 py-1.5 bg-indigo-900/40 border-b border-indigo-500/30 text-sm text-indigo-200">
          {toast}
        </div>
      )}

      {/* ── Main area ── */}
      <div className="flex flex-1 overflow-hidden">
        <NodePalette />

        {/* Canvas */}
        <div className="flex-1 relative" onDrop={onDrop} onDragOver={onDragOver}>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onNodeClick={onNodeClick}
            onPaneClick={onPaneClick}
            nodeTypes={NODE_TYPES}
            colorMode={isDark ? "dark" : "light"}
            fitView
            snapToGrid
            snapGrid={[15, 15]}
            defaultEdgeOptions={{
              animated: true,
              style: { stroke: "#6366f1", strokeWidth: 2 },
            }}
          >
            <Background
              variant={BackgroundVariant.Dots}
              gap={20}
              size={1}
              color="rgba(255,255,255,0.05)"
            />
            <Controls />
            <MiniMap
              nodeColor={n =>
                n.type === "skillNode"      ? "#3b82f6" :
                n.type === "conditionNode"  ? "#eab308" :
                n.type === "dependencyNode" ? "#a855f7" : "#22c55e"
              }
            />
            {nodes.length === 0 && (
              <Panel position="top-center" style={{ marginTop: 48 }}>
                <div className="text-center pointer-events-none">
                  <p className="text-white/25 text-base">Knoten aus der Palette auf die Canvas ziehen</p>
                  <p className="text-white/15 text-sm mt-1">Skills bündeln → Verbinden → Speichern</p>
                </div>
              </Panel>
            )}
          </ReactFlow>
        </div>

        {/* Properties panel */}
        {selectedNode && (
          <PropertiesPanel
            node={selectedNode}
            onChange={updateParams}
            onDelete={deleteSelected}
          />
        )}
      </div>
    </div>
  );
}
