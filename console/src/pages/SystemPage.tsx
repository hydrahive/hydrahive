import { useEffect, useState } from "react";
import { RefreshCw, CheckCircle, XCircle, Clock, Cpu, HardDrive, Activity, Zap, Stethoscope, AlertTriangle, FlaskConical, RotateCcw } from "lucide-react";
import { api, GpuInfo, GpuEntry, DoctorReport, DoctorCheck, TestReport } from "@/lib/api";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/hooks/useAuth";

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

function DoctorStatusIcon({ status }: { status: DoctorCheck["status"] }) {
  if (status === "ok")   return <CheckCircle className="h-4 w-4 text-green-500 flex-shrink-0" />;
  if (status === "warn") return <AlertTriangle className="h-4 w-4 text-yellow-500 flex-shrink-0" />;
  return <XCircle className="h-4 w-4 text-destructive flex-shrink-0" />;
}

function DoctorPanel() {
  const { t } = useTranslation();
  const { isAdmin } = useAuth();
  const [report,   setReport]   = useState<DoctorReport | null>(null);
  const [loading,  setLoading]  = useState(false);
  const [error,    setError]    = useState<string | null>(null);
  const [fixing,   setFixing]   = useState<string | null>(null);
  const [fixMsg,   setFixMsg]   = useState<Record<string, string>>({});

  if (!isAdmin) return null;

  async function runDoctor() {
    setLoading(true);
    setError(null);
    try {
      const r = await api.doctor();
      setReport(r);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  async function applyFix(fixId: string) {
    setFixing(fixId);
    setFixMsg(prev => ({ ...prev, [fixId]: "" }));
    try {
      const r = await api.doctorFix(fixId);
      setFixMsg(prev => ({ ...prev, [fixId]: r.ok ? (r.output || t("doctor.fixOk")) : (r.error || t("doctor.fixFailed")) }));
      if (r.ok) await runDoctor();
    } catch (e: unknown) {
      setFixMsg(prev => ({ ...prev, [fixId]: e instanceof Error ? e.message : t("doctor.fixFailed") }));
    } finally {
      setFixing(null);
    }
  }

  const summaryColor =
    report?.status === "error" ? "text-destructive" :
    report?.status === "warn"  ? "text-yellow-500" :
    report?.status === "ok"    ? "text-green-500" : "";

  return (
    <div className="bg-card border rounded-lg p-4 space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Stethoscope className="h-4 w-4 text-muted-foreground" />
          <h2 className="text-sm font-medium">{t("doctor.title")}</h2>
          {report && (
            <span className={`text-xs font-semibold ${summaryColor}`}>
              — {report.status === "ok" ? t("doctor.allOk") : t("doctor.issuesFound")}
            </span>
          )}
        </div>
        <button
          onClick={runDoctor}
          disabled={loading}
          className="flex items-center gap-2 px-3 py-1.5 text-sm border rounded-md hover:bg-accent transition-colors disabled:opacity-50"
        >
          <Stethoscope className={`h-3.5 w-3.5 ${loading ? "animate-pulse" : ""}`} />
          {loading ? t("doctor.running") : t("doctor.runBtn")}
        </button>
      </div>

      {error && (
        <p className="text-sm text-destructive">{error}</p>
      )}

      {report && (
        <>
          <div className="flex items-center gap-4 text-xs text-muted-foreground border-b pb-3">
            <span>{t("doctor.total")}: <strong>{report.summary.total}</strong></span>
            <span className="text-green-500">{t("doctor.ok")}: <strong>{report.summary.ok}</strong></span>
            <span className="text-yellow-500">{t("doctor.warnings")}: <strong>{report.summary.warn}</strong></span>
            <span className="text-destructive">{t("doctor.errors")}: <strong>{report.summary.error}</strong></span>
          </div>
          <div className="space-y-0">
            {report.checks.map((check, i) => (
              <div key={i} className="flex items-start gap-3 py-2.5 border-b last:border-0">
                <DoctorStatusIcon status={check.status} />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-sm font-medium">{check.name}</span>
                    {check.fix && (
                      <button
                        onClick={() => applyFix(check.fix!)}
                        disabled={fixing === check.fix}
                        className="inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded-md bg-primary/10 text-primary hover:bg-primary/20 disabled:opacity-50 transition-colors"
                      >
                        {fixing === check.fix ? t("doctor.fixing") : t("doctor.fixBtn")}
                      </button>
                    )}
                  </div>
                  <p className="text-xs text-muted-foreground mt-0.5">{check.detail}</p>
                  {check.hint && !check.fix && (
                    <p className="text-xs text-muted-foreground/60 mt-0.5 font-mono">{check.hint}</p>
                  )}
                  {check.fix && fixMsg[check.fix] && (
                    <p className={`text-xs mt-0.5 font-mono ${fixMsg[check.fix].startsWith("nginx") || fixMsg[check.fix] === t("doctor.fixOk") ? "text-green-500" : "text-destructive"}`}>
                      {fixMsg[check.fix]}
                    </p>
                  )}
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function TestsPanel() {
  const { t } = useTranslation();
  const { isAdmin } = useAuth();
  const [report,  setReport]  = useState<TestReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState<string | null>(null);
  const [showOutput, setShowOutput] = useState(false);

  if (!isAdmin) return null;

  async function runTests() {
    setLoading(true);
    setError(null);
    setShowOutput(false);
    try {
      const r = await api.runTests();
      setReport(r);
      if (r.status === "error") setShowOutput(true);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  const allPassed = report?.status === "ok";

  return (
    <div className="bg-card border rounded-lg p-4 space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <FlaskConical className="h-4 w-4 text-muted-foreground" />
          <h2 className="text-sm font-medium">{t("tests.title")}</h2>
          {report && (
            <span className={`text-xs font-semibold ${allPassed ? "text-green-500" : "text-destructive"}`}>
              — {allPassed ? t("tests.allPassed") : t("tests.someFaild")}
            </span>
          )}
        </div>
        <button
          onClick={runTests}
          disabled={loading}
          className="flex items-center gap-2 px-3 py-1.5 text-sm border rounded-md hover:bg-accent transition-colors disabled:opacity-50"
        >
          <FlaskConical className={`h-3.5 w-3.5 ${loading ? "animate-pulse" : ""}`} />
          {loading ? t("tests.running") : t("tests.runBtn")}
        </button>
      </div>

      {error && <p className="text-sm text-destructive">{error}</p>}

      {report && (
        <div className="space-y-3">
          <div className="flex items-center gap-4 text-xs text-muted-foreground">
            <span className="text-green-500">{t("tests.passed")}: <strong>{report.passed}</strong></span>
            {report.failed > 0 && (
              <span className="text-destructive">{t("tests.failed")}: <strong>{report.failed}</strong></span>
            )}
            <span>{t("tests.total")}: <strong>{report.total}</strong></span>
            <span className="ml-auto">{t("tests.duration")}: {report.duration.toFixed(2)}s</span>
          </div>

          <button
            onClick={() => setShowOutput(v => !v)}
            className="text-xs text-muted-foreground hover:text-foreground transition-colors"
          >
            {showOutput ? "▲" : "▼"} {t("tests.output")}
          </button>

          {showOutput && (
            <pre className="text-xs bg-muted rounded p-3 overflow-x-auto whitespace-pre-wrap max-h-80 overflow-y-auto font-mono">
              {report.output}
            </pre>
          )}
        </div>
      )}
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
  const [restartConfirm, setRestartConfirm] = useState(false);
  const [restarting,     setRestarting]     = useState(false);

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

  async function handleRestart() {
    if (!restartConfirm) { setRestartConfirm(true); setTimeout(() => setRestartConfirm(false), 4000); return; }
    setRestarting(true);
    setRestartConfirm(false);
    try { await api.coreRestart(); } catch { /* core stirbt — normal */ }
  }

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
        <div className="flex items-center gap-2">
          <button onClick={handleRestart} disabled={restarting}
            className={`flex items-center gap-2 px-3 py-1.5 text-sm border rounded-md transition-colors disabled:opacity-50 ${
              restartConfirm
                ? "border-red-500 bg-red-500 text-white hover:bg-red-600"
                : "border-red-500 text-red-500 hover:bg-red-50 dark:hover:bg-red-950"
            }`}>
            <RotateCcw className={`h-3.5 w-3.5 ${restarting ? "animate-spin" : ""}`} />
            {restarting ? t("system.restarting") : restartConfirm ? t("system.restartConfirm") : t("system.restartCore")}
          </button>
          <button onClick={refresh} disabled={refreshing}
            className="flex items-center gap-2 px-3 py-1.5 text-sm border rounded-md hover:bg-accent transition-colors disabled:opacity-50">
            <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? "animate-spin" : ""}`} />
            {t("system.refresh")}
          </button>
        </div>
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

      <DoctorPanel />
      <TestsPanel />
    </div>
  );
}
