import "@xyflow/react/dist/style.css";
import { useCallback, useEffect, useState } from "react";
import {
  ReactFlow, ReactFlowProvider, Background, Controls,
  addEdge, useNodesState, useEdgesState, useReactFlow,
  Handle, Position, BackgroundVariant, Panel,
  type Connection, type Node,
} from "@xyflow/react";
import {
  FolderOpen, Filter, FolderInput, Copy, TextCursorInput,
  Bot, Bell, Play, Plus, Save, Trash2, Loader2, CheckCircle2,
  AlertCircle, Power, ChevronDown, ChevronRight, X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useTranslation } from "react-i18next";
import { ConfirmDialog } from "@/components/ConfirmDialog";

const API = "/api";
function authHeaders() {
  const token = localStorage.getItem("hydrahive_token") || "";
  return { "Content-Type": "application/json", Authorization: `Bearer ${token}` };
}

// ── Node-Definitionen ─────────────────────────────────────────────────────────

const NODE_PALETTE = [
  { type: "folder_watch", label: "Ordner beobachten", icon: FolderOpen,      color: "emerald",  desc: "Trigger: neue Datei im Ordner" },
  { type: "type_filter",  label: "Typ-Filter",         icon: Filter,          color: "amber",    desc: "Nur bestimmte Dateiendungen" },
  { type: "move",         label: "Verschieben",         icon: FolderInput,     color: "blue",     desc: "Datei in Ordner verschieben" },
  { type: "copy",         label: "Kopieren",            icon: Copy,            color: "indigo",   desc: "Datei in Ordner kopieren" },
  { type: "rename",       label: "Umbenennen",          icon: TextCursorInput, color: "purple",   desc: "Umbenennen nach Muster" },
  { type: "agent_task",   label: "Agent beauftragen",   icon: Bot,             color: "violet",   desc: "Agent mit Datei beauftragen" },
  { type: "notify",       label: "Benachrichtigen",     icon: Bell,            color: "teal",     desc: "Benachrichtigung senden" },
] as const;

type PaletteType = typeof NODE_PALETTE[number]["type"];

const COLOR_MAP: Record<string, { bg: string; border: string; badge: string; handle: string }> = {
  emerald: { bg: "bg-emerald-950/60", border: "border-emerald-500/60", badge: "text-emerald-400", handle: "#34d399" },
  amber:   { bg: "bg-amber-950/60",   border: "border-amber-500/60",   badge: "text-amber-400",   handle: "#fbbf24" },
  blue:    { bg: "bg-blue-950/60",    border: "border-blue-500/60",    badge: "text-blue-400",    handle: "#60a5fa" },
  indigo:  { bg: "bg-indigo-950/60",  border: "border-indigo-500/60",  badge: "text-indigo-400",  handle: "#818cf8" },
  purple:  { bg: "bg-purple-950/60",  border: "border-purple-500/60",  badge: "text-purple-400",  handle: "#c084fc" },
  violet:  { bg: "bg-violet-950/60",  border: "border-violet-500/60",  badge: "text-violet-400",  handle: "#a78bfa" },
  teal:    { bg: "bg-teal-950/60",    border: "border-teal-500/60",    badge: "text-teal-400",    handle: "#2dd4bf" },
};

function getPalette(nodeType: string) {
  return NODE_PALETTE.find(n => n.type === nodeType);
}

// ── Node-Komponenten ──────────────────────────────────────────────────────────

function PipelineNode({ data, selected, type: nodeType }: { data: any; selected: boolean; type: string }) {
  const pal = getPalette(nodeType);
  const colors = COLOR_MAP[pal?.color ?? "blue"];
  const Icon = pal ? pal.icon : FolderOpen;
  const isSource = nodeType !== "folder_watch";
  const isTarget = nodeType !== "folder_watch";

  return (
    <div className={cn(
      "min-w-[190px] max-w-[240px] rounded-xl border-2 px-3 py-2.5 shadow-lg select-none",
      colors.bg, colors.border, selected && "ring-2 ring-white/25"
    )}>
      <div className="flex items-center gap-1.5 mb-1">
        <Icon className={cn("h-3 w-3", colors.badge)} />
        <span className={cn("text-[0.55rem] font-bold uppercase tracking-widest", colors.badge)}>
          {pal?.label ?? nodeType}
        </span>
      </div>
      <p className="text-sm font-medium text-white leading-tight">{data.label || pal?.label}</p>
      {data.subtitle && <p className="text-[0.65rem] text-white/40 mt-0.5 font-mono truncate">{data.subtitle}</p>}
      {isTarget && (
        <Handle type="target" position={Position.Left} id="in"
          style={{ background: colors.handle, border: "2px solid rgba(0,0,0,0.4)", width: 10, height: 10 }} />
      )}
      {isSource && (
        <Handle type="source" position={Position.Right} id="out"
          style={{ background: colors.handle, border: "2px solid rgba(0,0,0,0.4)", width: 10, height: 10 }} />
      )}
    </div>
  );
}

const NODE_TYPES = Object.fromEntries(
  NODE_PALETTE.map(p => [p.type, PipelineNode])
);

// ── Node-Config-Panel ─────────────────────────────────────────────────────────

function NodeConfigPanel({ node, onChange, onClose }: {
  node: Node;
  onChange: (id: string, data: any) => void;
  onClose: () => void;
}) {
  const pal = getPalette(node.type ?? "");
  const data = node.data as any;

  function field(key: string, label: string, placeholder: string, hint?: string) {
    return (
      <div className="space-y-1">
        <label className="text-xs text-white/50 uppercase tracking-wide">{label}</label>
        <input
          value={data[key] ?? ""}
          onChange={e => onChange(node.id, { ...data, [key]: e.target.value })}
          placeholder={placeholder}
          className="w-full px-2.5 py-1.5 text-xs bg-zinc-800 border border-zinc-700 rounded-lg text-white focus:outline-none focus:ring-1 focus:ring-indigo-500 font-mono"
        />
        {hint && <p className="text-[0.6rem] text-white/30">{hint}</p>}
      </div>
    );
  }

  return (
    <div className="absolute right-3 top-12 z-20 w-64 bg-zinc-900 border border-zinc-700 rounded-xl p-4 space-y-3 shadow-2xl">
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold text-white/70">{pal?.label ?? node.type}</span>
        <button onClick={onClose} className="text-white/40 hover:text-white/80"><X className="h-3.5 w-3.5" /></button>
      </div>

      {field("label", "Bezeichnung", pal?.label ?? "")}

      {node.type === "folder_watch" && (
        <>
          {field("path", "Ordnerpfad", "/projects/mein-projekt", "Absoluter Pfad zum beobachteten Ordner")}
          <div className="space-y-1">
            <label className="text-xs text-white/50 uppercase tracking-wide">Unterordner einschließen</label>
            <label className="flex items-center gap-2 text-xs text-white/60 cursor-pointer">
              <input type="checkbox" checked={!!data.recursive} onChange={e => onChange(node.id, { ...data, recursive: e.target.checked })} className="rounded" />
              Rekursiv
            </label>
          </div>
        </>
      )}

      {node.type === "type_filter" && (
        field("extensions", "Dateiendungen", "jpg,png,mp4", "Kommagetrennt, ohne Punkt")
      )}

      {(node.type === "move" || node.type === "copy") && (
        field("destination", "Zielordner", "/projects/sortiert/{year}/{month}", "Platzhalter: {year} {month} {day} {date}")
      )}

      {node.type === "rename" && (
        field("pattern", "Namensmuster", "{date}_{name}", "Platzhalter: {name} {ext} {date} {year} {month} {day} {time}")
      )}

      {node.type === "agent_task" && (
        <>
          {field("agent_id", "Agent-ID", "my-agent-id")}
          <div className="space-y-1">
            <label className="text-xs text-white/50 uppercase tracking-wide">Prompt</label>
            <textarea
              value={data.prompt ?? ""}
              onChange={e => onChange(node.id, { ...data, prompt: e.target.value })}
              placeholder="Verarbeite diese Datei: {file}"
              rows={3}
              className="w-full px-2.5 py-1.5 text-xs bg-zinc-800 border border-zinc-700 rounded-lg text-white focus:outline-none focus:ring-1 focus:ring-indigo-500 resize-none font-mono"
            />
            <p className="text-[0.6rem] text-white/30">Platzhalter: {"{file}"} {"{name}"} {"{ext}"}</p>
          </div>
        </>
      )}

      {node.type === "notify" && (
        field("message", "Nachricht", "Neue Datei: {file}", "Platzhalter: {file} {name} {ext} {date}")
      )}
    </div>
  );
}

// ── Haupt-Editor ──────────────────────────────────────────────────────────────

interface Pipeline { id: string; name: string; enabled: boolean; nodes: any[]; edges: any[] }

function PipelineEditor({ pipeline, onSaved, onClose }: {
  pipeline: Pipeline | null;
  onSaved: () => void;
  onClose: () => void;
}) {
  const [nodes, setNodes, onNodesChange] = useNodesState(pipeline?.nodes ?? []);
  const [edges, setEdges, onEdgesChange] = useEdgesState(pipeline?.edges ?? []);
  const [name, setName] = useState(pipeline?.name ?? "Neue Pipeline");
  const [saving, setSaving] = useState(false);
  const [selectedNode, setSelectedNode] = useState<Node | null>(null);
  const { screenToFlowPosition } = useReactFlow();

  const onConnect = useCallback(
    (connection: Connection) => setEdges(eds => addEdge({ ...connection, animated: true, style: { stroke: "#818cf8" } }, eds)),
    [setEdges]
  );

  function addNode(nodeType: PaletteType) {
    const pal = NODE_PALETTE.find(p => p.type === nodeType)!;
    const id = `${nodeType}_${Date.now()}`;
    const newNode: Node = {
      id,
      type: nodeType,
      position: { x: 100 + nodes.length * 220, y: 150 },
      data: { label: pal.label, subtitle: "" },
    };
    setNodes(nds => [...nds, newNode]);
  }

  function updateNodeData(nodeId: string, data: any) {
    setNodes(nds => nds.map(n => n.id === nodeId
      ? { ...n, data: { ...data, subtitle: data.path || data.destination || data.extensions || data.pattern || data.agent_id || "" } }
      : n
    ));
    setSelectedNode(prev => prev?.id === nodeId ? { ...prev, data } : prev);
  }

  async function save() {
    setSaving(true);
    try {
      const body = JSON.stringify({ name, enabled: pipeline?.enabled ?? true, nodes, edges });
      const url = pipeline ? `${API}/pipelines/${pipeline.id}` : `${API}/pipelines`;
      const method = pipeline ? "PUT" : "POST";
      const res = await fetch(url, { method, headers: authHeaders(), body });
      if (!res.ok) throw new Error(await res.text());
      onSaved();
    } catch (e) {
      alert("Fehler beim Speichern: " + String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex flex-col h-full">
      {/* Toolbar */}
      <div className="flex items-center gap-2 px-4 py-2 border-b border-white/10 bg-zinc-900 shrink-0">
        <button onClick={onClose} className="text-white/40 hover:text-white/80 mr-1"><X className="h-4 w-4" /></button>
        <input
          value={name}
          onChange={e => setName(e.target.value)}
          className="flex-1 px-2 py-1 text-sm bg-transparent border-b border-white/20 text-white focus:outline-none focus:border-indigo-500"
        />
        <button onClick={save} disabled={saving}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg transition-colors disabled:opacity-50">
          {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
          Speichern
        </button>
      </div>

      <div className="flex flex-1 overflow-hidden relative">
        {/* Node-Palette */}
        <div className="w-44 shrink-0 bg-zinc-900/80 border-r border-white/10 overflow-y-auto p-2 space-y-1">
          <p className="text-[0.6rem] font-bold uppercase tracking-widest text-white/30 px-1 pt-1 pb-0.5">Nodes</p>
          {NODE_PALETTE.map(p => {
            const colors = COLOR_MAP[p.color];
            return (
              <button key={p.type} onClick={() => addNode(p.type)}
                className="w-full flex items-center gap-2 px-2 py-1.5 text-left rounded-lg hover:bg-white/5 transition-colors group">
                <p.icon className={cn("h-3.5 w-3.5 shrink-0", colors.badge)} />
                <div>
                  <p className="text-xs text-white/70 group-hover:text-white leading-tight">{p.label}</p>
                  <p className="text-[0.55rem] text-white/30 leading-tight">{p.desc}</p>
                </div>
              </button>
            );
          })}
        </div>

        {/* Canvas */}
        <div className="flex-1 relative">
          <ReactFlow
            nodes={nodes} edges={edges}
            onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            nodeTypes={NODE_TYPES}
            onNodeClick={(_, node) => setSelectedNode(prev => prev?.id === node.id ? null : node)}
            fitView
            colorMode="dark"
          >
            <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="#333" />
            <Controls />
          </ReactFlow>

          {selectedNode && (
            <NodeConfigPanel
              node={selectedNode}
              onChange={updateNodeData}
              onClose={() => setSelectedNode(null)}
            />
          )}
        </div>
      </div>
    </div>
  );
}

// ── Haupt-Tab ─────────────────────────────────────────────────────────────────

export function FilePipelineTab() {
  const { t } = useTranslation();
  const [pipelines, setPipelines] = useState<Pipeline[]>([]);
  const [loading, setLoading]     = useState(true);
  const [editing, setEditing]     = useState<Pipeline | "new" | null>(null);
  const [runResult, setRunResult] = useState<{pipeline_id: string; steps: any[]} | null>(null);
  const [runPath, setRunPath]     = useState("");
  const [runLoading, setRunLoading] = useState(false);
  const [confirmState, setConfirmState] = useState<{action: () => void; title: string; message: string} | null>(null);

  async function load() {
    try {
      const res = await fetch(`${API}/pipelines`, { headers: authHeaders() });
      const data = await res.json();
      setPipelines(data);
    } catch { /* ignore */ }
    finally { setLoading(false); }
  }

  useEffect(() => { load(); }, []);

  async function toggle(p: Pipeline) {
    await fetch(`${API}/pipelines/${p.id}/toggle`, { method: "PATCH", headers: authHeaders() });
    load();
  }

  function deletePipeline(p: Pipeline) {
    setConfirmState({
      title: t("confirm.titleDelete"),
      message: t("confirm.deletePipeline", { name: p.name }),
      action: async () => {
        await fetch(`${API}/pipelines/${p.id}`, { method: "DELETE", headers: authHeaders() });
        load();
      },
    });
  }

  async function runTest(p: Pipeline) {
    if (!runPath.trim()) return;
    setRunLoading(true); setRunResult(null);
    try {
      const res = await fetch(`${API}/pipelines/${p.id}/run`, {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({ file_path: runPath }),
      });
      const data = await res.json();
      setRunResult({ pipeline_id: p.id, steps: data.steps ?? [] });
    } catch { /* ignore */ }
    finally { setRunLoading(false); }
  }

  if (editing !== null) {
    return (
      <ReactFlowProvider>
        <PipelineEditor
          pipeline={editing === "new" ? null : editing}
          onSaved={() => { setEditing(null); load(); }}
          onClose={() => setEditing(null)}
        />
      </ReactFlowProvider>
    );
  }

  return (
    <div className="p-4 space-y-4 overflow-y-auto h-full text-white">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold">Datei-Pipelines</h2>
          <p className="text-xs text-white/40">Ordner beobachten, Dateien sortieren und verarbeiten</p>
        </div>
        <button onClick={() => setEditing("new")}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg transition-colors">
          <Plus className="h-3.5 w-3.5" /> Neue Pipeline
        </button>
      </div>

      {loading ? (
        <div className="flex justify-center py-12"><Loader2 className="h-6 w-6 animate-spin text-white/30" /></div>
      ) : pipelines.length === 0 ? (
        <div className="text-center py-12 text-white/30 text-sm">
          Noch keine Pipelines — klicke „Neue Pipeline" um zu starten
        </div>
      ) : (
        <div className="space-y-2">
          {pipelines.map(p => (
            <div key={p.id} className="bg-zinc-900 border border-white/10 rounded-xl p-4 space-y-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <div className={cn("h-2 w-2 rounded-full", p.enabled ? "bg-emerald-400" : "bg-zinc-600")} />
                  <span className="text-sm font-medium">{p.name}</span>
                  <span className="text-[0.6rem] text-white/30 font-mono">{p.nodes.length} Nodes</span>
                </div>
                <div className="flex items-center gap-1.5">
                  <button onClick={() => toggle(p)} title={p.enabled ? "Deaktivieren" : "Aktivieren"}
                    className="p-1.5 rounded-lg hover:bg-white/10 transition-colors">
                    <Power className={cn("h-3.5 w-3.5", p.enabled ? "text-emerald-400" : "text-white/30")} />
                  </button>
                  <button onClick={() => setEditing(p)}
                    className="px-2.5 py-1 text-xs border border-white/20 rounded-lg hover:bg-white/5 transition-colors">
                    Bearbeiten
                  </button>
                  <button onClick={() => deletePipeline(p)}
                    className="p-1.5 rounded-lg hover:bg-red-500/20 text-red-400/60 hover:text-red-400 transition-colors">
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>

              {/* Manual test */}
              <div className="flex gap-2 pt-1 border-t border-white/10">
                <input
                  value={runPath}
                  onChange={e => setRunPath(e.target.value)}
                  placeholder="/pfad/zur/testdatei.jpg"
                  className="flex-1 px-2.5 py-1 text-xs bg-zinc-800 border border-zinc-700 rounded-lg text-white focus:outline-none focus:ring-1 focus:ring-indigo-500 font-mono"
                />
                <button onClick={() => runTest(p)} disabled={runLoading || !runPath.trim()}
                  className="flex items-center gap-1 px-2.5 py-1 text-xs bg-zinc-700 hover:bg-zinc-600 text-white rounded-lg transition-colors disabled:opacity-40">
                  {runLoading ? <Loader2 className="h-3 w-3 animate-spin" /> : <Play className="h-3 w-3" />}
                  Test
                </button>
              </div>

              {/* Run result */}
              {runResult?.pipeline_id === p.id && (
                <div className="space-y-1 text-xs">
                  {runResult.steps.map((s, i) => (
                    <div key={i} className={cn(
                      "flex items-start gap-2 px-2 py-1 rounded-lg",
                      s.status === "ok" || s.status === "passed" || s.status === "trigger" ? "bg-emerald-500/10 text-emerald-300" :
                      s.status === "filtered_out" ? "bg-amber-500/10 text-amber-300" :
                      s.status === "error" ? "bg-red-500/10 text-red-300" : "bg-zinc-800 text-white/50"
                    )}>
                      {s.status === "ok" || s.status === "passed" ? <CheckCircle2 className="h-3 w-3 mt-0.5 shrink-0" /> :
                       s.status === "error" ? <AlertCircle className="h-3 w-3 mt-0.5 shrink-0" /> :
                       <span className="h-3 w-3 mt-0.5 shrink-0 text-center">·</span>}
                      <span><strong>{s.label}</strong>: {s.status}{s.reason ? ` (${s.reason})` : ""}{s.to ? ` → ${s.to}` : ""}{s.error ? `: ${s.error}` : ""}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    <ConfirmDialog
      open={!!confirmState}
      title={confirmState?.title || ""}
      message={confirmState?.message || ""}
      onConfirm={() => { confirmState?.action(); setConfirmState(null); }}
      onCancel={() => setConfirmState(null)}
      variant="danger"
    />
    </div>
  );
}
