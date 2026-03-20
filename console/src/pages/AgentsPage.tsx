import { useEffect, useState } from "react";
import { Bot, RefreshCw, Circle } from "lucide-react";
import { api } from "@/lib/api";

interface AgentRuntime {
  status:             string;
  type:               string;
  restart_count:      number;
  last_heartbeat_age: number;
  heartbeat_timeout:  number;
  on_failure:         string;
}

interface AgentEntry {
  config:  { type: string; identity: string; model: string };
  runtime: AgentRuntime | null;
}

const STATUS_COLORS: Record<string, string> = {
  running:    "text-green-500",
  starting:   "text-yellow-500",
  restarting: "text-orange-500",
  stopped:    "text-muted-foreground",
  error:      "text-destructive",
};

export function AgentsPage() {
  const [agents,   setAgents]   = useState<Record<string, AgentEntry>>({});
  const [loading,  setLoading]  = useState(true);
  const [error,    setError]    = useState("");
  const [refreshing, setRefreshing] = useState(false);

  async function load() {
    try {
      const data = await api.agents() as Record<string, AgentEntry>;
      setAgents(data);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Fehler beim Laden");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  useEffect(() => { load(); }, []);

  function refresh() { setRefreshing(true); load(); }

  const agentList = Object.entries(agents);

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Agenten</h1>
          <p className="text-sm text-muted-foreground">
            {agentList.length} Agent{agentList.length !== 1 ? "en" : ""} registriert
          </p>
        </div>
        <button
          onClick={refresh}
          disabled={refreshing}
          className="flex items-center gap-2 px-3 py-1.5 text-sm border rounded-md hover:bg-accent transition-colors disabled:opacity-50"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? "animate-spin" : ""}`} />
          Aktualisieren
        </button>
      </div>

      {/* Error */}
      {error && (
        <div className="bg-destructive/10 border border-destructive/20 rounded-lg px-4 py-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="space-y-3">
          {[1,2,3].map(i => (
            <div key={i} className="bg-card border rounded-lg p-4 animate-pulse">
              <div className="h-4 bg-muted rounded w-1/4 mb-2" />
              <div className="h-3 bg-muted rounded w-1/2" />
            </div>
          ))}
        </div>
      )}

      {/* Agenten-Liste */}
      {!loading && agentList.length === 0 && (
        <div className="bg-card border rounded-lg p-12 text-center space-y-3">
          <Bot className="h-10 w-10 mx-auto text-muted-foreground" />
          <p className="text-sm text-muted-foreground">
            Keine Agenten gefunden. Lege Agenten unter <code className="text-xs">/agents/</code> an.
          </p>
        </div>
      )}

      {!loading && agentList.length > 0 && (
        <div className="space-y-3">
          {agentList.map(([id, agent]) => {
            const rt      = agent.runtime;
            const status  = rt?.status ?? "unbekannt";
            const color   = STATUS_COLORS[status] ?? "text-muted-foreground";
            const hbAge   = rt?.last_heartbeat_age;
            const hbWarn  = hbAge != null && hbAge > (rt?.heartbeat_timeout ?? 90) * 0.8;

            return (
              <div key={id} className="bg-card border rounded-lg p-4 flex items-start gap-4">
                {/* Icon */}
                <div className="w-9 h-9 rounded-md bg-primary/10 flex items-center justify-center flex-shrink-0">
                  <Bot className="h-5 w-5 text-primary" />
                </div>

                {/* Info */}
                <div className="flex-1 min-w-0 space-y-1">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-sm">{agent.config.identity}</span>
                    <span className="text-xs text-muted-foreground">({id})</span>
                    <span className="text-xs px-1.5 py-0.5 rounded bg-secondary text-secondary-foreground">
                      {agent.config.type}
                    </span>
                  </div>
                  <p className="text-xs text-muted-foreground">{agent.config.model}</p>
                </div>

                {/* Status */}
                <div className="flex flex-col items-end gap-1 flex-shrink-0">
                  <div className={`flex items-center gap-1.5 text-sm font-medium ${color}`}>
                    <Circle className="h-2 w-2 fill-current" />
                    {status}
                  </div>
                  {rt && (
                    <div className={`text-xs ${hbWarn ? "text-orange-500" : "text-muted-foreground"}`}>
                      HB {hbAge?.toFixed(0)}s
                      {rt.restart_count > 0 && <span className="ml-2 text-orange-500">↺ {rt.restart_count}</span>}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
