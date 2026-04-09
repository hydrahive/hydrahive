/**
 * EkgMonitor — Live Agent-Monitoring im EKG-Style (#534)
 *
 * Canvas-basierte Echtzeit-Visualisierung der Agent-Aktivität.
 * Scanline-Animation, Bezier-Kurven, Glow-Effekt.
 *
 * Pollt /projects/{id}/monitor alle 2s für echte Daten.
 */
import { useEffect, useRef, useState } from "react";
import { X, Maximize2, Minimize2 } from "lucide-react";
import { api } from "@/lib/api";

// ── Types ────────────────────────────────────────────────────────────────────

interface AgentLine {
  id: string;
  name: string;
  tag: string;
  model: string;
  color: string;
  glowColor: string;
  points: number[];
  tools: string[];
  active: boolean;
  tokensSent: number;
  tokensReceived: number;
  log: string[];
  currentActivity: string | null;
  avgResponseMs: number;
  errorRate: number;
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

const POINT_COUNT = 200;
const SCAN_SPEED = 1.5;
const POLL_INTERVAL = 2000;

// ── EKG Canvas Component ─────────────────────────────────────────────────────

function EkgCanvas({ agents }: { agents: AgentLine[] }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const animRef = useRef<number>(0);
  const scanPos = useRef(0);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const dpr = window.devicePixelRatio || 1;

    function resize() {
      if (!canvas) return;
      const rect = canvas.getBoundingClientRect();
      canvas.width = rect.width * dpr;
      canvas.height = rect.height * dpr;
      ctx!.scale(dpr, dpr);
    }
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(canvas);

    function draw() {
      if (!canvas || !ctx) return;
      const w = canvas.getBoundingClientRect().width;
      const h = canvas.getBoundingClientRect().height;
      const rowH = h / Math.max(agents.length, 1);

      ctx.clearRect(0, 0, w, h);

      // Background grid
      ctx.strokeStyle = "rgba(255,255,255,0.03)";
      ctx.lineWidth = 0.5;
      for (let y = 0; y < h; y += rowH) {
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(w, y);
        ctx.stroke();
        const mid = y + rowH / 2;
        ctx.beginPath();
        ctx.setLineDash([2, 6]);
        ctx.moveTo(0, mid);
        ctx.lineTo(w, mid);
        ctx.stroke();
        ctx.setLineDash([]);
      }

      // Scanline
      scanPos.current = (scanPos.current + SCAN_SPEED) % w;
      const sx = scanPos.current;
      const grad = ctx.createLinearGradient(sx - 40, 0, sx, 0);
      grad.addColorStop(0, "transparent");
      grad.addColorStop(1, "rgba(255,255,255,0.06)");
      ctx.fillStyle = grad;
      ctx.fillRect(sx - 40, 0, 40, h);

      // Draw each agent line
      agents.forEach((agent, idx) => {
        const baseY = idx * rowH + rowH / 2;
        const amplitude = rowH * 0.35;

        ctx.strokeStyle = agent.color;
        ctx.lineWidth = agent.active ? 2 : 1;
        ctx.lineJoin = "round";
        ctx.lineCap = "round";

        if (agent.active) {
          ctx.shadowBlur = 12;
          ctx.shadowColor = agent.glowColor;
        } else {
          ctx.shadowBlur = 0;
        }

        ctx.beginPath();
        const step = w / (POINT_COUNT - 1);

        for (let i = 0; i < POINT_COUNT; i++) {
          const x = i * step;
          const y = baseY - agent.points[i] * amplitude;

          const dist = Math.abs(x - sx);
          const fadeDist = w * 0.7;
          if (dist > fadeDist) {
            ctx.globalAlpha = Math.max(0.1, 1 - (dist - fadeDist) / (w * 0.3));
          } else {
            ctx.globalAlpha = 1;
          }

          if (i === 0) {
            ctx.moveTo(x, y);
          } else {
            const prevX = (i - 1) * step;
            const prevY = baseY - agent.points[i - 1] * amplitude;
            const cpx = (prevX + x) / 2;
            ctx.bezierCurveTo(cpx, prevY, cpx, y, x, y);
          }
        }
        ctx.globalAlpha = 1;
        ctx.stroke();
        ctx.shadowBlur = 0;
      });

      animRef.current = requestAnimationFrame(draw);
    }

    animRef.current = requestAnimationFrame(draw);
    return () => {
      cancelAnimationFrame(animRef.current);
      ro.disconnect();
    };
  }, [agents]);

  return <canvas ref={canvasRef} className="w-full h-full" />;
}

// ── Log Box Component ────────────────────────────────────────────────────────

function LogBox({ agent, expanded, onToggle }: { agent: AgentLine; expanded: boolean; onToggle: () => void }) {
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [agent.log.length]);

  return (
    <div className={`flex flex-col rounded-xl border border-white/10 bg-black/60 backdrop-blur overflow-hidden transition-all duration-300 ${
      expanded ? "fixed inset-8 z-50" : "relative"
    }`}>
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-white/10 flex-shrink-0">
        <div className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-full" style={{ backgroundColor: agent.color, boxShadow: agent.active ? `0 0 6px ${agent.glowColor}` : "none" }} />
          <span className="text-[11px] font-medium text-white/80">{agent.name}</span>
          {agent.currentActivity && (
            <span className="text-[9px] text-emerald-400/80 font-mono truncate max-w-40">{agent.currentActivity}</span>
          )}
        </div>
        <button onClick={onToggle} className="p-0.5 rounded hover:bg-white/10 text-white/40 hover:text-white/70 transition-colors">
          {expanded ? <Minimize2 className="h-3 w-3" /> : <Maximize2 className="h-3 w-3" />}
        </button>
      </div>
      <div ref={logRef} className={`overflow-y-auto font-mono text-[11px] leading-relaxed px-3 py-2 ${expanded ? "flex-1" : "h-32"}`}>
        {agent.log.length === 0 ? (
          <span className="text-white/20">{agent.active ? "Warte auf Events…" : "(idle)"}</span>
        ) : (
          agent.log.map((line, i) => (
            <div key={i} className={`${
              line.startsWith("▸ TOOL") ? "text-yellow-400" :
              line.startsWith("▸ ERROR") ? "text-red-400" :
              line.startsWith("▸ DONE") ? "text-emerald-400" :
              line.startsWith("▸") ? "text-blue-400" :
              "text-white/50"
            }`}>
              {line}
            </div>
          ))
        )}
      </div>
    </div>
  );
}

// ── Main Component ───────────────────────────────────────────────────────────

export function EkgMonitor({ projectId, onClose }: EkgMonitorProps) {
  const [agents, setAgents] = useState<AgentLine[]>([]);
  const [expandedLog, setExpandedLog] = useState<string | null>(null);
  const [metrics, setMetrics] = useState<any>(null);
  const prevTokensRef = useRef<Record<string, { sent: number; recv: number }>>({});

  // Polling for real data
  useEffect(() => {
    let alive = true;

    async function poll() {
      try {
        const data = await api.get<any>(`/projects/${projectId}/monitor`);
        if (!alive) return;

        const agentsData = data.agents || {};
        const metricsData = data.metrics || {};
        setMetrics(metricsData);

        // Extract token info from last_calls for EKG spikes
        const lastCalls = metricsData.last_calls || [];

        setAgents(prev => {
          const agentIds = Object.keys(agentsData);
          return agentIds.map((agentId, idx) => {
            const ad = agentsData[agentId];
            const existing = prev.find(a => a.id === agentId);
            const colorIdx = idx % AGENT_COLORS.length;
            const isActive = ad.status === "running" && ad.current_activity != null;

            // Build points from existing or new
            const pts = existing ? [...existing.points] : new Array(POINT_COUNT).fill(0);
            pts.shift();

            // Check if tokens changed → create spike
            const prevTok = prevTokensRef.current[agentId] || { sent: 0, recv: 0 };
            const newSent = ad.total_requests || 0;
            if (newSent > prevTok.sent && isActive) {
              // Activity detected — spike based on response time
              const mag = Math.min(1, (ad.last_response_ms || 500) / 2000);
              pts.push(Math.random() > 0.5 ? mag : -mag);
            } else if (isActive) {
              // Active but no new request — small noise
              const last = pts[pts.length - 1] || 0;
              pts.push(last * 0.85 + (Math.random() - 0.5) * 0.15);
            } else {
              // Idle
              const last = pts[pts.length - 1] || 0;
              pts.push(last * 0.9 + (Math.random() - 0.5) * 0.02);
            }
            prevTokensRef.current[agentId] = { sent: newSent, recv: 0 };

            // Build log from activity changes
            const log = existing?.log || [];
            if (ad.current_activity && (!existing || existing.currentActivity !== ad.current_activity)) {
              const ts = new Date().toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
              if (ad.current_activity.toLowerCase().includes("tool")) {
                log.push(`▸ TOOL ${ts}  ${ad.current_activity}`);
              } else if (ad.current_activity.toLowerCase().includes("error")) {
                log.push(`▸ ERROR ${ts}  ${ad.current_activity}`);
              } else {
                log.push(`▸ ${ts}  ${ad.current_activity}`);
              }
              if (log.length > 200) log.splice(0, log.length - 200);
            }

            // Build tool chain from log
            const tools = existing?.tools || [];
            if (ad.current_activity && ad.current_activity.startsWith("Tool:")) {
              const toolName = ad.current_activity.replace("Tool: ", "").split(" ")[0];
              if (tools[tools.length - 1] !== toolName) {
                tools.push(toolName);
                if (tools.length > 6) tools.shift();
              }
            }

            return {
              id: agentId,
              name: ad.identity || agentId,
              tag: ad.role || ad.type || "worker",
              model: ad.model || "—",
              color: AGENT_COLORS[colorIdx].color,
              glowColor: AGENT_COLORS[colorIdx].glow,
              points: pts,
              tools,
              active: isActive || ad.status === "running",
              tokensSent: metricsData.total_input_tokens || 0,
              tokensReceived: metricsData.total_output_tokens || 0,
              log,
              currentActivity: ad.current_activity,
              avgResponseMs: ad.avg_response_ms || 0,
              errorRate: ad.error_rate || 0,
            };
          });
        });
      } catch (e) {
        // Silently ignore poll errors
      }
    }

    poll();
    const interval = setInterval(poll, POLL_INTERVAL);
    return () => { alive = false; clearInterval(interval); };
  }, [projectId]);

  // Keep points animating between polls
  useEffect(() => {
    const tick = setInterval(() => {
      setAgents(prev => prev.map(agent => {
        const pts = [...agent.points];
        pts.shift();
        if (agent.active) {
          const last = pts[pts.length - 1] || 0;
          pts.push(last * 0.88 + (Math.random() - 0.5) * 0.12);
        } else {
          const last = pts[pts.length - 1] || 0;
          pts.push(last * 0.92 + (Math.random() - 0.5) * 0.015);
        }
        return { ...agent, points: pts };
      }));
    }, 50);
    return () => clearInterval(tick);
  }, []);

  const totalTokens = (metrics?.total_input_tokens || 0) + (metrics?.total_output_tokens || 0);
  const cacheHit = metrics?.cache_hit_rate != null ? (metrics.cache_hit_rate * 100).toFixed(1) : "—";
  const activeCount = agents.filter(a => a.active).length;
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
            <span className="text-white/60">{agents.length} Agenten</span>
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

      {/* EKG Area */}
      <div className="flex flex-1 min-h-0 overflow-hidden">
        {/* Left: Agent labels */}
        <div className="flex flex-col w-48 flex-shrink-0 border-r border-white/10 py-2">
          {agents.map(agent => {
            const rowH = 100 / Math.max(agents.length, 1);
            return (
              <div key={agent.id} className="flex flex-col justify-center px-4" style={{ height: `${rowH}%` }}>
                <div className="flex items-center gap-2">
                  <span className={`w-2 h-2 rounded-full ${agent.active ? "animate-pulse" : ""}`}
                    style={{ backgroundColor: agent.color, boxShadow: agent.active ? `0 0 8px ${agent.glowColor}` : "none" }} />
                  <span className="text-xs font-medium text-white/90 truncate">{agent.name}</span>
                </div>
                <div className="flex items-center gap-1.5 mt-1">
                  <span className="text-[9px] rounded px-1 py-0.5 font-medium uppercase tracking-wider"
                    style={{ backgroundColor: agent.color + "20", color: agent.color }}>
                    {agent.tag}
                  </span>
                  <span className="text-[9px] text-white/30 truncate">{agent.model}</span>
                </div>
                <div className="flex gap-2 mt-1 text-[9px]">
                  {agent.avgResponseMs > 0 && <span className="text-yellow-400/60">Ø {Math.round(agent.avgResponseMs)}ms</span>}
                  {agent.errorRate > 0 && <span className="text-red-400/70">{agent.errorRate.toFixed(1)}% err</span>}
                </div>
              </div>
            );
          })}
          {agents.length === 0 && (
            <div className="flex items-center justify-center h-full text-white/20 text-xs">Lade Agenten…</div>
          )}
        </div>

        {/* Center: Canvas */}
        <div className="flex-1 min-w-0 relative">
          <EkgCanvas agents={agents} />
        </div>

        {/* Right: Tool chain */}
        <div className="flex flex-col w-56 flex-shrink-0 border-l border-white/10 py-2">
          {agents.map(agent => {
            const rowH = 100 / Math.max(agents.length, 1);
            return (
              <div key={agent.id} className="flex items-center px-3 overflow-hidden" style={{ height: `${rowH}%` }}>
                <div className="flex flex-wrap gap-1">
                  {agent.tools.length === 0 && <span className="text-[9px] text-white/20">—</span>}
                  {agent.tools.map((tool, i) => (
                    <span key={`${tool}-${i}`} className="inline-flex items-center gap-0.5 rounded px-1.5 py-0.5 text-[9px] font-mono transition-all duration-300"
                      style={{ backgroundColor: agent.color + "15", color: agent.color + "cc", borderLeft: `2px solid ${agent.color}40` }}>
                      {i > 0 && <span className="text-white/20 mr-0.5">▸</span>}
                      {tool}
                    </span>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Log Boxes */}
      <div className={`grid gap-2 p-3 border-t border-white/10 flex-shrink-0`}
        style={{ gridTemplateColumns: `repeat(${Math.min(agents.length || 1, 4)}, 1fr)`, height: expandedLog ? "auto" : "11rem" }}>
        {agents.slice(0, 4).map(agent => (
          <LogBox
            key={agent.id}
            agent={agent}
            expanded={expandedLog === agent.id}
            onToggle={() => setExpandedLog(expandedLog === agent.id ? null : agent.id)}
          />
        ))}
        {agents.length === 0 && (
          <div className="flex items-center justify-center text-white/20 text-xs col-span-full">Keine Agenten im Projekt</div>
        )}
      </div>
    </div>
  );
}
