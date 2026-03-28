import "@xyflow/react/dist/style.css";
import React, { useCallback, useEffect, useRef, useState } from "react";
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
  Workflow,
  Zap,
  Clock,
  Calendar,
  Users,
  MessageCircle,
  Bot,
  Inbox,
  EyeOff,
  ArrowRight,
  Plus,
  Save,
  Trash2,
  ToggleLeft,
  ToggleRight,
  Filter,
} from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

// ── Types ──────────────────────────────────────────────────────────────────
interface ButlerNodeData {
  subtype: string;
  label: string;
  params: Record<string, unknown>;
  [key: string]: unknown; // React Flow requires index signature
}

interface ButlerFlow {
  id: string;
  name: string;
  enabled: boolean;
  nodes: Node<ButlerNodeData>[];
  edges: Edge[];
}

type BNode = Node<ButlerNodeData>;

// ── Default params per subtype ─────────────────────────────────────────────
function defaultParams(subtype: string): Record<string, unknown> {
  switch (subtype) {
    case "message_received": return { channel: "all" };
    case "time_window":      return { from: "23:00", to: "08:00" };
    case "day_of_week":      return { days: ["mo","di","mi","do","fr","sa","so"] };
    case "contact_known":    return {};
    case "message_contains": return { keyword: "" };
    case "agent_reply":      return { agent_id: "" };
    case "queue":            return {};
    case "ignore":           return {};
    case "forward":          return { agent_id: "" };
    default:                 return {};
  }
}

// ── Palette definitions ────────────────────────────────────────────────────
const PALETTE = [
  {
    group: "Trigger",
    color: "green" as const,
    items: [
      { type: "triggerNode",   subtype: "message_received", label: "Nachricht empfangen", icon: MessageCircle },
    ],
  },
  {
    group: "Bedingung",
    color: "blue" as const,
    items: [
      { type: "conditionNode", subtype: "time_window",      label: "Zeitfenster",         icon: Clock },
      { type: "conditionNode", subtype: "day_of_week",      label: "Wochentag",           icon: Calendar },
      { type: "conditionNode", subtype: "contact_known",    label: "Kontakt bekannt?",    icon: Users },
      { type: "conditionNode", subtype: "message_contains", label: "Text enthält",        icon: Filter },
    ],
  },
  {
    group: "Aktion",
    color: "orange" as const,
    items: [
      { type: "actionNode", subtype: "agent_reply", label: "Agent antwortet", icon: Bot },
      { type: "actionNode", subtype: "queue",       label: "In Warteschlange", icon: Inbox },
      { type: "actionNode", subtype: "ignore",      label: "Ignorieren",      icon: EyeOff },
      { type: "actionNode", subtype: "forward",     label: "Weiterleiten",    icon: ArrowRight },
    ],
  },
];

// ── Summary text for node preview ─────────────────────────────────────────
function paramSummary(subtype: string, params: Record<string, unknown>): string {
  switch (subtype) {
    case "message_received": {
      const ch = (params.channel as string) || "all";
      return ch === "all" ? "Alle Kanäle" : ch.charAt(0).toUpperCase() + ch.slice(1);
    }
    case "time_window":
      return `${params.from ?? "?"}–${params.to ?? "?"}`;
    case "day_of_week": {
      const days = (params.days as string[]) ?? [];
      return days.map(d => d.charAt(0).toUpperCase() + d.slice(1)).join(" ");
    }
    case "message_contains":
      return params.keyword ? `"${params.keyword}"` : "—";
    case "agent_reply":
    case "forward":
      return (params.agent_id as string) || "—";
    default:
      return "";
  }
}

// ── Custom node components ─────────────────────────────────────────────────
function TriggerNodeComp({ data, selected }: { data: ButlerNodeData; selected: boolean }) {
  const summary = paramSummary(data.subtype, data.params);
  return (
    <div className={cn(
      "min-w-[185px] rounded-xl border-2 px-3 py-2.5 shadow-lg select-none",
      "border-green-500/60 bg-green-950/50",
      selected && "ring-2 ring-white/25"
    )}>
      <div className="flex items-center gap-1.5 mb-1">
        <Zap className="h-3 w-3 text-green-400" />
        <span className="text-[0.55rem] font-bold uppercase tracking-widest text-green-400">Trigger</span>
      </div>
      <p className="text-sm font-medium text-white leading-tight">{data.label}</p>
      {summary && <p className="text-xs text-green-300/60 mt-0.5">{summary}</p>}
      <Handle
        type="source"
        position={Position.Right}
        id="output"
        style={{ background: "#22c55e", border: "2px solid #16a34a", width: 10, height: 10 }}
      />
    </div>
  );
}

function ConditionNodeComp({ data, selected }: { data: ButlerNodeData; selected: boolean }) {
  const summary = paramSummary(data.subtype, data.params);
  return (
    <div className={cn(
      "min-w-[185px] rounded-xl border-2 px-3 py-2.5 shadow-lg select-none",
      "border-blue-500/60 bg-blue-950/50",
      selected && "ring-2 ring-white/25"
    )}>
      <Handle
        type="target"
        position={Position.Left}
        id="input"
        style={{ background: "#3b82f6", border: "2px solid #1d4ed8", width: 10, height: 10 }}
      />
      <div className="flex items-center gap-1.5 mb-1">
        <span className="text-[0.55rem] font-bold uppercase tracking-widest text-blue-400">Bedingung</span>
      </div>
      <p className="text-sm font-medium text-white leading-tight">{data.label}</p>
      {summary && <p className="text-xs text-blue-300/60 mt-0.5">{summary}</p>}
      {/* true / false output handles */}
      <div className="relative mt-2 h-8">
        <Handle
          type="source"
          position={Position.Right}
          id="true"
          style={{ top: "25%", background: "#22c55e", border: "2px solid #16a34a", width: 10, height: 10 }}
        />
        <span className="absolute right-[-22px] top-[0px] text-[9px] text-green-400 font-semibold leading-none">ja</span>
        <Handle
          type="source"
          position={Position.Right}
          id="false"
          style={{ top: "75%", background: "#ef4444", border: "2px solid #b91c1c", width: 10, height: 10 }}
        />
        <span className="absolute right-[-24px] bottom-[0px] text-[9px] text-red-400 font-semibold leading-none">nein</span>
      </div>
    </div>
  );
}

function ActionNodeComp({ data, selected }: { data: ButlerNodeData; selected: boolean }) {
  const summary = paramSummary(data.subtype, data.params);
  return (
    <div className={cn(
      "min-w-[185px] rounded-xl border-2 px-3 py-2.5 shadow-lg select-none",
      "border-orange-500/60 bg-orange-950/50",
      selected && "ring-2 ring-white/25"
    )}>
      <Handle
        type="target"
        position={Position.Left}
        id="input"
        style={{ background: "#f97316", border: "2px solid #c2410c", width: 10, height: 10 }}
      />
      <div className="flex items-center gap-1.5 mb-1">
        <Zap className="h-3 w-3 text-orange-400" />
        <span className="text-[0.55rem] font-bold uppercase tracking-widest text-orange-400">Aktion</span>
      </div>
      <p className="text-sm font-medium text-white leading-tight">{data.label}</p>
      {summary && <p className="text-xs text-orange-300/60 mt-0.5">{summary}</p>}
      <Handle
        type="source"
        position={Position.Right}
        id="output"
        style={{ background: "#f97316", border: "2px solid #c2410c", width: 10, height: 10 }}
      />
    </div>
  );
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const NODE_TYPES: NodeTypes = {
  triggerNode:   TriggerNodeComp as any,
  conditionNode: ConditionNodeComp as any,
  actionNode:    ActionNodeComp as any,
};

// ── Node Palette (left sidebar) ────────────────────────────────────────────
function NodePalette() {
  const onDragStart = (event: React.DragEvent, item: { type: string; subtype: string; label: string }) => {
    event.dataTransfer.setData("application/butler-node", JSON.stringify(item));
    event.dataTransfer.effectAllowed = "move";
  };

  const colorMap = {
    green:  "border-green-500/40 bg-green-950/30 hover:bg-green-950/60 text-green-300",
    blue:   "border-blue-500/40 bg-blue-950/30 hover:bg-blue-950/60 text-blue-300",
    orange: "border-orange-500/40 bg-orange-950/30 hover:bg-orange-950/60 text-orange-300",
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
                    colorMap[group.color]
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
  node: BNode;
  agents: { id: string; name: string }[];
  onChange: (params: Record<string, unknown>) => void;
  onDelete: () => void;
}

function PropertiesPanel({ node, agents, onChange, onDelete }: PropsPanelProps) {
  const d = node.data;
  const p = d.params;
  const ALL_DAYS = ["mo","di","mi","do","fr","sa","so"];
  const DAY_LABEL: Record<string, string> = { mo:"Mo",di:"Di",mi:"Mi",do:"Do",fr:"Fr",sa:"Sa",so:"So" };

  return (
    <div className="w-56 shrink-0 border-l border-white/10 bg-[hsl(var(--sidebar-bg,220_15%_8%))] p-4 flex flex-col gap-4 overflow-y-auto">
      <div>
        <p className="text-[0.55rem] font-bold uppercase tracking-widest text-white/30 mb-1">Eigenschaften</p>
        <p className="text-sm font-semibold text-white">{d.label}</p>
      </div>

      {/* Trigger: Nachricht empfangen */}
      {d.subtype === "message_received" && (
        <div>
          <label className="block text-xs text-white/50 mb-1">Kanal</label>
          <select
            value={(p.channel as string) || "all"}
            onChange={e => onChange({ ...p, channel: e.target.value })}
            className="w-full rounded-lg bg-white/5 border border-white/15 px-2 py-1.5 text-sm text-white focus:outline-none focus:border-white/30"
          >
            <option value="all">Alle Kanäle</option>
            <option value="whatsapp">WhatsApp</option>
            <option value="telegram">Telegram</option>
            <option value="discord">Discord</option>
            <option value="matrix">Matrix</option>
          </select>
        </div>
      )}

      {/* Condition: Zeitfenster */}
      {d.subtype === "time_window" && (
        <div className="flex flex-col gap-2">
          <div>
            <label className="block text-xs text-white/50 mb-1">Von</label>
            <input type="time" value={(p.from as string) || "23:00"}
              onChange={e => onChange({ ...p, from: e.target.value })}
              className="w-full rounded-lg bg-white/5 border border-white/15 px-2 py-1.5 text-sm text-white focus:outline-none focus:border-white/30"
            />
          </div>
          <div>
            <label className="block text-xs text-white/50 mb-1">Bis</label>
            <input type="time" value={(p.to as string) || "08:00"}
              onChange={e => onChange({ ...p, to: e.target.value })}
              className="w-full rounded-lg bg-white/5 border border-white/15 px-2 py-1.5 text-sm text-white focus:outline-none focus:border-white/30"
            />
          </div>
          <p className="text-[10px] text-white/25">Übernacht (23:00–08:00) wird unterstützt.</p>
        </div>
      )}

      {/* Condition: Wochentag */}
      {d.subtype === "day_of_week" && (
        <div>
          <label className="block text-xs text-white/50 mb-2">Tage</label>
          <div className="flex flex-wrap gap-1">
            {ALL_DAYS.map(day => {
              const days = (p.days as string[]) || ALL_DAYS;
              const active = days.includes(day);
              return (
                <button key={day} type="button"
                  onClick={() => {
                    const next = active ? days.filter(d => d !== day) : [...days, day];
                    onChange({ ...p, days: next });
                  }}
                  className={cn(
                    "w-8 py-1 rounded text-xs font-medium transition-colors",
                    active ? "bg-blue-600 text-white" : "bg-white/5 text-white/35 hover:bg-white/10"
                  )}
                >
                  {DAY_LABEL[day]}
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* Condition: Text enthält */}
      {d.subtype === "message_contains" && (
        <div>
          <label className="block text-xs text-white/50 mb-1">Stichwort</label>
          <input type="text" placeholder="z.B. dringend"
            value={(p.keyword as string) || ""}
            onChange={e => onChange({ ...p, keyword: e.target.value })}
            className="w-full rounded-lg bg-white/5 border border-white/15 px-2 py-1.5 text-sm text-white placeholder-white/20 focus:outline-none focus:border-white/30"
          />
        </div>
      )}

      {/* Action: Agent antwortet / Weiterleiten */}
      {(d.subtype === "agent_reply" || d.subtype === "forward") && (
        <div>
          <label className="block text-xs text-white/50 mb-1">Agent</label>
          <select value={(p.agent_id as string) || ""}
            onChange={e => onChange({ ...p, agent_id: e.target.value })}
            className="w-full rounded-lg bg-white/5 border border-white/15 px-2 py-1.5 text-sm text-white focus:outline-none focus:border-white/30"
          >
            <option value="">— wählen —</option>
            {agents.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
        </div>
      )}

      {/* Info-only nodes */}
      {d.subtype === "contact_known" && (
        <p className="text-xs text-white/35 leading-relaxed">
          Prüft ob der Absender in der HydraHive-Kontaktliste eingetragen ist.
        </p>
      )}
      {d.subtype === "ignore" && (
        <p className="text-xs text-white/35 leading-relaxed">
          Nachricht wird still ignoriert — keine Antwort, kein Logging.
        </p>
      )}
      {d.subtype === "queue" && (
        <p className="text-xs text-white/35 leading-relaxed">
          Nachricht wird in der Warteschlange für spätere Bearbeitung gespeichert.
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
export function ButlerPage() {
  return (
    <ReactFlowProvider>
      <ButlerPageInner />
    </ReactFlowProvider>
  );
}

let _nSeq = 0;
function genId(type: string) { return `${type}-${++_nSeq}-${Date.now()}`; }

function ButlerPageInner() {
  const [flows, setFlows]           = useState<ButlerFlow[]>([]);
  const [activeFlowId, setActiveId] = useState<string | null>(null);
  const [flowName, setFlowName]     = useState("Neuer Flow");
  const [flowEnabled, setEnabled]   = useState(true);
  const [saving, setSaving]         = useState(false);
  const [toast, setToast]           = useState<string | null>(null);

  const [nodes, setNodes, onNodesChange] = useNodesState<BNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [selectedId, setSelectedId]     = useState<string | null>(null);

  const [agents, setAgents] = useState<{ id: string; name: string }[]>([]);
  const rf = useReactFlow();

  const showToast = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(null), 3000);
  };

  // Load flows + agents
  useEffect(() => {
    api.get<ButlerFlow[]>("/admin/butler/flows")
      .then(setFlows)
      .catch(() => {});

    api.get<Record<string, { config: { identity?: string } }>>("/agents")
      .then(res => {
        const list = Object.entries(res || {}).map(([id, a]) => ({
          id,
          name: a?.config?.identity ? `${id} — ${String(a.config.identity).slice(0, 30)}` : id,
        }));
        setAgents(list);
      })
      .catch(() => {});
  }, []);

  const loadFlow = (flow: ButlerFlow) => {
    setActiveId(flow.id);
    setFlowName(flow.name);
    setEnabled(flow.enabled);
    setNodes((flow.nodes || []) as BNode[]);
    setEdges(flow.edges || []);
    setSelectedId(null);
  };

  const newFlow = () => {
    setActiveId(null);
    setFlowName("Neuer Flow");
    setEnabled(true);
    setNodes([]);
    setEdges([]);
    setSelectedId(null);
  };

  const saveFlow = async () => {
    setSaving(true);
    try {
      const payload = {
        name: flowName,
        enabled: flowEnabled,
        nodes: nodes.map(n => ({ id: n.id, type: n.type, position: n.position, data: n.data })),
        edges: edges.map(e => ({
          id: e.id, source: e.source, target: e.target,
          sourceHandle: e.sourceHandle ?? null,
          targetHandle: e.targetHandle ?? null,
        })),
      };
      if (activeFlowId) {
        const updated = await api.put<ButlerFlow>(`/admin/butler/flows/${activeFlowId}`, payload);
        setFlows(fs => fs.map(f => f.id === activeFlowId ? updated : f));
      } else {
        const created = await api.post<ButlerFlow>("/admin/butler/flows", payload);
        setFlows(fs => [...fs, created]);
        setActiveId(created.id);
      }
      showToast("Gespeichert ✓");
    } catch (e) {
      showToast(e instanceof Error ? e.message : "Fehler beim Speichern");
    } finally {
      setSaving(false);
    }
  };

  const deleteFlow = async () => {
    if (!activeFlowId || !confirm(`Flow "${flowName}" wirklich löschen?`)) return;
    try {
      await api.delete(`/admin/butler/flows/${activeFlowId}`);
      setFlows(fs => fs.filter(f => f.id !== activeFlowId));
      newFlow();
      showToast("Gelöscht");
    } catch (e) {
      showToast(e instanceof Error ? e.message : "Fehler");
    }
  };

  const toggleFlow = async () => {
    if (!activeFlowId) { setEnabled(e => !e); return; }
    try {
      const res = await api.patch<{ enabled: boolean }>(`/admin/butler/flows/${activeFlowId}/toggle`, {});
      setEnabled(res.enabled);
      setFlows(fs => fs.map(f => f.id === activeFlowId ? { ...f, enabled: res.enabled } : f));
    } catch (e) {
      showToast(e instanceof Error ? e.message : "Fehler");
    }
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
    const raw = event.dataTransfer.getData("application/butler-node");
    if (!raw) return;
    const { type, subtype, label } = JSON.parse(raw) as { type: string; subtype: string; label: string };
    const position = rf.screenToFlowPosition({ x: event.clientX, y: event.clientY });
    setNodes(ns => [...ns, {
      id: genId(type),
      type,
      position,
      data: { subtype, label, params: defaultParams(subtype) },
    } as BNode]);
  }, [rf, setNodes]);

  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
  }, []);

  const onNodeClick = useCallback((_: React.MouseEvent, node: Node) => {
    setSelectedId(node.id);
  }, []);

  const onPaneClick = useCallback(() => setSelectedId(null), []);

  const selectedNode = nodes.find(n => n.id === selectedId) as BNode | undefined;

  const updateParams = (params: Record<string, unknown>) => {
    if (!selectedId) return;
    setNodes(ns => ns.map(n =>
      n.id === selectedId ? { ...n, data: { ...n.data, params } } : n
    ) as BNode[]);
  };

  const deleteSelected = () => {
    if (!selectedId) return;
    setNodes(ns => (ns as BNode[]).filter(n => n.id !== selectedId));
    setEdges(es => es.filter(e => e.source !== selectedId && e.target !== selectedId));
    setSelectedId(null);
  };

  const isDark = typeof document !== "undefined" && document.documentElement.classList.contains("dark");

  return (
    <div className="flex h-full flex-col">
      {/* ── Top bar ── */}
      <div className="flex flex-wrap items-center gap-2 border-b border-white/10 px-4 py-2.5 shrink-0">
        <Workflow className="h-5 w-5 text-indigo-400 shrink-0" />
        <h1 className="text-base font-semibold text-white mr-1">Butler</h1>

        {/* Flow selector */}
        <select
          value={activeFlowId || ""}
          onChange={e => {
            const flow = flows.find(f => f.id === e.target.value);
            if (flow) loadFlow(flow); else newFlow();
          }}
          className="rounded-lg bg-white/5 border border-white/15 px-2.5 py-1.5 text-sm text-white focus:outline-none focus:border-indigo-500/50"
        >
          <option value="">— Neuer Flow —</option>
          {flows.map(f => (
            <option key={f.id} value={f.id}>{f.name}{f.enabled ? "" : " (inaktiv)"}</option>
          ))}
        </select>

        {/* Name */}
        <input
          type="text"
          value={flowName}
          onChange={e => setFlowName(e.target.value)}
          placeholder="Flow-Name"
          className="rounded-lg bg-white/5 border border-white/15 px-2.5 py-1.5 text-sm text-white placeholder-white/25 focus:outline-none focus:border-indigo-500/50 w-40"
        />

        {/* Toggle */}
        <button type="button" onClick={toggleFlow}
          className={cn(
            "flex items-center gap-1.5 text-sm px-2.5 py-1.5 rounded-lg border transition-colors",
            flowEnabled
              ? "border-green-500/40 bg-green-950/30 text-green-400 hover:bg-green-950/50"
              : "border-white/15 bg-white/5 text-white/35 hover:bg-white/10"
          )}
        >
          {flowEnabled ? <ToggleRight className="h-4 w-4" /> : <ToggleLeft className="h-4 w-4" />}
          {flowEnabled ? "Aktiv" : "Inaktiv"}
        </button>

        <div className="flex-1" />

        <button type="button" onClick={newFlow}
          className="flex items-center gap-1.5 rounded-lg border border-white/15 bg-white/5 px-2.5 py-1.5 text-sm text-white hover:bg-white/10 transition-colors"
        >
          <Plus className="h-3.5 w-3.5" />
          Neu
        </button>

        <button type="button" onClick={saveFlow} disabled={saving}
          className="flex items-center gap-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 px-3 py-1.5 text-sm text-white transition-colors"
        >
          <Save className="h-3.5 w-3.5" />
          {saving ? "Speichere…" : "Speichern"}
        </button>

        {activeFlowId && (
          <button type="button" onClick={deleteFlow}
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
                n.type === "triggerNode"   ? "#22c55e" :
                n.type === "conditionNode" ? "#3b82f6" : "#f97316"
              }
            />
            {nodes.length === 0 && (
              <Panel position="top-center" style={{ marginTop: 48 }}>
                <div className="text-center pointer-events-none">
                  <p className="text-white/25 text-base">Knoten aus der Palette auf die Canvas ziehen</p>
                  <p className="text-white/15 text-sm mt-1">Verbinden → Speichern → Läuft automatisch</p>
                </div>
              </Panel>
            )}
          </ReactFlow>
        </div>

        {/* Properties panel */}
        {selectedNode && (
          <PropertiesPanel
            node={selectedNode}
            agents={agents}
            onChange={updateParams}
            onDelete={deleteSelected}
          />
        )}
      </div>
    </div>
  );
}
