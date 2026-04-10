/**
 * EkgMonitor — Live Agent-Monitoring im EKG-Style (#534)
 *
 * Canvas-basierte Echtzeit-Visualisierung der Agent-Aktivität.
 * Punkte leben in Refs (kein React-Rerender), Canvas zeichnet per rAF.
 */
import { useEffect, useRef, useState } from "react";
import { X, Maximize2, Minimize2 } from "lucide-react";
import { api } from "@/lib/api";

// ── Types ────────────────────────────────────────────────────────────────────

interface AgentData {
  id: string;
  name: string;
  tag: string;
  model: string;
  color: string;
  glowColor: string;
  tools: string[];
  active: boolean;
  currentActivity: string | null;
  avgResponseMs: number;
  errorRate: number;
  log: string[];
}

interface EkgMonitorProps {
  projectId: string;
  onClose: () => void;
}

// ── Constants ────────────────────────────────────────────────────────────────

const AGENT_COLORS = [
  { color: "#22c55e", glow: "#22c55e80" },
  { color: "#3b82f6", glow: "#3b82f680" },
  { color: "#f97316", glow: "#f9731680" },
  { color: "#a855f7", glow: "#a855f780" },
  { color: "#ec4899", glow: "#ec489980" },
  { color: "#06b6d4", glow: "#06b6d480" },
];

const POINT_COUNT = 300;
const POLL_INTERVAL = 2000;

// ── EKG Canvas — rein ref-basiert, kein React-State ─────────────────────────

function EkgCanvas({ agentsRef }: { agentsRef: React.RefObject<Map<string, { color: string; glow: string; active: boolean; points: number[] }>> }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const animRef = useRef<number>(0);
  const scanPos = useRef(0);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const dpr = window.devicePixelRatio || 1;
    let w = 0, h = 0;

    function resize() {
      if (!canvas) return;
      const rect = canvas.getBoundingClientRect();
      w = rect.width;
      h = rect.height;
      canvas.width = w * dpr;
      canvas.height = h * dpr;
      ctx!.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(canvas);

    function draw() {
      if (!ctx || w === 0) { animRef.current = requestAnimationFrame(draw); return; }
      const agents = agentsRef.current;
      if (!agents) { animRef.current = requestAnimationFrame(draw); return; }

      const count = agents.size;
      const rowH = count > 0 ? h / count : h;

      ctx.clearRect(0, 0, w, h);

      // Grid
      ctx.strokeStyle = "rgba(255,255,255,0.03)";
      ctx.lineWidth = 0.5;
      let gridY = 0;
      for (let i = 0; i < count; i++) {
        ctx.beginPath(); ctx.moveTo(0, gridY); ctx.lineTo(w, gridY); ctx.stroke();
        ctx.beginPath(); ctx.setLineDash([2, 6]); ctx.moveTo(0, gridY + rowH / 2); ctx.lineTo(w, gridY + rowH / 2); ctx.stroke(); ctx.setLineDash([]);
        gridY += rowH;
      }

      // Scanline
      scanPos.current = (scanPos.current + 0.5) % w;
      const sx = scanPos.current;
      const grad = ctx.createLinearGradient(sx - 30, 0, sx, 0);
      grad.addColorStop(0, "transparent");
      grad.addColorStop(1, "rgba(255,255,255,0.05)");
      ctx.fillStyle = grad;
      ctx.fillRect(sx - 30, 0, 30, h);

      // Lines
      let idx = 0;
      agents.forEach((agent) => {
        const baseY = idx * rowH + rowH / 2;
        const amplitude = rowH * 0.35;
        const step = w / (POINT_COUNT - 1);

        ctx.strokeStyle = agent.color;
        ctx.lineWidth = agent.active ? 2 : 1;
        ctx.lineJoin = "round";
        ctx.lineCap = "round";
        ctx.shadowBlur = agent.active ? 10 : 0;
        ctx.shadowColor = agent.active ? agent.glow : "transparent";

        ctx.beginPath();
        for (let i = 0; i < agent.points.length; i++) {
          const x = i * step;
          const y = baseY - agent.points[i] * amplitude;

          // Fade
          const dist = Math.abs(x - sx);
          ctx.globalAlpha = dist > w * 0.6 ? Math.max(0.08, 1 - (dist - w * 0.6) / (w * 0.4)) : 1;

          if (i === 0) {
            ctx.moveTo(x, y);
          } else {
            const px = (i - 1) * step;
            const py = baseY - agent.points[i - 1] * amplitude;
            ctx.bezierCurveTo((px + x) / 2, py, (px + x) / 2, y, x, y);
          }
        }
        ctx.globalAlpha = 1;
        ctx.stroke();
        ctx.shadowBlur = 0;
        idx++;
      });

      animRef.current = requestAnimationFrame(draw);
    }

    animRef.current = requestAnimationFrame(draw);
    return () => { cancelAnimationFrame(animRef.current); ro.disconnect(); };
  }, [agentsRef]);

  return <canvas ref={canvasRef} className="w-full h-full" />;
}

// ── Log Box ──────────────────────────────────────────────────────────────────

function LogBox({ agent, expanded, onToggle }: { agent: AgentData; expanded: boolean; onToggle: () => void }) {
  const logRef = useRef<HTMLDivElement>(null);
  useEffect(() => { if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight; }, [agent.log.length]);

  return (
    <div className={`flex flex-col rounded-xl border border-white/10 bg-black/60 backdrop-blur overflow-hidden transition-all duration-300 ${expanded ? "fixed inset-8 z-50" : "relative"}`}>
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-white/10 flex-shrink-0">
        <div className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-full" style={{ backgroundColor: agent.color, boxShadow: agent.active ? `0 0 6px ${agent.glowColor}` : "none" }} />
          <span className="text-[11px] font-medium text-white/80">{agent.name}</span>
          {agent.currentActivity && <span className="text-[9px] text-emerald-400/80 font-mono truncate max-w-40">{agent.currentActivity}</span>}
        </div>
        <button onClick={onToggle} className="p-0.5 rounded hover:bg-white/10 text-white/40 hover:text-white/70 transition-colors">
          {expanded ? <Minimize2 className="h-3 w-3" /> : <Maximize2 className="h-3 w-3" />}
        </button>
      </div>
      <div ref={logRef} className={`overflow-y-auto font-mono text-[11px] leading-relaxed px-3 py-2 ${expanded ? "flex-1" : "h-32"}`}>
        {agent.log.length === 0 ? (
          <span className="text-white/20">{agent.active ? "Warte auf Events…" : "(idle)"}</span>
        ) : agent.log.map((line, i) => (
          <div key={i} className={line.startsWith("▸ TOOL") ? "text-yellow-400" : line.startsWith("▸ ERROR") ? "text-red-400" : line.startsWith("▸ DONE") ? "text-emerald-400" : line.startsWith("▸") ? "text-blue-400" : "text-white/50"}>
            {line}
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Main ─────────────────────────────────────────────────────────────────────

export function EkgMonitor({ projectId, onClose }: EkgMonitorProps) {
  const [agentList, setAgentList] = useState<AgentData[]>([]);
  const [expandedLog, setExpandedLog] = useState<string | null>(null);
  const [metrics, setMetrics] = useState<any>(null);

  // Points live in a ref — mutated directly, no React rerender
  const pointsRef = useRef<Map<string, { color: string; glow: string; active: boolean; points: number[] }>>(new Map());
  const prevActivityRef = useRef<Record<string, string | null>>({});

  // Polling
  useEffect(() => {
    let alive = true;

    async function poll() {
      try {
        const data = await api.get<any>(`/projects/${projectId}/monitor`);
        if (!alive) return;

        const agentsData = data.agents || {};
        setMetrics(data.metrics || {});

        const newList: AgentData[] = [];
        const agentIds = Object.keys(agentsData);

        agentIds.forEach((agentId, idx) => {
          const ad = agentsData[agentId];
          const colorIdx = idx % AGENT_COLORS.length;
          const isActive = ad.status === "running";
          const hasActivity = ad.current_activity != null;

          // Update points ref
          if (!pointsRef.current.has(agentId)) {
            pointsRef.current.set(agentId, {
              color: AGENT_COLORS[colorIdx].color,
              glow: AGENT_COLORS[colorIdx].glow,
              active: isActive,
              points: new Array(POINT_COUNT).fill(0),
            });
          }
          const entry = pointsRef.current.get(agentId)!;
          entry.active = isActive;
          entry.color = AGENT_COLORS[colorIdx].color;
          entry.glow = AGENT_COLORS[colorIdx].glow;

          // Spike on activity change
          if (hasActivity && ad.current_activity !== prevActivityRef.current[agentId]) {
            const mag = 0.4 + Math.random() * 0.5;
            entry.points.push(Math.random() > 0.5 ? mag : -mag);
            if (entry.points.length > POINT_COUNT) entry.points.shift();
          }
          prevActivityRef.current[agentId] = ad.current_activity;

          // Build log from existing + new activity
          const prev = agentList.find(a => a.id === agentId);
          const log = prev?.log ? [...prev.log] : [];
          if (ad.current_activity && (!prev || prev.currentActivity !== ad.current_activity)) {
            const ts = new Date().toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
            const act = ad.current_activity;
            if (act.toLowerCase().includes("tool")) log.push(`▸ TOOL ${ts}  ${act}`);
            else if (act.toLowerCase().includes("error")) log.push(`▸ ERROR ${ts}  ${act}`);
            else log.push(`▸ ${ts}  ${act}`);
            if (log.length > 200) log.splice(0, log.length - 200);
          }

          // Tool chain
          const tools = prev?.tools ? [...prev.tools] : [];
          if (ad.current_activity?.startsWith("Tool:")) {
            const toolName = ad.current_activity.replace("Tool: ", "").split(" ")[0];
            if (tools[tools.length - 1] !== toolName) {
              tools.push(toolName);
              if (tools.length > 6) tools.shift();
            }
          }

          newList.push({
            id: agentId,
            name: ad.identity || agentId,
            tag: ad.role || ad.type || "worker",
            model: ad.model || "—",
            color: AGENT_COLORS[colorIdx].color,
            glowColor: AGENT_COLORS[colorIdx].glow,
            tools,
            active: isActive,
            currentActivity: ad.current_activity,
            avgResponseMs: ad.avg_response_ms || 0,
            errorRate: ad.error_rate || 0,
            log,
          });
        });

        setAgentList(newList);
      } catch { /* ignore */ }
    }

    poll();
    const iv = setInterval(poll, POLL_INTERVAL);
    return () => { alive = false; clearInterval(iv); };
  }, [projectId]);

  // Smooth point animation — mutates ref directly, no setState
  useEffect(() => {
    const tick = setInterval(() => {
      pointsRef.current.forEach((entry) => {
        const last = entry.points[entry.points.length - 1] || 0;
        if (entry.active) {
          entry.points.push(last * 0.92 + (Math.random() - 0.5) * 0.08);
        } else {
          entry.points.push(last * 0.95 + (Math.random() - 0.5) * 0.01);
        }
        if (entry.points.length > POINT_COUNT) entry.points.shift();
      });
    }, 80);
    return () => clearInterval(tick);
  }, []);

  const totalTokens = (metrics?.total_input_tokens || 0) + (metrics?.total_output_tokens || 0);
  const cacheHit = metrics?.cache_hit_rate != null ? (metrics.cache_hit_rate * 100).toFixed(1) : "—";
  const activeCount = agentList.filter(a => a.active).length;
  const errorCount = metrics?.overflow_count || 0;
  const toolCalls = metrics?.tool_calls_total || 0;
  const avgLatency = metrics?.avg_latency_ms != null ? Math.round(metrics.avg_latency_ms) : "—";

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-[#0a0a0f]/95 backdrop-blur-md">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-3 border-b border-white/10 flex-shrink-0">
        <div className="flex items-center gap-4">
          <h2 className="text-sm font-semibold text-white tracking-wide">EKG Monitor</h2>
          <div className="flex items-center gap-3 text-[11px]">
            <span className="text-emerald-400">{activeCount} aktiv</span>
            <span className="text-white/40">·</span>
            <span className="text-white/60">{agentList.length} Agenten</span>
            <span className="text-white/40">·</span>
            <span className="text-blue-400">{totalTokens.toLocaleString()} Tokens</span>
            <span className="text-white/40">·</span>
            <span className="text-purple-400">{toolCalls} Tool-Calls</span>
            <span className="text-white/40">·</span>
            <span className="text-cyan-400">Cache {cacheHit}%</span>
            <span className="text-white/40">·</span>
            <span className="text-yellow-400">Ø {avgLatency}ms</span>
            <span className="text-white/40">·</span>
            <span className={errorCount > 0 ? "text-red-400" : "text-white/30"}>{errorCount} Errors</span>
          </div>
        </div>
        <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-white/10 text-white/50 hover:text-white transition-colors">
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* Main area: Labels | EKG | Tools */}
      <div className="flex flex-1 min-h-0 overflow-hidden">
        {/* Left: Agent info cards */}
        <div className="flex flex-col w-56 flex-shrink-0 border-r border-white/10 py-2 overflow-y-auto">
          {agentList.map(agent => (
            <div key={agent.id} className="px-4 py-3 border-b border-white/5">
              <div className="flex items-center gap-2">
                <span className={`w-2 h-2 rounded-full flex-shrink-0 ${agent.active ? "animate-pulse" : ""}`}
                  style={{ backgroundColor: agent.color, boxShadow: agent.active ? `0 0 8px ${agent.glowColor}` : "none" }} />
                <span className="text-xs font-medium text-white/90 truncate">{agent.name}</span>
              </div>
              <div className="flex items-center gap-1.5 mt-1.5">
                <span className="text-[9px] rounded px-1 py-0.5 font-medium uppercase tracking-wider"
                  style={{ backgroundColor: agent.color + "20", color: agent.color }}>
                  {agent.tag}
                </span>
                <span className="text-[9px] text-white/30 truncate">{agent.model}</span>
              </div>
              {agent.avgResponseMs > 0 && (
                <div className="flex gap-3 mt-1.5 text-[9px]">
                  <span className="text-yellow-400/60">Ø {Math.round(agent.avgResponseMs)}ms</span>
                  {agent.errorRate > 0 && <span className="text-red-400/70">{agent.errorRate.toFixed(1)}% err</span>}
                </div>
              )}
              {agent.currentActivity && (
                <div className="mt-1.5 text-[9px] text-emerald-400/70 font-mono truncate">{agent.currentActivity}</div>
              )}
            </div>
          ))}
          {agentList.length === 0 && (
            <div className="flex items-center justify-center h-full text-white/20 text-xs">Lade…</div>
          )}
        </div>

        {/* Center: EKG Canvas (smaller) */}
        <div className="flex-1 min-w-0 relative">
          <EkgCanvas agentsRef={pointsRef} />
        </div>

        {/* Right: Tool chain + metrics */}
        <div className="flex flex-col w-56 flex-shrink-0 border-l border-white/10 py-2 overflow-y-auto">
          {agentList.map(agent => (
            <div key={agent.id} className="px-3 py-3 border-b border-white/5">
              <div className="text-[9px] text-white/30 mb-1.5">{agent.name}</div>
              <div className="flex flex-wrap gap-1">
                {agent.tools.length === 0 && <span className="text-[9px] text-white/15">Keine Tools</span>}
                {agent.tools.map((tool, i) => (
                  <span key={`${tool}-${i}`} className="inline-flex items-center rounded px-1.5 py-0.5 text-[9px] font-mono"
                    style={{ backgroundColor: agent.color + "15", color: agent.color + "cc", borderLeft: `2px solid ${agent.color}40` }}>
                    {i > 0 && <span className="text-white/20 mr-1">▸</span>}
                    {tool}
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Log Boxes */}
      <div className="grid gap-2 p-3 border-t border-white/10 flex-shrink-0"
        style={{ gridTemplateColumns: `repeat(${Math.min(agentList.length || 1, 4)}, 1fr)`, height: expandedLog ? "auto" : "11rem" }}>
        {agentList.slice(0, 4).map(agent => (
          <LogBox key={agent.id} agent={agent} expanded={expandedLog === agent.id}
            onToggle={() => setExpandedLog(expandedLog === agent.id ? null : agent.id)} />
        ))}
        {agentList.length === 0 && (
          <div className="flex items-center justify-center text-white/20 text-xs col-span-full">Keine Agenten</div>
        )}
      </div>
    </div>
  );
}
