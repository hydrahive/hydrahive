/**
 * EkgMonitor — Live Agent-Monitoring im EKG-Style (#534)
 *
 * Layout:
 *   [Header: 3 Info-Boxen (Claude Tokens | Codex Tokens | Projekt-Stats)]
 *   [Linke Box | EKG Canvas (mit Labels) | Rechte Box]
 *   [4 Log-Boxen]
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

// ── EKG Canvas — Labels links, rein ref-basiert ─────────────────────────────

function EkgCanvas({ agentsRef, agentList }: {
  agentsRef: React.RefObject<Map<string, { color: string; glow: string; active: boolean; points: number[] }>>;
  agentList: AgentData[];
}) {
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
    const LABEL_W = 130; // Platz für Labels links

    function resize() {
      if (!canvas) return;
      const rect = canvas.getBoundingClientRect();
      w = rect.width; h = rect.height;
      canvas.width = w * dpr; canvas.height = h * dpr;
      ctx!.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(canvas);

    function draw() {
      if (!ctx || w === 0) { animRef.current = requestAnimationFrame(draw); return; }
      const agents = agentsRef.current;
      if (!agents || agents.size === 0) { animRef.current = requestAnimationFrame(draw); return; }

      const count = agents.size;
      const rowH = h / count;
      const lineW = w - LABEL_W;

      ctx.clearRect(0, 0, w, h);

      // Grid (nur im Linien-Bereich)
      ctx.strokeStyle = "rgba(255,255,255,0.03)";
      ctx.lineWidth = 0.5;
      for (let i = 0; i < count; i++) {
        const y = i * rowH;
        ctx.beginPath(); ctx.moveTo(LABEL_W, y); ctx.lineTo(w, y); ctx.stroke();
        ctx.beginPath(); ctx.setLineDash([2, 6]);
        ctx.moveTo(LABEL_W, y + rowH / 2); ctx.lineTo(w, y + rowH / 2); ctx.stroke();
        ctx.setLineDash([]);
      }

      // Scanline
      scanPos.current = (scanPos.current + 0.5) % lineW;
      const sx = LABEL_W + scanPos.current;
      const grad = ctx.createLinearGradient(sx - 30, 0, sx, 0);
      grad.addColorStop(0, "transparent");
      grad.addColorStop(1, "rgba(255,255,255,0.05)");
      ctx.fillStyle = grad;
      ctx.fillRect(sx - 30, 0, 30, h);

      // Labels + Lines
      let idx = 0;
      const listArr = Array.from(agents.entries());
      listArr.forEach(([agentId, agent]) => {
        const baseY = idx * rowH + rowH / 2;
        const amplitude = rowH * 0.35;
        const step = lineW / (POINT_COUNT - 1);
        const info = agentList.find(a => a.id === agentId);

        // Label
        ctx.globalAlpha = 1;
        // Dot
        ctx.fillStyle = agent.color;
        ctx.shadowBlur = agent.active ? 6 : 0;
        ctx.shadowColor = agent.glow;
        ctx.beginPath();
        ctx.arc(12, baseY, 4, 0, Math.PI * 2);
        ctx.fill();
        ctx.shadowBlur = 0;

        // Name
        ctx.fillStyle = "rgba(255,255,255,0.85)";
        ctx.font = "bold 11px system-ui, sans-serif";
        ctx.textBaseline = "middle";
        ctx.fillText(info?.name || agentId, 24, baseY - 8);

        // Tag + Model
        ctx.fillStyle = agent.color + "aa";
        ctx.font = "9px system-ui, sans-serif";
        ctx.fillText((info?.tag || "").toUpperCase(), 24, baseY + 6);
        ctx.fillStyle = "rgba(255,255,255,0.25)";
        ctx.fillText(info?.model || "", 24 + ctx.measureText((info?.tag || "").toUpperCase()).width + 8, baseY + 6);

        // EKG Line
        ctx.strokeStyle = agent.color;
        ctx.lineWidth = agent.active ? 2 : 1;
        ctx.lineJoin = "round";
        ctx.lineCap = "round";
        ctx.shadowBlur = agent.active ? 10 : 0;
        ctx.shadowColor = agent.active ? agent.glow : "transparent";

        ctx.beginPath();
        for (let i = 0; i < agent.points.length; i++) {
          const x = LABEL_W + i * step;
          const y = baseY - agent.points[i] * amplitude;
          const dist = Math.abs((x - LABEL_W) - scanPos.current);
          ctx.globalAlpha = dist > lineW * 0.6 ? Math.max(0.08, 1 - (dist - lineW * 0.6) / (lineW * 0.4)) : 1;

          if (i === 0) { ctx.moveTo(x, y); }
          else {
            const px = LABEL_W + (i - 1) * step;
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
  }, [agentsRef, agentList]);

  return <canvas ref={canvasRef} className="w-full h-full" />;
}

// ── Info Box (header + side panels) ──────────────────────────────────────────

function InfoBox({ title, children, className }: { title: string; children: React.ReactNode; className?: string }) {
  return (
    <div className={`flex flex-col rounded-xl border border-white/10 bg-black/60 backdrop-blur overflow-hidden ${className || ""}`}>
      <div className="px-3 py-1.5 border-b border-white/10">
        <span className="text-[10px] font-medium uppercase tracking-wider text-white/40">{title}</span>
      </div>
      <div className="flex-1 px-3 py-2 text-[11px]">
        {children}
      </div>
    </div>
  );
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
        Object.keys(agentsData).forEach((agentId, idx) => {
          const ad = agentsData[agentId];
          const colorIdx = idx % AGENT_COLORS.length;
          const isActive = ad.status === "running";

          if (!pointsRef.current.has(agentId)) {
            pointsRef.current.set(agentId, {
              color: AGENT_COLORS[colorIdx].color, glow: AGENT_COLORS[colorIdx].glow,
              active: isActive, points: new Array(POINT_COUNT).fill(0),
            });
          }
          const entry = pointsRef.current.get(agentId)!;
          entry.active = isActive;

          if (ad.current_activity && ad.current_activity !== prevActivityRef.current[agentId]) {
            const mag = 0.4 + Math.random() * 0.5;
            entry.points.push(Math.random() > 0.5 ? mag : -mag);
            if (entry.points.length > POINT_COUNT) entry.points.shift();
          }
          prevActivityRef.current[agentId] = ad.current_activity;

          const prev = agentList.find(a => a.id === agentId);
          const log = prev?.log ? [...prev.log] : [];
          if (ad.current_activity && (!prev || prev.currentActivity !== ad.current_activity)) {
            const ts = new Date().toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
            const act = ad.current_activity;
            log.push(act.toLowerCase().includes("tool") ? `▸ TOOL ${ts}  ${act}` : act.toLowerCase().includes("error") ? `▸ ERROR ${ts}  ${act}` : `▸ ${ts}  ${act}`);
            if (log.length > 200) log.splice(0, log.length - 200);
          }

          const tools = prev?.tools ? [...prev.tools] : [];
          if (ad.current_activity?.startsWith("Tool:")) {
            const tn = ad.current_activity.replace("Tool: ", "").split(" ")[0];
            if (tools[tools.length - 1] !== tn) { tools.push(tn); if (tools.length > 6) tools.shift(); }
          }

          newList.push({
            id: agentId, name: ad.identity || agentId, tag: ad.role || ad.type || "worker",
            model: ad.model || "—", color: AGENT_COLORS[colorIdx].color, glowColor: AGENT_COLORS[colorIdx].glow,
            tools, active: isActive, currentActivity: ad.current_activity,
            avgResponseMs: ad.avg_response_ms || 0, errorRate: ad.error_rate || 0, log,
          });
        });
        setAgentList(newList);
      } catch { /* ignore */ }
    }
    poll();
    const iv = setInterval(poll, POLL_INTERVAL);
    return () => { alive = false; clearInterval(iv); };
  }, [projectId]);

  // Smooth animation — ref only
  useEffect(() => {
    const tick = setInterval(() => {
      pointsRef.current.forEach((entry) => {
        const last = entry.points[entry.points.length - 1] || 0;
        entry.points.push(entry.active ? last * 0.92 + (Math.random() - 0.5) * 0.08 : last * 0.95 + (Math.random() - 0.5) * 0.01);
        if (entry.points.length > POINT_COUNT) entry.points.shift();
      });
    }, 80);
    return () => clearInterval(tick);
  }, []);

  const totalIn = metrics?.total_input_tokens || 0;
  const totalOut = metrics?.total_output_tokens || 0;
  const cacheHit = metrics?.cache_hit_rate != null ? (metrics.cache_hit_rate * 100).toFixed(1) : "0";
  const cacheRead = metrics?.total_cache_read || 0;
  const activeCount = agentList.filter(a => a.active).length;
  const errorCount = metrics?.overflow_count || 0;
  const toolCalls = metrics?.tool_calls_total || 0;
  const avgLatency = metrics?.avg_latency_ms != null ? Math.round(metrics.avg_latency_ms) : 0;
  const llmCalls = metrics?.llm_call_count || 0;
  const compactions = metrics?.compaction_count || 0;

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-[#0a0a0f]/95 backdrop-blur-md">
      {/* Close */}
      <button onClick={onClose} className="absolute top-3 right-4 z-10 p-1.5 rounded-lg hover:bg-white/10 text-white/50 hover:text-white transition-colors">
        <X className="h-4 w-4" />
      </button>

      {/* Header: 3 Info-Boxen */}
      <div className="grid grid-cols-3 gap-2 p-3 flex-shrink-0">
        <InfoBox title="Claude Tokens">
          <div className="space-y-1.5">
            <div className="flex justify-between"><span className="text-white/40">Input</span><span className="text-blue-400 font-mono">{totalIn.toLocaleString()}</span></div>
            <div className="flex justify-between"><span className="text-white/40">Output</span><span className="text-emerald-400 font-mono">{totalOut.toLocaleString()}</span></div>
            <div className="flex justify-between"><span className="text-white/40">Cache Read</span><span className="text-cyan-400 font-mono">{cacheRead.toLocaleString()}</span></div>
            <div className="flex justify-between"><span className="text-white/40">Cache Hit</span><span className="text-cyan-400 font-mono">{cacheHit}%</span></div>
            <div className="h-1.5 rounded-full bg-white/5 mt-1">
              <div className="h-full rounded-full bg-gradient-to-r from-cyan-500 to-emerald-500 transition-all" style={{ width: `${Math.min(parseFloat(cacheHit), 100)}%` }} />
            </div>
          </div>
        </InfoBox>

        <InfoBox title="Codex / LLM">
          <div className="space-y-1.5">
            <div className="flex justify-between"><span className="text-white/40">LLM Calls</span><span className="text-purple-400 font-mono">{llmCalls}</span></div>
            <div className="flex justify-between"><span className="text-white/40">Tool Calls</span><span className="text-yellow-400 font-mono">{toolCalls}</span></div>
            <div className="flex justify-between"><span className="text-white/40">Ø Latenz</span><span className="text-yellow-400 font-mono">{avgLatency}ms</span></div>
            <div className="flex justify-between"><span className="text-white/40">Compactions</span><span className="text-orange-400 font-mono">{compactions}</span></div>
          </div>
        </InfoBox>

        <InfoBox title="Projekt-Status">
          <div className="space-y-1.5">
            <div className="flex justify-between"><span className="text-white/40">Agenten</span><span className="text-white/80 font-mono">{agentList.length}</span></div>
            <div className="flex justify-between"><span className="text-white/40">Aktiv</span><span className="text-emerald-400 font-mono">{activeCount}</span></div>
            <div className="flex justify-between"><span className="text-white/40">Errors</span><span className={`font-mono ${errorCount > 0 ? "text-red-400" : "text-white/30"}`}>{errorCount}</span></div>
            <div className="flex justify-between"><span className="text-white/40">Retries</span><span className="text-orange-400 font-mono">{metrics?.retries || 0}</span></div>
            <div className="flex justify-between"><span className="text-white/40">Failovers</span><span className="text-orange-400 font-mono">{metrics?.failovers || 0}</span></div>
          </div>
        </InfoBox>
      </div>

      {/* Middle: Left Box | EKG | Right Box */}
      <div className="flex flex-1 min-h-0 gap-2 px-3 overflow-hidden">
        {/* Left info box */}
        <InfoBox title="Tool-Chain" className="w-52 flex-shrink-0">
          <div className="space-y-3 overflow-y-auto h-full">
            {agentList.map(agent => (
              <div key={agent.id}>
                <div className="flex items-center gap-1.5 mb-1">
                  <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: agent.color }} />
                  <span className="text-[10px] text-white/60">{agent.name}</span>
                </div>
                <div className="flex flex-wrap gap-1 pl-3">
                  {agent.tools.length === 0 && <span className="text-[9px] text-white/15">—</span>}
                  {agent.tools.map((tool, i) => (
                    <span key={`${tool}-${i}`} className="inline-flex items-center rounded px-1.5 py-0.5 text-[9px] font-mono"
                      style={{ backgroundColor: agent.color + "15", color: agent.color + "cc", borderLeft: `2px solid ${agent.color}40` }}>
                      {i > 0 && <span className="text-white/20 mr-1">▸</span>}{tool}
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </InfoBox>

        {/* EKG Canvas (labels drawn on canvas) */}
        <div className="flex-1 min-w-0 rounded-xl border border-white/10 bg-black/40 overflow-hidden">
          <EkgCanvas agentsRef={pointsRef} agentList={agentList} />
        </div>

        {/* Right info box */}
        <InfoBox title="Agent-Details" className="w-52 flex-shrink-0">
          <div className="space-y-3 overflow-y-auto h-full">
            {agentList.map(agent => (
              <div key={agent.id} className="pb-2 border-b border-white/5 last:border-0">
                <div className="flex items-center gap-1.5">
                  <span className={`w-2 h-2 rounded-full ${agent.active ? "animate-pulse" : ""}`}
                    style={{ backgroundColor: agent.color, boxShadow: agent.active ? `0 0 6px ${agent.glowColor}` : "none" }} />
                  <span className="text-[11px] font-medium text-white/85">{agent.name}</span>
                </div>
                <div className="pl-4 mt-1 space-y-0.5 text-[9px]">
                  <div className="flex justify-between"><span className="text-white/30">Modell</span><span className="text-white/60 font-mono truncate ml-2">{agent.model}</span></div>
                  {agent.avgResponseMs > 0 && <div className="flex justify-between"><span className="text-white/30">Ø Response</span><span className="text-yellow-400/70 font-mono">{Math.round(agent.avgResponseMs)}ms</span></div>}
                  {agent.errorRate > 0 && <div className="flex justify-between"><span className="text-white/30">Fehlerrate</span><span className="text-red-400/70 font-mono">{agent.errorRate.toFixed(1)}%</span></div>}
                  {agent.currentActivity && <div className="text-emerald-400/60 font-mono truncate mt-1">{agent.currentActivity}</div>}
                </div>
              </div>
            ))}
          </div>
        </InfoBox>
      </div>

      {/* Log Boxes */}
      <div className="grid gap-2 p-3 flex-shrink-0"
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
