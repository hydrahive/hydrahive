import { useEffect, useRef, useState, useCallback, lazy, Suspense } from "react";
import type ForceGraph3DType from "react-force-graph-3d";
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const ForceGraph3D = lazy(() => import("react-force-graph-3d")) as any as typeof ForceGraph3DType;
import { Loader2, RefreshCw, Eye, EyeOff } from "lucide-react";
import { api } from "@/lib/api";

interface GraphNode {
  id: string;
  label: string;
  type: string;
  group: string;
  running?: boolean;
  model?: string;
  tools_count?: number;
  mem_count?: number;
  skill_count?: number;
  // react-force-graph adds these at runtime:
  x?: number; y?: number; z?: number;
  __threeObj?: unknown;
}

interface GraphLink {
  source: string | GraphNode;
  target: string | GraphNode;
  type: string;
}

interface GraphData {
  nodes: GraphNode[];
  links: GraphLink[];
}

// ── Farben pro Gruppe ──────────────────────────────────────────────────────
const GROUP_COLOR: Record<string, string> = {
  agent_boss:    "#22d3ee",   // cyan
  agent_worker:  "#60a5fa",   // blau
  agent_personal:"#a78bfa",   // lila
  tool:          "#fb923c",   // orange
  memory:        "#4ade80",   // grün
  project:       "#facc15",   // gelb
  skill:         "#f472b6",   // pink
  llm:           "#e2e8f0",   // weiß
};

// nodeVal = relative Größe (wird intern als Kugelgröße genutzt, klein halten!)
const GROUP_SIZE: Record<string, number> = {
  agent_boss:    3,
  agent_worker:  2,
  agent_personal:2,
  project:       2.5,
  tool:          0.8,
  memory:        0.6,
  skill:         0.6,
  llm:           1.5,
};

const LINK_COLOR: Record<string, string> = {
  has_boss:    "#facc1588",
  has_worker:  "#facc1544",
  has_tool:    "#fb923c55",
  has_memory:  "#4ade8044",
  has_skill:   "#f472b644",
  uses_llm:    "#e2e8f033",
};

const LINK_PARTICLES: Record<string, number> = {
  has_tool:    2,
  has_memory:  1,
  has_skill:   1,
  uses_llm:    3,
  has_boss:    0,
  has_worker:  0,
};

// ── Legende ────────────────────────────────────────────────────────────────
const LEGEND = [
  { group: "agent_boss",    label: "Boss-Agent" },
  { group: "agent_worker",  label: "Worker-Agent" },
  { group: "agent_personal",label: "Personal-Agent" },
  { group: "project",       label: "Projekt" },
  { group: "tool",          label: "Tool" },
  { group: "memory",        label: "Memory" },
  { group: "skill",         label: "Skill" },
  { group: "llm",           label: "LLM-Provider" },
];

export function HydraBrainPage() {
  const [graphData, setGraphData] = useState<GraphData>({ nodes: [], links: [] });
  const [loading, setLoading]     = useState(true);
  const [selected, setSelected]   = useState<GraphNode | null>(null);
  const [showLabels, setShowLabels] = useState(true);
  const [activeNodes, setActiveNodes] = useState<Set<string>>(new Set());
  const fgRef = useRef<any>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [dims, setDims] = useState({ w: 800, h: 600 });

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.get("/brain-graph") as GraphData;
      setGraphData(data);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  // Dimensionen tracken
  useEffect(() => {
    const update = () => {
      if (containerRef.current) {
        setDims({
          w: containerRef.current.clientWidth,
          h: containerRef.current.clientHeight,
        });
      }
    };
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []);

  // SSE Activity-Stream — laufende Agenten blinken lassen
  useEffect(() => {
    const token = localStorage.getItem("hydrahive_token") || "";
    const es = new EventSource(`/api/admin/agents/live?token=${encodeURIComponent(token)}`);
    es.onmessage = (e) => {
      try {
        const ev = JSON.parse(e.data);
        const agentId = ev.agent_id || ev.agentId || ev.id;
        if (!agentId) return;
        setActiveNodes(prev => new Set([...prev, agentId]));
        setTimeout(() => {
          setActiveNodes(prev => {
            const next = new Set(prev);
            next.delete(agentId);
            return next;
          });
        }, 2500);
      } catch { /* ignore */ }
    };
    es.onerror = () => es.close();
    return () => es.close();
  }, []);

  const nodeColor = useCallback((node: GraphNode) => {
    if (activeNodes.has(node.id)) return "#ffffff";
    return GROUP_COLOR[node.group] ?? "#888";
  }, [activeNodes]);

  const nodeVal = useCallback((node: GraphNode) => {
    const base = GROUP_SIZE[node.group] ?? 3;
    return activeNodes.has(node.id) ? base * 2 : base;
  }, [activeNodes]);

  const linkColor = useCallback((link: GraphLink) => {
    return LINK_COLOR[link.type] ?? "#ffffff22";
  }, []);

  const linkParticles = useCallback((link: GraphLink) => {
    return LINK_PARTICLES[link.type] ?? 0;
  }, []);

  // d3-Kräfte konfigurieren sobald graphData geladen ist und ref gesetzt
  useEffect(() => {
    if (graphData.nodes.length === 0) return;
    // Kurz warten bis ForceGraph3D gemountet und ref gesetzt ist
    const t = setTimeout(() => {
      const fg = fgRef.current;
      if (!fg) return;
      fg.d3Force("charge")?.strength(-80).distanceMax(400);
      fg.d3Force("link")?.distance(120);
    }, 100);
    return () => clearTimeout(t);
  }, [graphData.nodes.length]);

  const handleNodeClick = useCallback((node: GraphNode) => {
    setSelected(prev => prev?.id === node.id ? null : node);
    // Kamera auf Knoten zoomen
    if (fgRef.current) {
      const distance = 80;
      const distRatio = 1 + distance / Math.hypot(node.x ?? 0, node.y ?? 0, node.z ?? 0);
      fgRef.current.cameraPosition(
        { x: (node.x ?? 0) * distRatio, y: (node.y ?? 0) * distRatio, z: (node.z ?? 0) * distRatio },
        node,
        1000
      );
    }
  }, []);

  return (
    <div className="relative w-full h-screen bg-[#050810] overflow-hidden" ref={containerRef}>

      {/* Toolbar */}
      <div className="absolute top-4 left-4 z-20 flex items-center gap-2">
        <div className="flex items-center gap-1.5 rounded-xl bg-black/60 border border-white/10 px-3 py-2 backdrop-blur">
          <div className="w-2 h-2 rounded-full bg-cyan-400 animate-pulse" />
          <span className="text-xs font-semibold text-white/70">HydraBrain</span>
          <span className="text-xs text-white/30 ml-1">{graphData.nodes.length} Knoten · {graphData.links.length} Verbindungen</span>
        </div>
        <button
          onClick={load}
          disabled={loading}
          className="rounded-xl bg-black/60 border border-white/10 p-2 hover:bg-white/10 transition-colors backdrop-blur"
          title="Neu laden"
        >
          {loading
            ? <Loader2 className="h-4 w-4 text-white/50 animate-spin" />
            : <RefreshCw className="h-4 w-4 text-white/50" />}
        </button>
        <button
          onClick={() => setShowLabels(v => !v)}
          className="rounded-xl bg-black/60 border border-white/10 p-2 hover:bg-white/10 transition-colors backdrop-blur"
          title="Labels togglen"
        >
          {showLabels
            ? <Eye className="h-4 w-4 text-white/50" />
            : <EyeOff className="h-4 w-4 text-white/50" />}
        </button>
      </div>

      {/* Legende */}
      <div className="absolute top-4 right-4 z-20 rounded-xl bg-black/60 border border-white/10 px-3 py-2.5 backdrop-blur flex flex-col gap-1.5">
        {LEGEND.map(l => (
          <div key={l.group} className="flex items-center gap-2">
            <div className="w-2.5 h-2.5 rounded-full shrink-0" style={{ background: GROUP_COLOR[l.group] }} />
            <span className="text-[11px] text-white/50">{l.label}</span>
          </div>
        ))}
      </div>

      {/* Detail-Panel */}
      {selected && (() => {
        // Verbundene Knoten berechnen
        const nodeMap = Object.fromEntries(graphData.nodes.map(n => [n.id, n]));
        const outgoing = graphData.links.filter(l => {
          const src = typeof l.source === "object" ? (l.source as GraphNode).id : l.source;
          return src === selected.id;
        });
        const incoming = graphData.links.filter(l => {
          const tgt = typeof l.target === "object" ? (l.target as GraphNode).id : l.target;
          return tgt === selected.id;
        });
        const color = GROUP_COLOR[selected.group] ?? "#888";

        const typeLabel: Record<string, string> = {
          agent_boss: "Boss-Agent", agent_worker: "Worker-Agent",
          agent_personal: "Personal-Agent", project: "Projekt",
          tool: "Tool", memory: "Memory", skill: "Skill", llm: "LLM-Provider",
        };

        return (
          <div className="absolute right-4 top-1/2 -translate-y-1/2 z-20 rounded-2xl bg-black/85 border border-white/15 backdrop-blur w-64 overflow-hidden shadow-2xl">
            {/* Header */}
            <div className="px-4 py-3 border-b border-white/10" style={{ borderLeftColor: color, borderLeftWidth: 3 }}>
              <div className="flex items-center gap-2">
                <div className="w-3 h-3 rounded-full shrink-0" style={{ background: color }} />
                <p className="text-sm font-bold text-white truncate flex-1">{selected.label}</p>
                <button onClick={() => setSelected(null)} className="text-white/30 hover:text-white text-xs shrink-0">✕</button>
              </div>
              <p className="text-[11px] text-white/40 mt-0.5">{typeLabel[selected.group] ?? selected.group}</p>
            </div>

            <div className="px-4 py-3 flex flex-col gap-3">
              {/* ID */}
              <div>
                <p className="text-[10px] text-white/30 uppercase tracking-widest mb-1">ID</p>
                <p className="text-xs font-mono text-white/60 break-all">{selected.id}</p>
              </div>

              {/* Status bei Agenten */}
              {selected.running !== undefined && (
                <div className="flex items-center gap-2">
                  <div className={`w-2 h-2 rounded-full ${selected.running ? "bg-green-400 animate-pulse" : "bg-white/20"}`} />
                  <span className={`text-xs ${selected.running ? "text-green-400" : "text-white/30"}`}>
                    {selected.running ? "Aktiv / läuft" : "Gestoppt"}
                  </span>
                </div>
              )}

              {/* Modell */}
              {selected.model && (
                <div>
                  <p className="text-[10px] text-white/30 uppercase tracking-widest mb-1">Modell</p>
                  <p className="text-xs text-cyan-300 font-mono">{selected.model}</p>
                </div>
              )}

              {/* Zähler bei Agenten */}
              {selected.type === "agent" && (
                <div className="grid grid-cols-3 gap-2">
                  {[
                    { label: "Tools",  val: selected.tools_count ?? 0, color: "#fb923c" },
                    { label: "Memory", val: selected.mem_count   ?? 0, color: "#4ade80" },
                    { label: "Skills", val: selected.skill_count ?? 0, color: "#f472b6" },
                  ].map(({ label, val, color: c }) => (
                    <div key={label} className="rounded-lg bg-white/5 px-2 py-1.5 text-center">
                      <p className="text-sm font-bold" style={{ color: c }}>{val}</p>
                      <p className="text-[10px] text-white/35">{label}</p>
                    </div>
                  ))}
                </div>
              )}

              {/* Verbindungen */}
              {(outgoing.length > 0 || incoming.length > 0) && (
                <div>
                  <p className="text-[10px] text-white/30 uppercase tracking-widest mb-1.5">Verbindungen</p>
                  <div className="flex flex-col gap-1 max-h-40 overflow-y-auto">
                    {outgoing.slice(0, 8).map((l, i) => {
                      const tid = typeof l.target === "object" ? (l.target as GraphNode).id : l.target;
                      const tNode = nodeMap[tid];
                      return (
                        <div key={i} className="flex items-center gap-1.5 text-[11px]">
                          <div className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: GROUP_COLOR[tNode?.group ?? ""] ?? "#666" }} />
                          <span className="text-white/50 truncate">{tNode?.label ?? tid}</span>
                          <span className="text-white/25 ml-auto shrink-0">{l.type.replace(/_/g," ")}</span>
                        </div>
                      );
                    })}
                    {incoming.slice(0, 4).map((l, i) => {
                      const sid = typeof l.source === "object" ? (l.source as GraphNode).id : l.source;
                      const sNode = nodeMap[sid];
                      return (
                        <div key={"in"+i} className="flex items-center gap-1.5 text-[11px]">
                          <div className="w-1.5 h-1.5 rounded-full shrink-0 opacity-50" style={{ background: GROUP_COLOR[sNode?.group ?? ""] ?? "#666" }} />
                          <span className="text-white/35 truncate">← {sNode?.label ?? sid}</span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          </div>
        );
      })()}

      {/* Graph */}
      {!loading && (
        <Suspense fallback={<div className="flex items-center justify-center w-full h-full"><Loader2 className="animate-spin text-muted-foreground" /></div>}>
        <ForceGraph3D
          ref={fgRef}
          width={dims.w}
          height={dims.h}
          graphData={graphData}
          nodeId="id"
          nodeLabel={(node: object) => {
            const n = node as GraphNode;
            const parts = [`<b>${n.label}</b>`];
            if (n.model) parts.push(`<span style="color:#67e8f9">${n.model}</span>`);
            if (n.running !== undefined) parts.push(n.running ? `<span style="color:#4ade80">● aktiv</span>` : `<span style="color:#555">○ gestoppt</span>`);
            if (n.tools_count) parts.push(`${n.tools_count} Tools`);
            if (n.mem_count)   parts.push(`${n.mem_count} Memory`);
            return parts.join(" · ");
          }}
          nodeColor={nodeColor}
          nodeVal={nodeVal}
          nodeOpacity={0.92}
          linkColor={linkColor}
          linkWidth={1}
          linkOpacity={0.5}
          linkDirectionalParticles={linkParticles}
          linkDirectionalParticleWidth={1.5}
          linkDirectionalParticleSpeed={0.004}
          backgroundColor="#050810"
          onNodeClick={handleNodeClick as any}
          enableNodeDrag={true}
          showNavInfo={false}
          // Physik: sanft, kein Feuerwerk
          d3AlphaDecay={0.03}
          d3VelocityDecay={0.5}
          warmupTicks={80}
          cooldownTicks={150}
          onEngineStop={() => { fgRef.current?.zoomToFit(600, 60); }}
        />
        </Suspense>
      )}

      {loading && (
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="flex flex-col items-center gap-3">
            <Loader2 className="h-8 w-8 text-cyan-400 animate-spin" />
            <p className="text-sm text-white/40">HydraBrain lädt…</p>
          </div>
        </div>
      )}
    </div>
  );
}
