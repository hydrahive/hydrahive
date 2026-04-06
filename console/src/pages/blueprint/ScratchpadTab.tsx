import "@xyflow/react/dist/style.css";
import { useCallback, useEffect, useState, useRef } from "react";
import {
  ReactFlow, ReactFlowProvider, Background, Controls, MiniMap,
  addEdge, useNodesState, useEdgesState, useReactFlow,
  Handle, Position, BackgroundVariant, Panel,
  type Connection, type Edge, type Node,
} from "@xyflow/react";
import { PlusCircle, Save, Loader2, Trash2, Palette, X, PenTool, FolderOpen, FilePlus, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useTranslation } from "react-i18next";
import { ConfirmDialog } from "@/components/ConfirmDialog";

const COLORS = [
  { id: "zinc",    bg: "bg-zinc-800/80",    border: "border-zinc-500/50", text: "text-white",       handle: "#a1a1aa" },
  { id: "blue",    bg: "bg-blue-950/70",    border: "border-blue-500/50", text: "text-blue-100",    handle: "#60a5fa" },
  { id: "green",   bg: "bg-green-950/70",   border: "border-green-500/50",text: "text-green-100",   handle: "#4ade80" },
  { id: "purple",  bg: "bg-purple-950/70",  border: "border-purple-500/50",text:"text-purple-100",  handle: "#c084fc" },
  { id: "orange",  bg: "bg-orange-950/70",  border: "border-orange-500/50",text:"text-orange-100",  handle: "#fb923c" },
  { id: "pink",    bg: "bg-pink-950/70",    border: "border-pink-500/50", text: "text-pink-100",    handle: "#f472b6" },
  { id: "cyan",    bg: "bg-cyan-950/70",    border: "border-cyan-500/50", text: "text-cyan-100",    handle: "#22d3ee" },
  { id: "amber",   bg: "bg-amber-950/70",   border: "border-amber-500/50",text: "text-amber-100",  handle: "#fbbf24" },
  { id: "red",     bg: "bg-red-950/70",     border: "border-red-500/50",  text: "text-red-100",     handle: "#f87171" },
];

function ScratchNode({ data, selected }: { data: any; selected: boolean; id: string }) {
  const color = COLORS.find(c => c.id === (data.color || "zinc")) || COLORS[0];
  const hStyle = { background: color.handle, border: "2px solid rgba(0,0,0,0.3)", width: 8, height: 8 };
  return (
    <div className={cn(
      "min-w-[120px] max-w-[300px] rounded-xl border-2 px-3 py-2 shadow-lg select-none",
      color.bg, color.border, color.text,
      selected && "ring-2 ring-white/30",
    )}>
      <p className="text-sm font-medium leading-snug whitespace-pre-wrap">{data.label || "..."}</p>
      {data.note && <p className="text-[0.6rem] opacity-50 mt-1 leading-tight">{data.note}</p>}
      <Handle type="target" position={Position.Top}    id="t" style={{ ...hStyle, left: "50%" }} />
      <Handle type="target" position={Position.Left}   id="l" style={hStyle} />
      <Handle type="source" position={Position.Bottom} id="b" style={{ ...hStyle, left: "50%" }} />
      <Handle type="source" position={Position.Right}  id="r" style={hStyle} />
    </div>
  );
}

const NODE_TYPES = { scratch: ScratchNode as any };
const SCRATCHPADS_DIR = "/etc/hydrahive/scratchpads";

function ScratchpadInner() {
  const { t } = useTranslation();
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [selectedNode, setSelectedNode]  = useState<Node | null>(null);
  const [saving, setSaving]   = useState(false);
  const [toast, setToast]     = useState<string | null>(null);
  const [colorPicker, setColorPicker] = useState(false);
  const [padName, setPadName]         = useState("default");
  const [padList, setPadList]         = useState<string[]>([]);
  const [showPadList, setShowPadList] = useState(false);
  const [newPadName, setNewPadName]   = useState("");
  const [confirmState, setConfirmState] = useState<{action: () => void; title: string; message: string} | null>(null);
  const rf = useReactFlow();
  const counter = useRef(0);

  // Pad-Liste laden
  async function loadPadList() {
    try {
      const r = await api.get<{items:{name:string;type:string}[]}>(`/admin/files?path=${SCRATCHPADS_DIR}`);
      setPadList(r.items.filter(i => i.name.endsWith(".json")).map(i => i.name.replace(".json", "")));
    } catch {
      setPadList([]);
    }
  }

  // Pad laden
  async function loadPad(name: string) {
    setPadName(name);
    setSelectedNode(null);
    try {
      const r = await api.get<{content:string}>(`/admin/files/read?path=${SCRATCHPADS_DIR}/${name}.json`);
      if (r.content) {
        const d = JSON.parse(r.content);
        setNodes(d.nodes || []); setEdges(d.edges || []); counter.current = d.counter || 0;
        setTimeout(() => rf.fitView({ padding: 0.2 }), 100);
        return;
      }
    } catch { /* nicht vorhanden */ }
    // Fallback localStorage für Migration
    try {
      const raw = localStorage.getItem("hydrahive_scratchpad");
      if (raw && name === "default") {
        const d = JSON.parse(raw);
        setNodes(d.nodes || []); setEdges(d.edges || []); counter.current = d.counter || 0;
        setTimeout(() => rf.fitView({ padding: 0.2 }), 100);
        return;
      }
    } catch {}
    setNodes([]); setEdges([]); counter.current = 0;
  }

  // Initial laden
  useEffect(() => { loadPadList(); loadPad("default"); }, []);

  // Speichern
  async function savePad() {
    setSaving(true);
    const data = JSON.stringify({ nodes, edges, counter: counter.current });
    try {
      await api.put("/admin/files/write", { path: `${SCRATCHPADS_DIR}/${padName}.json`, content: data });
      // Auch localStorage für Offline-Fallback
      try { localStorage.setItem("hydrahive_scratchpad", data); } catch {}
      setToast(t("common.saved"));
      setTimeout(() => setToast(null), 2000);
      loadPadList();
    } catch (e: any) { setToast(t("common.error") + ": " + e.message); }
    finally { setSaving(false); }
  }

  // Auto-Save alle 10s
  useEffect(() => {
    const t = setInterval(() => {
      const data = JSON.stringify({ nodes, edges, counter: counter.current });
      api.put("/admin/files/write", { path: `${SCRATCHPADS_DIR}/${padName}.json`, content: data }).catch(e => console.error("Failed to auto-save scratchpad", e));
      try { localStorage.setItem("hydrahive_scratchpad", data); } catch {}
    }, 10000);
    return () => clearInterval(t);
  }, [nodes, edges, padName]);

  const onConnect = useCallback((c: Connection) => {
    setEdges(es => addEdge({ ...c, animated: false, style: { stroke: "#6366f1", strokeWidth: 2 }, type: "smoothstep" } as Edge, es));
  }, [setEdges]);

  function addScratchNode(color: string = "zinc") {
    counter.current++;
    const id = `scratch-${counter.current}`;
    const viewport = rf.getViewport();
    setNodes(ns => [...ns, {
      id, type: "scratch",
      position: { x: -viewport.x / viewport.zoom + 200 + Math.random() * 100, y: -viewport.y / viewport.zoom + 150 + Math.random() * 100 },
      data: { label: `Notiz ${counter.current}`, color, note: "" },
    }]);
  }

  function updateNode(nodeId: string, patch: any) {
    setNodes(ns => ns.map(n => n.id === nodeId ? { ...n, data: { ...n.data, ...patch } } : n));
    setSelectedNode(prev => prev?.id === nodeId ? { ...prev, data: { ...prev.data, ...patch } } : prev);
  }

  function deleteNode(nodeId: string) {
    setNodes(ns => ns.filter(n => n.id !== nodeId));
    setEdges(es => es.filter(e => e.source !== nodeId && e.target !== nodeId));
    setSelectedNode(null);
  }

  function clearAll() {
    setConfirmState({
      title: t("confirm.titleClear"),
      message: t("confirm.clearAll"),
      action: () => {
        setNodes([]); setEdges([]); setSelectedNode(null); counter.current = 0;
      },
    });
  }

  function deletePad(name: string) {
    setConfirmState({
      title: t("confirm.titleDelete"),
      message: t("confirm.deleteScratchpad", { name }),
      action: async () => {
        try {
          await api.delete(`/admin/files/delete?path=${SCRATCHPADS_DIR}/${name}.json`);
          if (padName === name) { setPadName("default"); loadPad("default"); }
          loadPadList();
          setToast(`"${name}" gelöscht`);
          setTimeout(() => setToast(null), 2000);
        } catch (e: any) { setToast(t("common.error") + ": " + e.message); }
      },
    });
  }

  function createNewPad() {
    const name = newPadName.trim().toLowerCase().replace(/[^a-z0-9_-]/g, "-");
    if (!name) return;
    setNodes([]); setEdges([]); counter.current = 0;
    setPadName(name); setNewPadName(""); setShowPadList(false);
    setToast(`Neues Scratchpad: ${name}`);
    setTimeout(() => setToast(null), 2000);
  }

  return (
    <div className="flex h-full flex-col">
      {/* Toolbar */}
      <div className="flex items-center gap-2 px-4 py-2 border-b border-white/10 shrink-0 flex-wrap">
        {/* Pad-Auswahl */}
        <div className="relative">
          <button onClick={() => { setShowPadList(p => !p); loadPadList(); }}
            className="flex items-center gap-1.5 rounded-lg bg-zinc-900 border border-white/10 px-3 py-1.5 text-xs text-white hover:bg-zinc-800 transition-colors">
            <FolderOpen className="h-3.5 w-3.5" /> {padName}
          </button>
          {showPadList && (
            <div className="absolute top-full left-0 mt-1 w-56 bg-zinc-900 border border-white/10 rounded-lg shadow-xl z-50 p-2 space-y-1">
              {padList.map(p => (
                <div key={p} className="flex items-center justify-between">
                  <button onClick={() => { loadPad(p); setShowPadList(false); }}
                    className={cn("flex-1 text-left px-2 py-1.5 rounded text-xs transition-colors",
                      p === padName ? "bg-indigo-600 text-white" : "text-white/60 hover:bg-white/5 hover:text-white")}>
                    {p}
                  </button>
                  {p !== "default" && (
                    <button onClick={() => deletePad(p)} className="p-1 text-red-400 hover:bg-red-500/15 rounded"><Trash2 className="h-3 w-3" /></button>
                  )}
                </div>
              ))}
              <div className="border-t border-white/10 pt-1 mt-1 flex gap-1">
                <input value={newPadName} onChange={e => setNewPadName(e.target.value)}
                  placeholder="Neuer Name..."
                  onKeyDown={e => e.key === "Enter" && createNewPad()}
                  className="flex-1 px-2 py-1 rounded bg-zinc-800 border border-white/10 text-xs text-white focus:outline-none" />
                <button onClick={createNewPad} disabled={!newPadName.trim()}
                  className="p-1.5 rounded bg-indigo-600 text-white disabled:opacity-30"><FilePlus className="h-3 w-3" /></button>
              </div>
            </div>
          )}
        </div>

        <div className="h-4 w-px bg-white/10" />

        <button onClick={() => addScratchNode("zinc")}
          className="flex items-center gap-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-700 px-3 py-1.5 text-xs text-white transition-colors">
          <PlusCircle className="h-3.5 w-3.5" /> Notiz
        </button>
        <button onClick={() => setColorPicker(p => !p)}
          className="flex items-center gap-1 rounded-lg bg-zinc-900 border border-white/10 px-2.5 py-1.5 text-xs text-white hover:bg-zinc-800 transition-colors">
          <Palette className="h-3.5 w-3.5" /> Farbe
        </button>
        {colorPicker && COLORS.map(c => (
          <button key={c.id} onClick={() => { addScratchNode(c.id); setColorPicker(false); }}
            className={cn("w-6 h-6 rounded-lg border-2", c.bg, c.border, "hover:scale-110 transition-transform")} title={c.id} />
        ))}
        <div className="h-4 w-px bg-white/10" />
        <button onClick={clearAll}
          className="flex items-center gap-1.5 rounded-lg border border-red-500/30 px-2.5 py-1.5 text-xs text-red-400 hover:bg-red-500/10 transition-colors">
          <Trash2 className="h-3 w-3" /> Leeren
        </button>
        <div className="flex-1" />
        {toast && <span className="text-xs text-indigo-300">{toast}</span>}
        <span className="text-[0.6rem] text-white/20">{nodes.length} Notizen · Auto-Save</span>
        <button onClick={savePad} disabled={saving}
          className="flex items-center gap-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 border border-white/10 px-3 py-1.5 text-xs text-white transition-colors disabled:opacity-50">
          {saving ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />} Speichern
        </button>
      </div>

      {/* Canvas + Properties */}
      <div className="flex flex-1 overflow-hidden">
        <div className="flex-1 relative">
          <ReactFlow nodes={nodes} edges={edges} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
            onConnect={onConnect} nodeTypes={NODE_TYPES} colorMode="dark" fitView
            onNodeClick={(_, n) => setSelectedNode(n)} onPaneClick={() => setSelectedNode(null)}>
            <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="rgba(255,255,255,0.04)" />
            <Controls />
            <MiniMap nodeColor={n => { const c = COLORS.find(x => x.id === ((n.data as any)?.color || "zinc")); return c?.handle || "#a1a1aa"; }} />
            {nodes.length === 0 && (
              <Panel position="top-center" style={{ marginTop: 80 }}>
                <div className="text-center space-y-2 pointer-events-none">
                  <PenTool className="h-8 w-8 text-white/10 mx-auto" />
                  <p className="text-white/20 text-sm">Klicke "Notiz" um loszulegen</p>
                  <p className="text-white/10 text-xs">Kästchen frei platzieren, beschriften, verbinden</p>
                </div>
              </Panel>
            )}
          </ReactFlow>
        </div>
        <div className="w-56 shrink-0 border-l border-white/10 bg-zinc-900/50 flex flex-col">
          <div className="px-3 py-2 border-b border-white/10">
            <p className="text-[0.65rem] font-bold uppercase tracking-wider text-white/30">Eigenschaften</p>
          </div>
          <div className="flex-1 overflow-y-auto p-3 space-y-3">
            {!selectedNode ? (
              <p className="text-white/20 text-xs text-center mt-8">Notiz auswählen</p>
            ) : (
              <>
                <div className="flex items-center justify-between">
                  <span className="text-xs text-white/40">Notiz</span>
                  <button onClick={() => deleteNode(selectedNode.id)} className="p-1 rounded text-red-400 hover:bg-red-500/15"><Trash2 className="h-3 w-3" /></button>
                </div>
                <div>
                  <label className="block text-[0.65rem] text-white/40 mb-1">Text</label>
                  <textarea value={(selectedNode.data as any).label || ""} onChange={e => updateNode(selectedNode.id, { label: e.target.value })}
                    rows={3} className="w-full rounded-lg bg-zinc-800 border border-white/10 px-2.5 py-1.5 text-sm text-white focus:outline-none focus:border-indigo-500/60 resize-none" />
                </div>
                <div>
                  <label className="block text-[0.65rem] text-white/40 mb-1">Notiz (klein)</label>
                  <input value={(selectedNode.data as any).note || ""} onChange={e => updateNode(selectedNode.id, { note: e.target.value })}
                    className="w-full rounded-lg bg-zinc-800 border border-white/10 px-2.5 py-1.5 text-xs text-white focus:outline-none" />
                </div>
                <div>
                  <label className="block text-[0.65rem] text-white/40 mb-1">Farbe</label>
                  <div className="flex flex-wrap gap-1.5">
                    {COLORS.map(c => (
                      <button key={c.id} onClick={() => updateNode(selectedNode.id, { color: c.id })}
                        className={cn("w-6 h-6 rounded-lg border-2 transition-transform", c.bg, c.border,
                          (selectedNode.data as any).color === c.id && "ring-2 ring-white/40 scale-110")} />
                    ))}
                  </div>
                </div>
              </>
            )}
          </div>
        </div>
      </div>
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

export function ScratchpadTab() {
  return (<ReactFlowProvider><ScratchpadInner /></ReactFlowProvider>);
}
