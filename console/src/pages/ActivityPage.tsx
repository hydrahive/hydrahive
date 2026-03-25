import { useEffect, useState, useCallback } from "react";
import { api, type AgentLiveEntry } from "@/lib/api";
import { useTranslation } from "react-i18next";
import { Activity, Square, RefreshCw, Zap, AlertTriangle, CheckCircle2, XCircle, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

const POLL_MS = 3000;

function statusIcon(status: string) {
  switch (status) {
    case "running":    return <CheckCircle2 className="w-4 h-4 text-green-500" />;
    case "starting":   return <Loader2 className="w-4 h-4 text-blue-400 animate-spin" />;
    case "restarting": return <RefreshCw className="w-4 h-4 text-yellow-400 animate-spin" />;
    case "error":      return <XCircle className="w-4 h-4 text-red-500" />;
    default:           return <Square className="w-4 h-4 text-gray-400" />;
  }
}

function statusLabel(status: string, t: (k: string) => string) {
  return t(`activity.status.${status}`) || status;
}

function TokenBar({ tokens, warn }: { tokens: number; warn: number }) {
  const pct = Math.min(100, (tokens / warn) * 100);
  const color = pct >= 100 ? "bg-red-500" : pct >= 70 ? "bg-yellow-400" : "bg-green-500";
  return (
    <div className="w-full">
      <div className="flex justify-between text-xs text-gray-500 dark:text-gray-400 mb-0.5">
        <span>{tokens.toLocaleString()} Tokens/h</span>
        <span className={cn("font-medium", pct >= 100 && "text-red-500")}>{Math.round(pct)}%</span>
      </div>
      <div className="h-1.5 rounded-full bg-gray-200 dark:bg-gray-700 overflow-hidden">
        <div className={cn("h-full rounded-full transition-all", color)} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function AgentCard({ agent, onStop }: { agent: AgentLiveEntry; onStop: (id: string) => void }) {
  const { t } = useTranslation();
  const [stopping, setStopping] = useState(false);
  const [confirmStop, setConfirmStop] = useState(false);

  const hbAge = agent.last_heartbeat_age ?? 0;
  const hbTimeout = agent.heartbeat_timeout ?? 90;
  const hbWarn = hbAge > hbTimeout * 0.8;

  async function handleStop() {
    if (!confirmStop) { setConfirmStop(true); setTimeout(() => setConfirmStop(false), 3000); return; }
    setStopping(true);
    try { await onStop(agent.id); } finally { setStopping(false); setConfirmStop(false); }
  }

  return (
    <div className={cn(
      "rounded-xl border p-4 flex flex-col gap-3 bg-white dark:bg-gray-800 shadow-sm",
      agent.status === "error" && "border-red-400",
      agent.status === "running" && "border-green-300 dark:border-green-700",
      agent.status === "stopped" && "border-gray-300 dark:border-gray-600 opacity-60",
    )}>
      {/* Header */}
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          {statusIcon(agent.status)}
          <div className="min-w-0">
            <div className="font-semibold text-sm truncate">{agent.identity}</div>
            <div className="text-xs text-gray-500 dark:text-gray-400 truncate">{agent.id}</div>
          </div>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          <span className="text-xs px-2 py-0.5 rounded-full bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300">
            {agent.type}
          </span>
          <button
            onClick={handleStop}
            disabled={stopping || agent.status === "stopped"}
            title={confirmStop ? t("activity.stopConfirm") : t("activity.stopBtn")}
            className={cn(
              "flex items-center gap-1 text-xs px-2 py-1 rounded-lg border font-medium transition-colors",
              confirmStop
                ? "bg-red-600 text-white border-red-600 hover:bg-red-700"
                : "border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 hover:bg-red-50 hover:border-red-400 hover:text-red-600 dark:hover:bg-red-900/30",
              (stopping || agent.status === "stopped") && "opacity-40 cursor-not-allowed"
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
          : "bg-gray-50 dark:bg-gray-700/40 text-gray-400 dark:text-gray-500"
      )}>
        {agent.current_activity
          ? <><Zap className="w-3 h-3 shrink-0" /><span className="truncate">{agent.current_activity}</span></>
          : <span className="italic">{t("activity.idle")}</span>
        }
      </div>

      {/* Metriken */}
      <div className="space-y-2">
        <TokenBar tokens={agent.tokens_1h} warn={agent.token_warn_threshold} />
        <div className="flex justify-between text-xs text-gray-500 dark:text-gray-400">
          <span>{agent.model ?? "—"}</span>
          <span className={cn(hbWarn && "text-yellow-500")}>
            {hbWarn && <AlertTriangle className="w-3 h-3 inline mr-0.5" />}
            HB {hbAge.toFixed(0)}s
            {agent.restart_count > 0 && <span className="ml-2 text-orange-500">↺{agent.restart_count}</span>}
          </span>
        </div>
      </div>
    </div>
  );
}

export function ActivityPage() {
  const { t } = useTranslation();
  const [data, setData] = useState<AgentLiveEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const r = await api.agentsLive();
      setData(r.agents);
      setLastUpdate(new Date());
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
    const id = setInterval(fetchData, POLL_MS);
    return () => clearInterval(id);
  }, [fetchData]);

  async function handleStop(id: string) {
    await api.stopAgent(id);
    await fetchData();
  }

  const running = data.filter(a => a.status === "running").length;
  const active  = data.filter(a => a.current_activity).length;

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Activity className="w-6 h-6 text-primary" />
          <div>
            <h1 className="text-2xl font-bold">{t("activity.title")}</h1>
            <p className="text-sm text-gray-500 dark:text-gray-400">{t("activity.subtitle")}</p>
          </div>
        </div>
        <div className="flex items-center gap-4 text-sm text-gray-500 dark:text-gray-400">
          {lastUpdate && (
            <span>{t("activity.updated")}: {lastUpdate.toLocaleTimeString()}</span>
          )}
          <div className="flex gap-3">
            <span className="flex items-center gap-1">
              <CheckCircle2 className="w-4 h-4 text-green-500" />{running} {t("activity.running")}
            </span>
            <span className="flex items-center gap-1">
              <Zap className="w-4 h-4 text-blue-500" />{active} {t("activity.active")}
            </span>
          </div>
        </div>
      </div>

      {/* States */}
      {loading && (
        <div className="flex items-center justify-center h-40 text-gray-400">
          <Loader2 className="w-6 h-6 animate-spin mr-2" />{t("activity.loading")}
        </div>
      )}
      {error && (
        <div className="rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 p-4 text-red-700 dark:text-red-400 text-sm flex items-center gap-2">
          <XCircle className="w-4 h-4 shrink-0" />{error}
        </div>
      )}

      {/* Grid */}
      {!loading && !error && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
          {data.map(agent => (
            <AgentCard key={agent.id} agent={agent} onStop={handleStop} />
          ))}
          {data.length === 0 && (
            <div className="col-span-full text-center text-gray-400 py-16">{t("activity.noAgents")}</div>
          )}
        </div>
      )}

      <p className="text-xs text-gray-400 dark:text-gray-600 text-right">
        {t("activity.pollInterval", { s: POLL_MS / 1000 })}
      </p>
    </div>
  );
}
