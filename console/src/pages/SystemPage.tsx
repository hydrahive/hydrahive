import { useEffect, useState } from "react";
import { RefreshCw, CheckCircle, XCircle, Clock, Cpu, HardDrive, Activity } from "lucide-react";
import { api } from "@/lib/api";

interface RuntimeAgent {
  status:             string;
  type:               string;
  restart_count:      number;
  last_heartbeat_age: number;
  heartbeat_timeout:  number;
  on_failure:         string;
}

interface SystemStatus {
  discovery?: { agents_dir: string; count: number };
  projects?:  { projects_dir: string; count: number };
  sessions?:  { active_projects: string[] };
  runtime?:   Record<string, RuntimeAgent>;
}

const STATUS_ICON: Record<string, JSX.Element> = {
  running:    <CheckCircle className="h-4 w-4 text-green-500" />,
  starting:   <Clock className="h-4 w-4 text-yellow-500" />,
  restarting: <Clock className="h-4 w-4 text-orange-500" />,
  stopped:    <XCircle className="h-4 w-4 text-muted-foreground" />,
  error:      <XCircle className="h-4 w-4 text-destructive" />,
};

function ServiceRow({ name, status }: { name: string; status: "ok" | "error" | "unknown" }) {
  return (
    <div className="flex items-center justify-between py-2 border-b last:border-0">
      <span className="text-sm">{name}</span>
      <div className="flex items-center gap-1.5">
        {status === "ok"
          ? <CheckCircle className="h-4 w-4 text-green-500" />
          : status === "error"
            ? <XCircle className="h-4 w-4 text-destructive" />
            : <Clock className="h-4 w-4 text-muted-foreground" />}
        <span className="text-xs text-muted-foreground capitalize">{status}</span>
      </div>
    </div>
  );
}

export function SystemPage() {
  const [status,    setStatus]    = useState<SystemStatus | null>(null);
  const [healthy,   setHealthy]   = useState<boolean | null>(null);
  const [loading,   setLoading]   = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  async function load() {
    const [h, s] = await Promise.allSettled([api.health(), api.status()]);
    setHealthy(h.status === "fulfilled");
    if (s.status === "fulfilled") setStatus(s.value as SystemStatus);
    setLoading(false);
    setRefreshing(false);
  }

  useEffect(() => { load(); const t = setInterval(load, 15000); return () => clearInterval(t); }, []);

  function refresh() { setRefreshing(true); load(); }

  const runtime = status?.runtime ?? {};
  const agentList = Object.entries(runtime);
  const runningCount = agentList.filter(([,a]) => a.status === "running").length;
  const errorCount   = agentList.filter(([,a]) => a.status === "error").length;

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">System</h1>
          <p className="text-sm text-muted-foreground">Services und Laufzeit-Status · aktualisiert alle 15s</p>
        </div>
        <button onClick={refresh} disabled={refreshing}
          className="flex items-center gap-2 px-3 py-1.5 text-sm border rounded-md hover:bg-accent transition-colors disabled:opacity-50">
          <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? "animate-spin" : ""}`} />
          Aktualisieren
        </button>
      </div>

      {/* Kurzübersicht */}
      <div className="grid grid-cols-3 gap-4">
        <div className="bg-card border rounded-lg p-4 space-y-1">
          <div className="flex items-center gap-2 text-muted-foreground text-xs uppercase tracking-wide font-medium">
            <Activity className="h-3.5 w-3.5" />Core API
          </div>
          <p className={`text-lg font-semibold ${healthy === false ? "text-destructive" : "text-green-500"}`}>
            {loading ? "..." : healthy ? "Online" : "Offline"}
          </p>
        </div>
        <div className="bg-card border rounded-lg p-4 space-y-1">
          <div className="flex items-center gap-2 text-muted-foreground text-xs uppercase tracking-wide font-medium">
            <Cpu className="h-3.5 w-3.5" />Agenten laufend
          </div>
          <p className="text-lg font-semibold">
            <span className="text-green-500">{runningCount}</span>
            <span className="text-muted-foreground text-sm"> / {agentList.length}</span>
            {errorCount > 0 && <span className="text-destructive text-sm ml-2">{errorCount} Fehler</span>}
          </p>
        </div>
        <div className="bg-card border rounded-lg p-4 space-y-1">
          <div className="flex items-center gap-2 text-muted-foreground text-xs uppercase tracking-wide font-medium">
            <HardDrive className="h-3.5 w-3.5" />Aktive Sessions
          </div>
          <p className="text-lg font-semibold">
            {status?.sessions?.active_projects?.length ?? "..."}
          </p>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-6">
        {/* Services */}
        <div className="bg-card border rounded-lg p-4 space-y-1">
          <h2 className="text-sm font-medium mb-3">Systemd-Services</h2>
          <ServiceRow name="octopos-core"      status={healthy ? "ok" : "error"} />
          <ServiceRow name="octopos-conduwuit" status="ok" />
          <ServiceRow name="octopos-console"   status="ok" />
          <ServiceRow name="ollama"            status="ok" />
        </div>

        {/* Verzeichnisse */}
        <div className="bg-card border rounded-lg p-4 space-y-1">
          <h2 className="text-sm font-medium mb-3">Verzeichnisse</h2>
          <div className="space-y-2">
            <div className="flex justify-between text-sm py-2 border-b">
              <span className="text-muted-foreground">Agenten</span>
              <code className="text-xs">{status?.discovery?.agents_dir ?? "/agents"}</code>
            </div>
            <div className="flex justify-between text-sm py-2 border-b">
              <span className="text-muted-foreground">Projekte</span>
              <code className="text-xs">{status?.projects?.projects_dir ?? "/projects"}</code>
            </div>
            <div className="flex justify-between text-sm py-2">
              <span className="text-muted-foreground">Aktive Projekte</span>
              <span className="text-xs">{status?.sessions?.active_projects?.join(", ") || "—"}</span>
            </div>
          </div>
        </div>
      </div>

      {/* Agent Runtime Detail */}
      {agentList.length > 0 && (
        <div className="bg-card border rounded-lg p-4">
          <h2 className="text-sm font-medium mb-4">Agent-Laufzeit</h2>
          <div className="space-y-0">
            {agentList.map(([id, agent]) => {
              const hbWarn = agent.last_heartbeat_age > agent.heartbeat_timeout * 0.8;
              return (
                <div key={id} className="flex items-center gap-3 py-2.5 border-b last:border-0">
                  {STATUS_ICON[agent.status] ?? STATUS_ICON["stopped"]}
                  <div className="flex-1 min-w-0">
                    <span className="text-sm font-medium">{id}</span>
                    <span className="text-xs text-muted-foreground ml-2">{agent.type}</span>
                  </div>
                  <div className="flex items-center gap-4 text-xs text-muted-foreground">
                    <span className={hbWarn ? "text-orange-500" : ""}>
                      HB {agent.last_heartbeat_age.toFixed(0)}s
                    </span>
                    {agent.restart_count > 0 && (
                      <span className="text-orange-500">↺ {agent.restart_count}</span>
                    )}
                    <span className="capitalize">{agent.status}</span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
