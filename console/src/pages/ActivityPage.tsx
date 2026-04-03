import { useEffect, useState, useCallback, useRef } from "react";
import { api, type AgentLiveEntry } from "@/lib/api";
import { useTranslation } from "react-i18next";
import {
  Activity, Square, RefreshCw, Zap, AlertTriangle,
  CheckCircle2, XCircle, Loader2, X, Terminal,
} from "lucide-react";
import { cn, agentCategory, AGENT_COLORS } from "@/lib/utils";

const POLL_MS = 3000;

// Alarm-Schwelle: Interval + 15s Puffer (passt sich an Agent-Konfiguration an)
function hbAlertThreshold(agent: AgentLiveEntry) {
  return (agent.heartbeat_interval ?? 30) + 15;
}

// ---------------------------------------------------------------- helpers

function isHung(agent: AgentLiveEntry) {
  return agent.status === "running" && (agent.last_heartbeat_age ?? 0) > hbAlertThreshold(agent);
}

function needsAttention(agent: AgentLiveEntry) {
  return agent.status === "error" || isHung(agent);
}

function statusIcon(status: string, hung: boolean) {
  if (hung) return <AlertTriangle className="w-4 h-4 text-orange-500" />;
  switch (status) {
    case "running":    return <CheckCircle2 className="w-4 h-4 text-green-500" />;
    case "starting":   return <Loader2 className="w-4 h-4 text-blue-400 animate-spin" />;
    case "restarting": return <RefreshCw className="w-4 h-4 text-yellow-400 animate-spin" />;
    case "error":      return <XCircle className="w-4 h-4 text-red-500" />;
    default:           return <Square className="w-4 h-4 text-gray-400" />;
  }
}

// ---------------------------------------------------------------- Sparkline

function Sparkline({ data, width = 120, height = 24 }: { data: number[]; width?: number; height?: number }) {
  if (!data.length || data.every(v => v === 0)) return null;
  const max = Math.max(...data, 1);
  const points = data.map((v, i) => {
    const x = (i / (data.length - 1)) * width;
    const y = height - (v / max) * (height - 2) - 1;
    return `${x},${y}`;
  }).join(" ");
  const fillPoints = `0,${height} ${points} ${width},${height}`;
  return (
    <svg width={width} height={height} className="shrink-0">
      <polyline points={fillPoints} fill="currentColor" opacity={0.1} />
      <polyline points={points} fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

// ---------------------------------------------------------------- TokenBar

function TokenBar({ tokens, warn, history }: { tokens: number; warn: number; history?: { minute: number; tokens: number }[] }) {
  const pct = Math.min(100, (tokens / warn) * 100);
  const color = pct >= 100 ? "bg-red-500" : pct >= 70 ? "bg-yellow-400" : "bg-green-500";
  const sparkColor = pct >= 100 ? "text-red-500" : pct >= 70 ? "text-yellow-400" : "text-primary";
  const sparkData = history?.map(h => h.tokens) ?? [];
  return (
    <div className="w-full">
      <div className="flex justify-between items-center text-xs text-gray-500 dark:text-gray-400 mb-0.5">
        <span>{tokens.toLocaleString()} Tokens/h</span>
        <div className="flex items-center gap-2">
          {sparkData.length > 0 && <span className={sparkColor}><Sparkline data={sparkData} /></span>}
          <span className={cn("font-medium", pct >= 100 && "text-red-500")}>{Math.round(pct)}%</span>
        </div>
      </div>
      <div className="h-1.5 rounded-full bg-gray-200 dark:bg-gray-700 overflow-hidden">
        <div className={cn("h-full rounded-full transition-all", color)} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- AgentCard

function AgentCard({
  agent, onStop, onSelect,
}: {
  agent: AgentLiveEntry;
  onStop: (id: string) => void;
  onSelect: (agent: AgentLiveEntry) => void;
}) {
  const { t } = useTranslation();
  const [stopping, setStopping] = useState(false);
  const [confirmStop, setConfirmStop] = useState(false);

  const hbAge  = agent.last_heartbeat_age ?? 0;
  const hung   = isHung(agent);
  const alert  = needsAttention(agent);

  async function handleStop(e: React.MouseEvent) {
    e.stopPropagation();
    if (!confirmStop) { setConfirmStop(true); setTimeout(() => setConfirmStop(false), 3000); return; }
    setStopping(true);
    try { await onStop(agent.id); } finally { setStopping(false); setConfirmStop(false); }
  }

  return (
    <div
      onClick={() => onSelect(agent)}
      className={cn(
        "rounded-xl border p-4 flex flex-col gap-3 shadow-sm cursor-pointer transition-all",
        "hover:shadow-md hover:-translate-y-px",
        // Kategorie-Farbe als Basis
        !alert && AGENT_COLORS[agentCategory(agent.id, agent.type ?? undefined)].bg,
        !alert && AGENT_COLORS[agentCategory(agent.id, agent.type ?? undefined)].border,
        // Alerts überschreiben Kategorie-Farbe
        alert && agent.status === "error" && "border-red-400 bg-red-50/30 dark:bg-red-900/10",
        alert && hung                     && "border-orange-400 bg-orange-50/30 dark:bg-orange-900/10",
        agent.status === "stopped" && "opacity-60",
      )}
    >
      {/* Alert-Banner */}
      {alert && (
        <div className={cn(
          "flex items-center gap-1.5 text-xs px-2 py-1 rounded-md font-medium -mt-1 -mx-1",
          agent.status === "error" ? "bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-400"
                                   : "bg-orange-100 dark:bg-orange-900/30 text-orange-700 dark:text-orange-400",
        )}>
          <AlertTriangle className="w-3 h-3 shrink-0" />
          {agent.status === "error"
            ? t("activity.alertError")
            : t("activity.alertHung", { s: Math.round(hbAge) })}
        </div>
      )}

      {/* Header */}
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          {statusIcon(agent.status, hung)}
          <div className="min-w-0">
            <div className="font-semibold text-sm truncate">{agent.identity}</div>
            <div className="text-xs text-gray-500 dark:text-gray-400 truncate">{agent.id}</div>
          </div>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          {(() => { const c = AGENT_COLORS[agentCategory(agent.id, agent.type ?? undefined)]; return (
            <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${c.badge}`}>{c.label}</span>
          ); })()}
          <button
            onClick={handleStop}
            disabled={stopping || agent.status === "stopped"}
            title={confirmStop ? t("activity.stopConfirm") : t("activity.stopBtn")}
            className={cn(
              "flex items-center gap-1 text-xs px-2 py-1 rounded-lg border font-medium transition-colors",
              confirmStop
                ? "bg-red-600 text-white border-red-600 hover:bg-red-700"
                : "border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 hover:bg-red-50 hover:border-red-400 hover:text-red-600 dark:hover:bg-red-900/30",
              (stopping || agent.status === "stopped") && "opacity-40 cursor-not-allowed",
            )}
          >
            {stopping ? <Loader2 className="w-3 h-3 animate-spin" /> : <Square className="w-3 h-3" />}
            {confirmStop ? t("activity.stopConfirm") : t("activity.stopBtn")}
          </button>
        </div>
      </div>

      {/* Aktivität */}
      <div className={cn(
        "flex items-center gap-2 text-xs rounded-lg px-3 py-2 min-h-[2rem]",
        agent.current_activity
          ? "bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-300"
          : "bg-gray-50 dark:bg-gray-700/40 text-gray-400 dark:text-gray-500",
      )}>
        {agent.current_activity
          ? <><Zap className="w-3 h-3 shrink-0" /><span className="truncate">{agent.current_activity}</span></>
          : <span className="italic">{t("activity.idle")}</span>}
      </div>

      {/* Metriken */}
      <div className="space-y-2">
        <TokenBar tokens={agent.tokens_1h} warn={agent.token_warn_threshold} history={agent.token_history} />
        <div className="flex justify-between text-xs text-gray-500 dark:text-gray-400">
          <span>{agent.model ?? "—"}</span>
          <span className={cn(hung && "text-orange-500", agent.status === "error" && "text-red-500")}>
            {(hung || agent.status === "error") && <AlertTriangle className="w-3 h-3 inline mr-0.5" />}
            HB {hbAge.toFixed(0)}s
            {agent.restart_count > 0 && <span className="ml-2 text-orange-500">↺{agent.restart_count}</span>}
          </span>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- AgentDetailPanel

function AgentDetailPanel({
  agent, onClose, onStop, onRefresh,
}: {
  agent: AgentLiveEntry;
  onClose: () => void;
  onStop: (id: string) => void;
  onRefresh: () => void;
}) {
  const { t } = useTranslation();
  const [logs, setLogs] = useState<string[]>([]);
  const [logsLoading, setLogsLoading] = useState(true);
  const logsEndRef = useRef<HTMLDivElement>(null);
  const [stopping, setStopping] = useState(false);
  const [confirmStop, setConfirmStop] = useState(false);

  const fetchLogs = useCallback(async () => {
    try {
      const r = await api.agentLogs(agent.id, 80);
      setLogs(r.lines);
    } catch { /* ignore */ }
    finally { setLogsLoading(false); }
  }, [agent.id]);

  useEffect(() => {
    fetchLogs();
    const id = setInterval(fetchLogs, POLL_MS);
    return () => clearInterval(id);
  }, [fetchLogs]);

  useEffect(() => {
    logsEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs]);

  async function handleStop() {
    if (!confirmStop) { setConfirmStop(true); setTimeout(() => setConfirmStop(false), 3000); return; }
    setStopping(true);
    try { await onStop(agent.id); onRefresh(); } finally { setStopping(false); setConfirmStop(false); }
  }

  const hung  = isHung(agent);
  const alert = needsAttention(agent);
  const hbAge = agent.last_heartbeat_age ?? 0;

  return (
    <>
      {/* Backdrop */}
      <div className="fixed inset-0 bg-black/30 dark:bg-black/50 z-40" onClick={onClose} />

      {/* Panel */}
      <div className="fixed inset-y-0 right-0 w-full max-w-lg bg-white dark:bg-gray-900 shadow-2xl z-50 flex flex-col">

        {/* Panel-Header */}
        <div className={cn(
          "flex items-center justify-between px-5 py-4 border-b shrink-0",
          alert && agent.status === "error" && "bg-red-50 dark:bg-red-900/20 border-red-200 dark:border-red-800",
          alert && hung && "bg-orange-50 dark:bg-orange-900/20 border-orange-200 dark:border-orange-800",
          !alert && "border-gray-200 dark:border-gray-700",
        )}>
          <div className="flex items-center gap-3 min-w-0">
            {statusIcon(agent.status, hung)}
            <div className="min-w-0">
              <div className="font-bold text-base truncate">{agent.identity}</div>
              <div className="text-xs text-gray-500 dark:text-gray-400">{agent.id} · {agent.type}</div>
            </div>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-500">
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Status-Infos */}
        <div className="px-5 py-4 border-b border-gray-100 dark:border-gray-800 shrink-0 space-y-3">
          {alert && (
            <div className={cn(
              "flex items-center gap-2 text-sm px-3 py-2 rounded-lg font-medium",
              agent.status === "error"
                ? "bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-400"
                : "bg-orange-100 dark:bg-orange-900/30 text-orange-700 dark:text-orange-400",
            )}>
              <AlertTriangle className="w-4 h-4 shrink-0" />
              {agent.status === "error"
                ? t("activity.alertError")
                : t("activity.alertHung", { s: Math.round(hbAge) })}
            </div>
          )}

          {/* Aktivität */}
          <div className={cn(
            "flex items-center gap-2 text-sm rounded-lg px-3 py-2",
            agent.current_activity
              ? "bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-300"
              : "bg-gray-50 dark:bg-gray-800 text-gray-400",
          )}>
            {agent.current_activity
              ? <><Zap className="w-3.5 h-3.5 shrink-0" /><span>{agent.current_activity}</span></>
              : <span className="italic text-xs">{t("activity.idle")}</span>}
          </div>

          {/* Metriken-Zeile */}
          <div className="grid grid-cols-3 gap-3 text-xs">
            <div className="bg-gray-50 dark:bg-gray-800 rounded-lg px-3 py-2">
              <div className="text-gray-400 mb-0.5">{t("activity.detail.model")}</div>
              <div className="font-medium truncate">{agent.model ?? "—"}</div>
            </div>
            <div className="bg-gray-50 dark:bg-gray-800 rounded-lg px-3 py-2">
              <div className="text-gray-400 mb-0.5">{t("activity.detail.heartbeat")}</div>
              <div className={cn("font-medium", hung && "text-orange-500")}>
                {hbAge.toFixed(0)}s
              </div>
            </div>
            <div className="bg-gray-50 dark:bg-gray-800 rounded-lg px-3 py-2">
              <div className="text-gray-400 mb-0.5">{t("activity.detail.restarts")}</div>
              <div className={cn("font-medium", agent.restart_count > 0 && "text-orange-500")}>
                {agent.restart_count}
              </div>
            </div>
          </div>

          <TokenBar tokens={agent.tokens_1h} warn={agent.token_warn_threshold} history={agent.token_history} />

          {/* Stop-Button */}
          <button
            onClick={handleStop}
            disabled={stopping || agent.status === "stopped"}
            className={cn(
              "w-full flex items-center justify-center gap-2 text-sm px-4 py-2 rounded-lg border font-medium transition-colors",
              confirmStop
                ? "bg-red-600 text-white border-red-600 hover:bg-red-700"
                : "border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 hover:bg-red-50 hover:border-red-400 hover:text-red-600 dark:hover:bg-red-900/30",
              (stopping || agent.status === "stopped") && "opacity-40 cursor-not-allowed",
            )}
          >
            {stopping ? <Loader2 className="w-4 h-4 animate-spin" /> : <Square className="w-4 h-4" />}
            {confirmStop ? t("activity.stopConfirm") : t("activity.stopBtn")}
          </button>
        </div>

        {/* Logs */}
        <div className="flex items-center gap-2 px-5 py-2.5 border-b border-gray-100 dark:border-gray-800 shrink-0">
          <Terminal className="w-3.5 h-3.5 text-gray-400" />
          <span className="text-xs font-medium text-gray-500 dark:text-gray-400">{t("activity.detail.logs")}</span>
          {logsLoading && <Loader2 className="w-3 h-3 animate-spin text-gray-400 ml-auto" />}
        </div>
        <div className="flex-1 overflow-y-auto px-4 py-3 font-mono text-xs bg-gray-950 text-green-400 leading-relaxed">
          {logs.length === 0 && !logsLoading && (
            <span className="text-gray-500 italic">{t("activity.detail.noLogs")}</span>
          )}
          {logs.map((line, i) => (
            <div key={i} className={cn(
              "whitespace-pre-wrap break-all",
              line.includes("ERROR") || line.includes("error") ? "text-red-400" :
              line.includes("WARNING") || line.includes("warn") ? "text-yellow-400" :
              line.includes("INFO") ? "text-green-400" : "text-gray-400",
            )}>{line}</div>
          ))}
          <div ref={logsEndRef} />
        </div>
      </div>
    </>
  );
}

// ---------------------------------------------------------------- ActivityPage

export function ActivityPage() {
  const { t } = useTranslation();
  const [data, setData] = useState<AgentLiveEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);
  const [selected, setSelected] = useState<AgentLiveEntry | null>(null);
  const selectedRef = useRef<AgentLiveEntry | null>(null);
  selectedRef.current = selected;

  const fetchData = useCallback(async () => {
    try {
      const r = await api.agentsLive();
      setData(r.agents);
      // selected-Agent-Daten synchron halten — via Ref, nicht Closure,
      // damit der Interval nicht verhindert dass das Panel geschlossen wird
      if (selectedRef.current) {
        const updated = r.agents.find(a => a.id === selectedRef.current!.id);
        if (updated) setSelected(updated);
      }
      setLastUpdate(new Date());
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []); // keine Dependency auf selected — Ref übernimmt das

  useEffect(() => {
    fetchData();
    const id = setInterval(fetchData, POLL_MS);
    return () => clearInterval(id);
  }, [fetchData]);

  async function handleStop(id: string) {
    await api.stopAgent(id);
    await fetchData();
  }

  const running  = data.filter(a => a.status === "running").length;
  const active   = data.filter(a => a.current_activity).length;
  const alerts   = data.filter(a => needsAttention(a)).length;

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <Activity className="w-6 h-6 text-primary" />
          <div>
            <h1 className="text-2xl font-bold">{t("activity.title")}</h1>
            <p className="text-sm text-gray-500 dark:text-gray-400">{t("activity.subtitle")}</p>
          </div>
        </div>
        <div className="flex items-center gap-4 text-sm text-gray-500 dark:text-gray-400 flex-wrap">
          {lastUpdate && <span>{t("activity.updated")}: {lastUpdate.toLocaleTimeString()}</span>}
          <div className="flex gap-3">
            <span className="flex items-center gap-1">
              <CheckCircle2 className="w-4 h-4 text-green-500" />{running} {t("activity.running")}
            </span>
            <span className="flex items-center gap-1">
              <Zap className="w-4 h-4 text-blue-500" />{active} {t("activity.active")}
            </span>
            {alerts > 0 && (
              <span className="flex items-center gap-1 text-orange-500 font-medium">
                <AlertTriangle className="w-4 h-4" />{alerts} {t("activity.alerts")}
              </span>
            )}
          </div>
        </div>
      </div>

      {loading && data.length === 0 && (
        <div className="flex items-center justify-center h-40 text-gray-400">
          <Loader2 className="w-6 h-6 animate-spin mr-2" />{t("activity.loading")}
        </div>
      )}
      {error && (
        <div className="rounded-lg bg-orange-50 dark:bg-orange-900/20 border border-orange-200 dark:border-orange-800 p-3 text-orange-700 dark:text-orange-400 text-sm flex items-center gap-2">
          <XCircle className="w-4 h-4 shrink-0" />
          <span>{t("activity.reconnecting", "Verbindung unterbrochen — reconnecting…")}</span>
        </div>
      )}

      {data.length > 0 && (
        <div className={cn("grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4", error && "opacity-60")}>
          {data.map(agent => (
            <AgentCard
              key={agent.id}
              agent={agent}
              onStop={handleStop}
              onSelect={setSelected}
            />
          ))}
        </div>
      )}
      {!loading && !error && data.length === 0 && (
        <div className="text-center text-gray-400 py-16">{t("activity.noAgents")}</div>
      )}

      <p className="text-xs text-gray-400 dark:text-gray-600 text-right">
        {t("activity.pollInterval", { s: POLL_MS / 1000 })}
      </p>

      {/* Detail-Panel */}
      {selected && (
        <AgentDetailPanel
          agent={selected}
          onClose={() => setSelected(null)}
          onStop={handleStop}
          onRefresh={fetchData}
        />
      )}
    </div>
  );
}
