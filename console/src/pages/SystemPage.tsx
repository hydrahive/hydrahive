import { useEffect, useState } from "react";
import { RefreshCw, CheckCircle, XCircle, Clock, Cpu, HardDrive, Activity, Zap, Stethoscope, AlertTriangle, FlaskConical, RotateCcw, Trash2 } from "lucide-react";
import { api, GpuInfo, GpuEntry, DoctorReport, DoctorCheck, TestReport, CleanupStatus, CleanupConfig } from "@/lib/api";
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

interface ResourceData {
  system: {
    cpu_percent: number;
    ram_total_mb: number;
    ram_used_mb: number;
    ram_percent: number;
    disk_total_gb: number;
    disk_used_gb: number;
    disk_percent: number;
  };
  process: { cpu_percent: number; ram_mb: number };
  agents: Record<string, { tokens_last_hour: number; running: boolean }>;
  token_warn_threshold: number;
}

function ResourceBar({ pct, label }: { pct: number; label: string }) {
  const color = pct >= 90 ? "bg-red-500" : pct >= 70 ? "bg-orange-500" : "bg-green-500";
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs text-muted-foreground">
        <span>{label}</span><span>{pct.toFixed(0)}%</span>
      </div>
      <div className="h-2 bg-muted rounded-full overflow-hidden">
        <div className={`h-full rounded-full transition-all duration-500 ${color}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
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

function CleanupPanel() {
  const { t } = useTranslation();
  const { isAdmin } = useAuth();
  const [status,   setStatus]   = useState<CleanupStatus | null>(null);
  const [running,  setRunning]  = useState(false);
  const [msg,      setMsg]      = useState<string | null>(null);
  const [cfgEdit,  setCfgEdit]  = useState(false);
  const [cfg,      setCfg]      = useState<CleanupConfig>({ transcript_days: 30, backup_keep: 10, warn_pct_yellow: 80, warn_pct_red: 90 });

  if (!isAdmin) return null;

  useEffect(() => {
    api.cleanupStatus().then(s => {
      setStatus(s);
      setCfg(s.config);
    }).catch(e => console.error("Failed to load cleanup status", e));
  }, []);

  async function runCleanup() {
    setRunning(true);
    setMsg(null);
    try {
      const r = await api.cleanupRun();
      setMsg(`Cleanup abgeschlossen: ${r.deleted_transcripts} Transcripts, ${r.deleted_backups} Backups, ${r.deleted_orphan_projects} Projekte gelöscht.`);
      const s = await api.cleanupStatus();
      setStatus(s);
    } catch (e: unknown) {
      setMsg(e instanceof Error ? e.message : t("common.error"));
    } finally {
      setRunning(false);
    }
  }

  async function saveConfig() {
    try {
      const r = await api.cleanupConfig(cfg);
      setCfg(r.config);
      setCfgEdit(false);
      setMsg("Konfiguration gespeichert.");
    } catch (e: unknown) {
      setMsg(e instanceof Error ? e.message : t("common.error"));
    }
  }

  const disk = status?.disk;
  const diskColor = disk ? (disk.percent >= 90 ? "text-red-500" : disk.percent >= 80 ? "text-orange-500" : "text-green-500") : "";

  return (
    <div className="bg-card border rounded-lg p-4 space-y-4">
      <div className="flex items-center gap-2">
        <Trash2 className="h-4 w-4 text-muted-foreground" />
        <h2 className="text-sm font-medium">Disk-Cleanup</h2>
        {disk && <span className={`ml-auto text-xs font-mono ${diskColor}`}>{disk.percent.toFixed(1)}% belegt ({disk.free_gb} GB frei)</span>}
      </div>

      {disk && (
        <ResourceBar pct={disk.percent} label={`Disk: ${disk.used_gb} GB / ${disk.total_gb} GB`} />
      )}

      {status?.last_result && (
        <div className="text-xs text-muted-foreground space-y-0.5">
          <p>Letzter Lauf: {new Date(status.last_result.ran_at).toLocaleString("de-DE")}</p>
          <p>Transcripts gelöscht: {status.last_result.deleted_transcripts} · Backups: {status.last_result.deleted_backups} · Projekte: {status.last_result.deleted_orphan_projects}</p>
        </div>
      )}

      {!cfgEdit ? (
        <div className="text-xs text-muted-foreground flex flex-wrap gap-4">
          <span>Transcripts: {cfg.transcript_days} Tage</span>
          <span>Backups: letzte {cfg.backup_keep} behalten</span>
          <span>Warnung: {cfg.warn_pct_yellow}% / {cfg.warn_pct_red}%</span>
          <button onClick={() => setCfgEdit(true)} className="ml-auto text-primary hover:underline">Konfigurieren</button>
        </div>
      ) : (
        <div className="space-y-3 border rounded-lg p-3 bg-muted/30">
          <div className="grid grid-cols-2 gap-3 text-xs">
            <label className="space-y-1">
              <span className="text-muted-foreground">Transcript-Alter (Tage)</span>
              <input type="number" min={1} value={cfg.transcript_days}
                onChange={e => setCfg(c => ({ ...c, transcript_days: +e.target.value }))}
                className="w-full rounded border bg-background px-2 py-1" />
            </label>
            <label className="space-y-1">
              <span className="text-muted-foreground">Backups behalten</span>
              <input type="number" min={1} value={cfg.backup_keep}
                onChange={e => setCfg(c => ({ ...c, backup_keep: +e.target.value }))}
                className="w-full rounded border bg-background px-2 py-1" />
            </label>
            <label className="space-y-1">
              <span className="text-muted-foreground">Warnung bei % (gelb)</span>
              <input type="number" min={50} max={99} value={cfg.warn_pct_yellow}
                onChange={e => setCfg(c => ({ ...c, warn_pct_yellow: +e.target.value }))}
                className="w-full rounded border bg-background px-2 py-1" />
            </label>
            <label className="space-y-1">
              <span className="text-muted-foreground">Warnung bei % (rot)</span>
              <input type="number" min={50} max={99} value={cfg.warn_pct_red}
                onChange={e => setCfg(c => ({ ...c, warn_pct_red: +e.target.value }))}
                className="w-full rounded border bg-background px-2 py-1" />
            </label>
          </div>
          <div className="flex gap-2">
            <button onClick={saveConfig} className="rounded bg-primary px-3 py-1.5 text-xs text-primary-foreground">{t("common.save")}</button>
            <button onClick={() => setCfgEdit(false)} className="rounded border px-3 py-1.5 text-xs">{t("common.cancel")}</button>
          </div>
        </div>
      )}

      {msg && <p className="text-xs text-muted-foreground">{msg}</p>}

      <button
        onClick={runCleanup}
        disabled={running}
        className="flex items-center gap-2 rounded-lg border px-3 py-2 text-sm hover:bg-muted disabled:opacity-50"
      >
        <RefreshCw className={`h-4 w-4 ${running ? "animate-spin" : ""}`} />
        {running ? "Läuft..." : "Cleanup jetzt ausführen"}
      </button>
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

function OAuthUsageCard({ data }: { data: Record<string,unknown> | null }) {
  if (!data) return null;
  if (!data.available) return (
    <div className="bg-card border rounded-lg p-4">
      <h2 className="text-sm font-medium flex items-center gap-2 mb-2">
        <Activity className="h-4 w-4 text-muted-foreground" /> Claude OAuth — Nutzungslimits
      </h2>
      <p className="text-xs text-muted-foreground">{String(data.message || "Warte auf ersten Chat mit Claude-Agent...")}</p>
    </div>
  );
  const ou = data;
  const windows = [
    { key: "5h", label: "Session (5h)", icon: "🕐" },
    { key: "7d", label: "Woche (7d)", icon: "📅" },
  ];
  const formatReset = (val: unknown) => {
    if (!val) return "";
    try {
      const d = new Date(Number(val) * 1000);
      if (isNaN(d.getTime())) return "";
      const diff = d.getTime() - Date.now();
      if (diff <= 0) return "jetzt";
      const h = Math.floor(diff / 3600000);
      const m = Math.floor((diff % 3600000) / 60000);
      return h > 0 ? `${h}h ${m}m` : `${m}m`;
    } catch { return ""; }
  };
  const status = String(ou.status || "unknown");
  return (
    <div className="bg-card border rounded-lg p-4">
      <h2 className="text-sm font-medium flex items-center gap-2 mb-3">
        <Activity className="h-4 w-4 text-muted-foreground" /> Claude OAuth — Nutzungslimits
        <span className={`ml-auto text-xs px-2 py-0.5 rounded-full ${
          status === "allowed" ? "bg-green-500/20 text-green-500" :
          status === "allowed_warning" ? "bg-orange-500/20 text-orange-500" :
          "bg-destructive/20 text-destructive"
        }`}>{status}</span>
      </h2>
      <div className="space-y-3">
        {windows.map(w => {
          const d = ou[w.key] as {utilization_pct: number; reset?: string} | undefined;
          if (!d) return null;
          const pct = d.utilization_pct ?? 0;
          const color = pct >= 90 ? "bg-red-500" : pct >= 70 ? "bg-orange-500" : pct >= 40 ? "bg-yellow-500" : "bg-green-500";
          const resetStr = formatReset(d.reset);
          return (
            <div key={w.key} className="space-y-1">
              <div className="flex justify-between text-xs">
                <span>{w.icon} {w.label}</span>
                <span className="text-muted-foreground">
                  {pct}% verwendet
                  {resetStr && <span className="ml-2">· Reset in {resetStr}</span>}
                </span>
              </div>
              <div className="h-2 bg-muted rounded-full overflow-hidden">
                <div className={`h-full rounded-full transition-all ${color}`} style={{ width: `${Math.min(100, pct)}%` }} />
              </div>
            </div>
          );
        })}
        {ou.overage ? (
          <div className="space-y-1 pt-1 border-t">
            <div className="flex justify-between text-xs">
              <span>💳 Zusätzliche Nutzung</span>
              <span className="text-muted-foreground">
                {(ou.overage as {utilization_pct: number}).utilization_pct}%
                {(ou.overage as {status?: string}).status && ` (${(ou.overage as {status: string}).status})`}
              </span>
            </div>
            <div className="h-2 bg-muted rounded-full overflow-hidden">
              <div className="h-full rounded-full transition-all bg-purple-500" style={{ width: `${Math.min(100, (ou.overage as {utilization_pct: number}).utilization_pct)}%` }} />
            </div>
          </div>
        ) : null}
        <div className="text-[10px] text-muted-foreground text-right">
          Aktualisiert: {ou.updated_at ? new Date(String(ou.updated_at)).toLocaleTimeString("de-DE") : "—"}
        </div>
      </div>
    </div>
  );
}

type CodexStatus = { configured: boolean; account_id: string | null; models?: string[]; rate_limits?: Record<string,string> };
type MinimaxModel = {
  name: string; label: string;
  interval_total: number; interval_used: number; interval_pct: number; interval_reset_in_s: number;
  weekly_total: number; weekly_used: number; weekly_pct: number;
};
type MinimaxUsage = { available: boolean; reason?: string; fetched_at?: string; models?: MinimaxModel[] };

function CodexUsageCard({ codex }: { codex: CodexStatus | null }) {
  if (!codex || !codex.configured) return null;
  const rl = codex.rate_limits || {};
  const primary = parseInt(rl["x-codex-primary-used-percent"] ?? "", 10);
  const secondary = parseInt(rl["x-codex-secondary-used-percent"] ?? "", 10);
  const plan = rl["x-codex-plan-type"] || "plus";
  const bars = [
    { key: "5h", label: "Session (5h)", icon: "🕐", pct: isNaN(primary) ? 0 : primary },
    { key: "7d", label: "Woche (7d)",   icon: "📅", pct: isNaN(secondary) ? 0 : secondary },
  ];
  return (
    <div className="bg-card border rounded-lg p-4">
      <h2 className="text-sm font-medium flex items-center gap-2 mb-3">
        <Activity className="h-4 w-4 text-muted-foreground" /> Codex — Nutzungslimits
        <span className="ml-auto text-xs px-2 py-0.5 rounded-full bg-blue-500/20 text-blue-400">{plan}</span>
      </h2>
      <div className="space-y-3">
        {bars.map(b => {
          const color = b.pct >= 90 ? "bg-red-500" : b.pct >= 70 ? "bg-orange-500" : b.pct >= 40 ? "bg-yellow-500" : "bg-green-500";
          return (
            <div key={b.key} className="space-y-1">
              <div className="flex justify-between text-xs">
                <span>{b.icon} {b.label}</span>
                <span className="text-muted-foreground">{b.pct}% verwendet</span>
              </div>
              <div className="h-2 bg-muted rounded-full overflow-hidden">
                <div className={`h-full rounded-full transition-all ${color}`} style={{ width: `${Math.min(100, b.pct)}%` }} />
              </div>
            </div>
          );
        })}
        {codex.models && codex.models.length > 0 ? (
          <div className="text-[10px] text-muted-foreground">
            Modelle: {codex.models.join(", ")}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function MinimaxUsageCard({ usage }: { usage: MinimaxUsage | null }) {
  if (!usage) return null;
  if (!usage.available) return (
    <div className="bg-card border rounded-lg p-4">
      <h2 className="text-sm font-medium flex items-center gap-2 mb-2">
        <Activity className="h-4 w-4 text-muted-foreground" /> MiniMax — Token-Plan
      </h2>
      <p className="text-xs text-muted-foreground">{usage.reason === "no_api_key" ? "Kein API-Key konfiguriert" : `Nicht verfügbar (${usage.reason ?? "unknown"})`}</p>
    </div>
  );
  const models = usage.models ?? [];
  const fmtReset = (s: number) => {
    if (s <= 0) return "jetzt";
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    return h > 0 ? `${h}h ${m}m` : `${m}m`;
  };
  return (
    <div className="bg-card border rounded-lg p-4">
      <h2 className="text-sm font-medium flex items-center gap-2 mb-3">
        <Activity className="h-4 w-4 text-muted-foreground" /> MiniMax — Token-Plan ({models.length} Modelle)
      </h2>
      <div className="space-y-3">
        {models.map((m) => {
          const iColor = m.interval_pct >= 90 ? "bg-red-500" : m.interval_pct >= 70 ? "bg-orange-500" : m.interval_pct >= 40 ? "bg-yellow-500" : "bg-green-500";
          const wColor = m.weekly_pct >= 90 ? "bg-red-500" : m.weekly_pct >= 70 ? "bg-orange-500" : m.weekly_pct >= 40 ? "bg-yellow-500" : "bg-green-500";
          return (
            <div key={m.label} className="space-y-1 pb-2 border-b last:border-b-0">
              <div className="flex items-center justify-between text-xs">
                <span className="font-medium">
                  <span className="text-[10px] uppercase tracking-wider text-muted-foreground mr-2">{m.name}</span>
                  {m.label}
                </span>
                <span className="text-muted-foreground text-[10px]">Reset in {fmtReset(m.interval_reset_in_s)}</span>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-0.5">
                  <div className="flex justify-between text-[10px] text-muted-foreground">
                    <span>Interval</span>
                    <span className="tabular-nums">{m.interval_used}/{m.interval_total} · {m.interval_pct < 10 ? m.interval_pct.toFixed(1) : Math.round(m.interval_pct)}%</span>
                  </div>
                  <div className="h-1.5 bg-muted rounded-full overflow-hidden">
                    <div className={`h-full rounded-full transition-all ${iColor}`} style={{ width: `${Math.min(100, m.interval_pct)}%` }} />
                  </div>
                </div>
                <div className="space-y-0.5">
                  <div className="flex justify-between text-[10px] text-muted-foreground">
                    <span>Weekly</span>
                    <span className="tabular-nums">{m.weekly_used}/{m.weekly_total} · {m.weekly_pct < 10 ? m.weekly_pct.toFixed(1) : Math.round(m.weekly_pct)}%</span>
                  </div>
                  <div className="h-1.5 bg-muted rounded-full overflow-hidden">
                    <div className={`h-full rounded-full transition-all ${wColor}`} style={{ width: `${Math.min(100, m.weekly_pct)}%` }} />
                  </div>
                </div>
              </div>
            </div>
          );
        })}
        {usage.fetched_at ? (
          <div className="text-[10px] text-muted-foreground text-right">
            Aktualisiert: {new Date(usage.fetched_at).toLocaleTimeString("de-DE")}
          </div>
        ) : null}
      </div>
    </div>
  );
}

export function SystemPage() {
  const { t } = useTranslation();
  const [status,    setStatus]    = useState<SystemStatus | null>(null);
  const [healthy,   setHealthy]   = useState<boolean | null>(null);
  const [gpu,       setGpu]       = useState<GpuInfo | null>(null);
  const [resources, setResources] = useState<ResourceData | null>(null);
  const [loading,   setLoading]   = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [restartConfirm, setRestartConfirm] = useState(false);
  const [restarting,     setRestarting]     = useState(false);
  const [oauthUsage, setOauthUsage] = useState<Record<string,unknown> | null>(null);
  const [codex,        setCodex]        = useState<CodexStatus | null>(null);
  const [minimaxUsage, setMinimaxUsage] = useState<MinimaxUsage | null>(null);

  async function load() {
    const [h, s, g, r, ou, cx, mu] = await Promise.allSettled([
      api.health(), api.status(), api.gpuInfo(),
      api.get("/admin/resources"),
      api.oauthUsage(),
      api.openaiCodexStatus(),
      api.minimaxUsage(),
    ]);
    setHealthy(h.status === "fulfilled");
    if (s.status === "fulfilled") setStatus(s.value as SystemStatus);
    if (g.status === "fulfilled") setGpu(g.value);
    if (r.status === "fulfilled") setResources(r.value as ResourceData);
    if (ou.status === "fulfilled") setOauthUsage(ou.value as Record<string,unknown>);
    if (cx.status === "fulfilled") setCodex(cx.value as CodexStatus);
    if (mu.status === "fulfilled") setMinimaxUsage(mu.value as MinimaxUsage);
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

      {/* ── Resource Monitoring ─────────────────────────────────────── */}
      {resources && (
        <div className="grid grid-cols-3 gap-4">
          {/* System-Ressourcen */}
          <div className="bg-card border rounded-lg p-4 space-y-3 col-span-1">
            <h2 className="text-sm font-medium flex items-center gap-2">
              <Cpu className="h-4 w-4 text-muted-foreground" /> System
            </h2>
            <ResourceBar pct={resources.system.cpu_percent}  label="CPU" />
            <ResourceBar pct={resources.system.ram_percent}  label={`RAM  ${resources.system.ram_used_mb} / ${resources.system.ram_total_mb} MB`} />
            <ResourceBar pct={resources.system.disk_percent} label={`Disk  ${resources.system.disk_used_gb} / ${resources.system.disk_total_gb} GB`} />
            <div className="pt-1 border-t text-xs text-muted-foreground space-y-1">
              <div className="flex justify-between"><span>Core CPU</span><span>{resources.process.cpu_percent.toFixed(1)}%</span></div>
              <div className="flex justify-between"><span>Core RAM</span><span>{resources.process.ram_mb} MB</span></div>
            </div>
          </div>

          {/* Token-Verbrauch pro Agent */}
          <div className="bg-card border rounded-lg p-4 col-span-2">
            <h2 className="text-sm font-medium flex items-center gap-2 mb-3">
              <Zap className="h-4 w-4 text-muted-foreground" /> Token-Verbrauch (letzte Stunde)
            </h2>
            {Object.keys(resources.agents).length === 0 ? (
              <p className="text-xs text-muted-foreground">Noch keine Aktivität in der letzten Stunde.</p>
            ) : (
              <div className="space-y-2">
                {Object.entries(resources.agents)
                  .filter(([, a]) => a.tokens_last_hour > 0)
                  .sort(([, a], [, b]) => b.tokens_last_hour - a.tokens_last_hour)
                  .map(([id, a]) => {
                    const pct = Math.min(100, (a.tokens_last_hour / (resources.token_warn_threshold || 100000)) * 100);
                    const color = pct >= 90 ? "bg-red-500" : pct >= 60 ? "bg-orange-500" : "bg-blue-500";
                    return (
                      <div key={id} className="space-y-1">
                        <div className="flex justify-between text-xs">
                          <span className="flex items-center gap-1.5">
                            <span className={`w-1.5 h-1.5 rounded-full ${a.running ? "bg-green-500" : "bg-muted-foreground"}`} />
                            <span className="font-mono text-muted-foreground">{id}</span>
                          </span>
                          <span className="text-muted-foreground">{a.tokens_last_hour.toLocaleString()} Tokens</span>
                        </div>
                        <div className="h-1.5 bg-muted rounded-full overflow-hidden">
                          <div className={`h-full rounded-full transition-all ${color}`} style={{ width: `${pct}%` }} />
                        </div>
                      </div>
                    );
                  })}
              </div>
            )}
          </div>
        </div>
      )}

      {/* OAuth Usage / Rate Limits */}
      <OAuthUsageCard data={oauthUsage} />

      {/* Codex Usage (#805 SystemPage-Parität) */}
      <CodexUsageCard codex={codex} />

      {/* MiniMax Token-Plan (#805 SystemPage-Parität) */}
      <MinimaxUsageCard usage={minimaxUsage} />

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

      <CleanupPanel />
      <DoctorPanel />
      <TestsPanel />
    </div>
  );
}
