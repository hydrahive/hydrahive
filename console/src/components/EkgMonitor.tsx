/**
 * EkgMonitor — Live Agent-Monitoring im EKG-Style (#534)
 *
 * Canvas-basierte Echtzeit-Visualisierung der Agent-Aktivität.
 * Scanline-Animation, Bezier-Kurven, Glow-Effekt.
 *
 * Beta: Fake-Daten für Prototyp, später SSE-Anbindung.
 */
import { useEffect, useRef, useState, useCallback } from "react";
import { X, Maximize2, Minimize2 } from "lucide-react";

// ── Types ────────────────────────────────────────────────────────────────────

interface AgentLine {
  id: string;
  name: string;
  tag: "boss" | "worker" | "specialist" | "idle";
  model: string;
  color: string;
  glowColor: string;
  points: number[];        // y-values ring buffer (normalized -1 to 1)
  tools: string[];         // recent tool chain
  active: boolean;
  tokensSent: number;
  tokensReceived: number;
  log: string[];           // terminal log lines
}

interface EkgMonitorProps {
  onClose: () => void;
}

// ── Constants ────────────────────────────────────────────────────────────────

const AGENT_COLORS = [
  { color: "#22c55e", glow: "#22c55e80" },  // green — boss
  { color: "#3b82f6", glow: "#3b82f680" },  // blue
  { color: "#f97316", glow: "#f9731680" },  // orange
  { color: "#a855f7", glow: "#a855f780" },  // purple
  { color: "#ec4899", glow: "#ec489980" },  // pink
  { color: "#06b6d4", glow: "#06b6d480" },  // cyan
];

const TOOLS = ["shell_exec", "read_file", "write_file", "web_search", "ask_agent", "delegate_agent", "read_memory", "write_memory"];
const LOG_LINES = [
  "> docker ps --format '{{.Names}}'",
  "nginx-proxy",
  "hydrahive-core",
  "bookstack-app",
  "> cat /etc/nginx/sites-enabled/default",
  "server {",
  "    listen 80;",
  "    server_name _;",
  "    location / {",
  "        proxy_pass http://127.0.0.1:8765;",
  "    }",
  "}",
  "> systemctl status hydrahive",
  "● hydrahive.service - HydraHive Core",
  "   Active: active (running) since Wed",
  "> df -h /opt",
  "Filesystem  Size  Used Avail Use%",
  "/dev/sda1    50G   12G   35G  26%",
  "> git log --oneline -3",
  "f47e61d feat: AutoDream sensibler",
  "9fe8811 fix: ChatView min-h-0",
  "d801d65 fix: BookStack APP_URL",
  "> python3 -c 'import json; print(\"OK\")'",
  "OK",
  "> curl -sf http://localhost:8765/health",
  '{"status": "ok", "agents": 4, "uptime": 86400}',
];

const POINT_COUNT = 200;
const SCAN_SPEED = 1.5;  // pixels per frame

// ── Fake Data Generator ──────────────────────────────────────────────────────

function createFakeAgents(): AgentLine[] {
  return [
    { id: "castiel", name: "Castiel", tag: "boss", model: "claude-sonnet-4-6", color: AGENT_COLORS[0].color, glowColor: AGENT_COLORS[0].glow, points: new Array(POINT_COUNT).fill(0), tools: [], active: true, tokensSent: 0, tokensReceived: 0, log: [] },
    { id: "devops", name: "DevOps Engineer", tag: "worker", model: "claude-haiku-4-5", color: AGENT_COLORS[1].color, glowColor: AGENT_COLORS[1].glow, points: new Array(POINT_COUNT).fill(0), tools: [], active: true, tokensSent: 0, tokensReceived: 0, log: [] },
    { id: "sysadmin", name: "SysAdmin", tag: "worker", model: "claude-haiku-4-5", color: AGENT_COLORS[2].color, glowColor: AGENT_COLORS[2].glow, points: new Array(POINT_COUNT).fill(0), tools: [], active: false, tokensSent: 0, tokensReceived: 0, log: [] },
    { id: "coder", name: "Full-Stack Coder", tag: "specialist", model: "claude-sonnet-4-6", color: AGENT_COLORS[3].color, glowColor: AGENT_COLORS[3].glow, points: new Array(POINT_COUNT).fill(0), tools: [], active: false, tokensSent: 0, tokensReceived: 0, log: [] },
  ];
}

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
      const rowH = h / agents.length;

      ctx.clearRect(0, 0, w, h);

      // Background grid
      ctx.strokeStyle = "rgba(255,255,255,0.03)";
      ctx.lineWidth = 0.5;
      for (let y = 0; y < h; y += rowH) {
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(w, y);
        ctx.stroke();
        // Horizontal gridlines
        const mid = y + rowH / 2;
        ctx.beginPath();
        ctx.setLineDash([2, 6]);
        ctx.moveTo(0, mid);
        ctx.lineTo(w, mid);
        ctx.stroke();
        ctx.setLineDash([]);
      }

      // Scanline position
      scanPos.current = (scanPos.current + SCAN_SPEED) % w;
      const sx = scanPos.current;

      // Scanline glow
      const grad = ctx.createLinearGradient(sx - 40, 0, sx, 0);
      grad.addColorStop(0, "transparent");
      grad.addColorStop(1, "rgba(255,255,255,0.06)");
      ctx.fillStyle = grad;
      ctx.fillRect(sx - 40, 0, 40, h);

      // Draw each agent line
      agents.forEach((agent, idx) => {
        const baseY = idx * rowH + rowH / 2;
        const amplitude = rowH * 0.35;

        // Line style
        ctx.strokeStyle = agent.color;
        ctx.lineWidth = agent.active ? 2 : 1;
        ctx.lineJoin = "round";
        ctx.lineCap = "round";

        // Glow
        if (agent.active) {
          ctx.shadowBlur = 12;
          ctx.shadowColor = agent.glowColor;
        } else {
          ctx.shadowBlur = 0;
        }

        // Draw bezier through points
        ctx.beginPath();
        const step = w / (POINT_COUNT - 1);

        for (let i = 0; i < POINT_COUNT; i++) {
          const x = i * step;
          const y = baseY - agent.points[i] * amplitude;

          // Fade out old data (far from scanline)
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
            // Smooth bezier
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
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-white/10 flex-shrink-0">
        <div className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-full" style={{ backgroundColor: agent.color, boxShadow: agent.active ? `0 0 6px ${agent.glowColor}` : "none" }} />
          <span className="text-[11px] font-medium text-white/80">{agent.name}</span>
        </div>
        <button onClick={onToggle} className="p-0.5 rounded hover:bg-white/10 text-white/40 hover:text-white/70 transition-colors">
          {expanded ? <Minimize2 className="h-3 w-3" /> : <Maximize2 className="h-3 w-3" />}
        </button>
      </div>
      {/* Log content */}
      <div ref={logRef} className={`overflow-y-auto font-mono text-[11px] leading-relaxed px-3 py-2 ${expanded ? "flex-1" : "h-32"}`}>
        {agent.log.length === 0 ? (
          <span className="text-white/20">(idle)</span>
        ) : (
          agent.log.map((line, i) => (
            <div key={i} className={`${line.startsWith(">") ? "text-emerald-400" : "text-white/60"}`}>
              {line}
            </div>
          ))
        )}
      </div>
    </div>
  );
}

// ── Main Component ───────────────────────────────────────────────────────────

export function EkgMonitor({ onClose }: EkgMonitorProps) {
  const [agents, setAgents] = useState<AgentLine[]>(createFakeAgents);
  const [expandedLog, setExpandedLog] = useState<string | null>(null);
  const tickRef = useRef<ReturnType<typeof setInterval>>();

  // Fake data simulation
  useEffect(() => {
    let logIdx = 0;

    tickRef.current = setInterval(() => {
      setAgents(prev => prev.map(agent => {
        const pts = [...agent.points];
        // Shift left
        pts.shift();

        if (agent.active) {
          // Random spikes: up = send, down = receive
          const r = Math.random();
          if (r < 0.08) {
            // Big spike — token burst
            const dir = Math.random() > 0.5 ? 1 : -1;
            const mag = 0.5 + Math.random() * 0.5;
            pts.push(dir * mag);
          } else if (r < 0.25) {
            // Small activity
            pts.push((Math.random() - 0.5) * 0.3);
          } else {
            // Gentle drift back to zero
            const last = pts[pts.length - 1] || 0;
            pts.push(last * 0.85 + (Math.random() - 0.5) * 0.05);
          }

          // Random tool calls
          const newTools = [...agent.tools];
          if (Math.random() < 0.03) {
            newTools.push(TOOLS[Math.floor(Math.random() * TOOLS.length)]);
            if (newTools.length > 5) newTools.shift();
          }

          // Random log lines
          const newLog = [...agent.log];
          if (Math.random() < 0.08) {
            newLog.push(LOG_LINES[logIdx % LOG_LINES.length]);
            logIdx++;
            if (newLog.length > 100) newLog.splice(0, newLog.length - 100);
          }

          // Token counting
          const sent = agent.tokensSent + (Math.random() < 0.1 ? Math.floor(Math.random() * 500 + 100) : 0);
          const recv = agent.tokensReceived + (Math.random() < 0.1 ? Math.floor(Math.random() * 300 + 50) : 0);

          return { ...agent, points: pts, tools: newTools, log: newLog, tokensSent: sent, tokensReceived: recv };
        } else {
          // Idle: minimal noise
          const last = pts[pts.length - 1] || 0;
          pts.push(last * 0.9 + (Math.random() - 0.5) * 0.02);

          // Occasionally wake up
          if (Math.random() < 0.005) {
            return { ...agent, points: pts, active: true };
          }
          return { ...agent, points: pts };
        }
      }));
    }, 50);  // 20 FPS data update

    return () => clearInterval(tickRef.current);
  }, []);

  const totalTokens = agents.reduce((s, a) => s + a.tokensSent + a.tokensReceived, 0);
  const activeCount = agents.filter(a => a.active).length;
  const errorCount = 0;

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
            const rowH = 100 / agents.length;
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
                  <span className="text-emerald-400/70">↑{agent.tokensSent.toLocaleString()}</span>
                  <span className="text-blue-400/70">↓{agent.tokensReceived.toLocaleString()}</span>
                </div>
              </div>
            );
          })}
        </div>

        {/* Center: Canvas */}
        <div className="flex-1 min-w-0 relative">
          <EkgCanvas agents={agents} />
        </div>

        {/* Right: Tool chain */}
        <div className="flex flex-col w-56 flex-shrink-0 border-l border-white/10 py-2">
          {agents.map(agent => {
            const rowH = 100 / agents.length;
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
      <div className="grid grid-cols-4 gap-2 p-3 border-t border-white/10 flex-shrink-0" style={{ height: expandedLog ? "auto" : "11rem" }}>
        {agents.map(agent => (
          <LogBox
            key={agent.id}
            agent={agent}
            expanded={expandedLog === agent.id}
            onToggle={() => setExpandedLog(expandedLog === agent.id ? null : agent.id)}
          />
        ))}
      </div>
    </div>
  );
}
