import { useEffect, useState } from "react";
import { RefreshCw, CheckCircle, XCircle, Clock, Cpu, HardDrive, Activity, Zap } from "lucide-react";
import { api, GpuInfo, GpuEntry } from "@/lib/api";
import { useTranslation } from "react-i18next";

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

function GpuBar({ value, max = 100, warn = 80, danger = 95 }: { value: number | null; max?: number; warn?: number; danger?: number }) {
  if (value === null) return <span className="text-muted-foreground text-xs">—</span>;
  const pct = Math.min(100, (value / max) * 100);
  const color = pct >= danger ? "bg-destructive" : pct >= warn ? "bg-orange-500" : "bg-green-500";
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden">
        <div className={`h-full rounded-full transition-all ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs text-muted-foreground w-8 text-right">{value}%</span>
    </div>
  );
}

function GpuCard({ gpu }: { gpu: GpuEntry }) {
  const memPct = gpu.mem_total_mb ? Math.round((gpu.mem_used_mb ?? 0) / gpu.mem_total_mb * 100) : null;
  const memGB  = (mb: number | null) => mb !== null ? (mb / 1024).toFixed(1) + " GB" : "—";
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium">{gpu.name}</span>
        {gpu.temp_c !== null && (
          <span className={`text-xs font-mono px-1.5 py-0.5 rounded ${
            gpu.temp_c >= 85 ? "bg-destructive/20 text-destructive" :
            gpu.temp_c >= 70 ? "bg-orange-500/20 text-orange-500" :
            "bg-green-500/20 text-green-500"
          }`}>
            {gpu.temp_c}°C
          </span>
        )}
      </div>
      <div className="space-y-2 text-xs">
        <div className="flex items-center gap-2">
          <span className="text-muted-foreground w-20">GPU</span>
          <div className="flex-1"><GpuBar value={gpu.util_gpu_pct} /></div>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-muted-foreground w-20">VRAM</span>
          <div className="flex-1"><GpuBar value={memPct} /></div>
        </div>
        <div className="flex justify-between text-muted-foreground pt-1 border-t">
          <span>VRAM: {memGB(gpu.mem_used_mb)} / {memGB(gpu.mem_total_mb)}</span>
          {gpu.power_draw_w !== null && (
            <span>{gpu.power_draw_w}W {gpu.power_limit_w ? `/ ${gpu.power_limit_w}W` : ""}</span>
          )}
        </div>
      </div>
    </div>
  );
}

export function SystemPage() {
  const { t } = useTranslation();
  const [status,    setStatus]    = useState<SystemStatus | null>(null);
  const [healthy,   setHealthy]   = useState<boolean | null>(null);
  const [gpu,       setGpu]       = useState<GpuInfo | null>(null);
  const [loading,   setLoading]   = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  async function load() {
    const [h, s, g] = await Promise.allSettled([api.health(), api.status(), api.gpuInfo()]);
    setHealthy(h.status === "fulfilled");
    if (s.status === "fulfilled") setStatus(s.value as SystemStatus);
    if (g.status === "fulfilled") setGpu(g.value);
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
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">{t("system.title")}</h1>
          <p className="text-sm text-muted-foreground">{t("system.subtitle")}</p>
        </div>
        <button onClick={refresh} disabled={refreshing}
          className="flex items-center gap-2 px-3 py-1.5 text-sm border rounded-md hover:bg-accent transition-colors disabled:opacity-50">
          <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? "animate-spin" : ""}`} />
          {t("system.refresh")}
        </button>
      </div>

      <div className="grid grid-cols-3 gap-4">
        <div className="bg-card border rounded-lg p-4 space-y-1">
          <div className="flex items-center gap-2 text-muted-foreground text-xs uppercase tracking-wide font-medium">
            <Activity className="h-3.5 w-3.5" />{t("system.coreApi")}
          </div>
          <p className={`text-lg font-semibold ${healthy === false ? "text-destructive" : "text-green-500"}`}>
            {loading ? "..." : healthy ? t("system.online") : t("system.offline")}
          </p>
        </div>
        <div className="bg-card border rounded-lg p-4 space-y-1">
          <div className="flex items-center gap-2 text-muted-foreground text-xs uppercase tracking-wide font-medium">
            <Cpu className="h-3.5 w-3.5" />{t("system.agentsRunning")}
          </div>
          <p className="text-lg font-semibold">
            <span className="text-green-500">{runningCount}</span>
            <span className="text-muted-foreground text-sm"> / {agentList.length}</span>
            {errorCount > 0 && <span className="text-destructive text-sm ml-2">{errorCount} {t("system.errors")}</span>}
          </p>
        </div>
        <div className="bg-card border rounded-lg p-4 space-y-1">
          <div className="flex items-center gap-2 text-muted-foreground text-xs uppercase tracking-wide font-medium">
            <HardDrive className="h-3.5 w-3.5" />{t("system.activeSessions")}
          </div>
          <p className="text-lg font-semibold">
            {status?.sessions?.active_projects?.length ?? "..."}
          </p>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-6">
        <div className="bg-card border rounded-lg p-4 space-y-1">
          <h2 className="text-sm font-medium mb-3">{t("system.services")}</h2>
          <ServiceRow name="hydrahive-core"      status={healthy ? "ok" : "error"} />
          <ServiceRow name="hydrahive-conduwuit" status="ok" />
          <ServiceRow name="hydrahive-console"   status="ok" />
          <ServiceRow name="ollama"            status="ok" />
        </div>

        <div className="bg-card border rounded-lg p-4 space-y-1">
          <h2 className="text-sm font-medium mb-3">{t("system.directories")}</h2>
          <div className="space-y-2">
            <div className="flex justify-between text-sm py-2 border-b">
              <span className="text-muted-foreground">{t("system.agentsDir")}</span>
              <code className="text-xs">{status?.discovery?.agents_dir ?? "/agents"}</code>
            </div>
            <div className="flex justify-between text-sm py-2 border-b">
              <span className="text-muted-foreground">{t("system.projectsDir")}</span>
              <code className="text-xs">{status?.projects?.projects_dir ?? "/projects"}</code>
            </div>
            <div className="flex justify-between text-sm py-2">
              <span className="text-muted-foreground">{t("system.activeProjects")}</span>
              <span className="text-xs">{status?.sessions?.active_projects?.join(", ") || "—"}</span>
            </div>
          </div>
        </div>
      </div>

      {gpu && gpu.available && gpu.gpus && gpu.gpus.length > 0 && (
        <div className="bg-card border rounded-lg p-4">
          <div className="flex items-center gap-2 mb-4">
            <Zap className="h-4 w-4 text-yellow-500" />
            <h2 className="text-sm font-medium">{t("system.gpu")}</h2>
            <span className="text-xs text-muted-foreground ml-auto">{t("system.gpuRefresh")}</span>
          </div>
          <div className={`grid gap-6 ${gpu.gpus.length > 1 ? "grid-cols-2" : "grid-cols-1"}`}>
            {gpu.gpus.map((g, i) => <GpuCard key={i} gpu={g} />)}
          </div>
        </div>
      )}

      {agentList.length > 0 && (
        <div className="bg-card border rounded-lg p-4">
          <h2 className="text-sm font-medium mb-4">{t("system.agentRuntime")}</h2>
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
