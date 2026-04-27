import { useEffect, useState, useRef, useCallback } from "react";
import {
  Gauge, RefreshCw, Loader2, XCircle, CheckCircle2,
  Clock, AlertTriangle, Download, X, PlayCircle,
} from "lucide-react";
import { api, type JobMeta, type JobStatus } from "@/lib/api";
import { useTranslation } from "react-i18next";
import { ConfirmDialog } from "@/components/ConfirmDialog";

// ── Helpers ────────────────────────────────────────────────────────────────────
function fmtDate(iso: string | null) {
  if (!iso) return "—";
  try { return new Date(iso).toLocaleString(); }
  catch { return iso; }
}

function fmtBytes(bytes: number) {
  if (bytes < 1024)       return `${bytes} B`;
  if (bytes < 1048576)    return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1073741824) return `${(bytes / 1048576).toFixed(1)} MB`;
  return `${(bytes / 1073741824).toFixed(1)} GB`;
}

// ── StatusBadge ────────────────────────────────────────────────────────────────
function StatusBadge({ status }: { status: JobStatus }) {
  const cfg: Record<JobStatus, { cls: string; label: string; dot: string; icon: React.ElementType }> = {
    queued:    { cls: "bg-muted text-muted-foreground",  label: "Queued",    dot: "bg-gray-400",   icon: Clock },
    running:   { cls: "bg-blue-900/50 text-blue-300",   label: "Running",   dot: "bg-blue-400 animate-pulse", icon: PlayCircle },
    succeeded: { cls: "bg-green-900/40 text-green-400", label: "Done",      dot: "bg-green-500", icon: CheckCircle2 },
    failed:    { cls: "bg-red-900/40 text-red-400",      label: "Failed",    dot: "bg-red-500",   icon: XCircle },
    cancelled: { cls: "bg-muted text-muted-foreground",  label: "Cancelled", dot: "bg-gray-400",   icon: X },
  };
  const c = cfg[status] ?? { cls: "", label: status, dot: "bg-gray-400", icon: Clock };
  const Icon = c.icon;
  return (
    <span className={`status-pill ${c.cls}`}>
      <span className={`w-2 h-2 rounded-full shrink-0 ${c.dot}`} />
      <Icon className="w-3 h-3 mr-0.5" />
      {c.label}
    </span>
  );
}

// ── ProgressBar ────────────────────────────────────────────────────────────────
function ProgressBar({ percent, message }: { percent: number | null; message: string | null }) {
  if (percent === null) return null;
  return (
    <div className="space-y-1">
      <div className="h-1.5 bg-muted rounded-full overflow-hidden">
        <div
          className="h-full bg-blue-500 rounded-full transition-all duration-300"
          style={{ width: `${percent}%` }}
        />
      </div>
      {message && <p className="text-xs text-muted-foreground truncate">{message}</p>}
    </div>
  );
}

// ── Main Component ──────────────────────────────────────────────────────────────

export default function JobsPage() {
  const { t } = useTranslation();
  const [jobs,        setJobs]        = useState<JobMeta[]>([]);
  const [loading,     setLoading]     = useState(true);
  const [error,       setError]       = useState("");
  const [refreshing,   setRefreshing]  = useState(false);

  // Filters (refs for synchronous access in loadJobs)
  const [statusFilter,  setStatusFilter]  = useState<string>("");
  const [typeFilter,    setTypeFilter]    = useState<string>("");
  const [projectFilter, setProjectFilter] = useState<string>("");
  const [projectOptions, setProjectOptions] = useState<{id:string; name:string}[]>([]);

  // Confirm cancel
  const [confirmJob, setConfirmJob] = useState<JobMeta | null>(null);
  const [cancelling, setCancelling] = useState<string | null>(null);

  // Refs for polling + filter access
  const pollingRef    = useRef<ReturnType<typeof setInterval> | null>(null);
  const visibleRef    = useRef(true);
  const statusRef    = useRef(statusFilter);
  const typeRef      = useRef(typeFilter);
  const projectRef   = useRef(projectFilter);

  // Keep refs in sync with state
  statusRef.current  = statusFilter;
  typeRef.current   = typeFilter;
  projectRef.current = projectFilter;

  // Load projects for filter dropdown
  async function loadProjects() {
    try {
      const r = await api.projects() as { projects?: Record<string, { name?: string }> };
      const pmap = r.projects ?? r as Record<string, { name?: string }>;
      setProjectOptions(
        Object.entries(pmap).map(([id, cfg]) => ({
          id, name: cfg?.name ?? id,
        }))
      );
    } catch { /* ignore */ }
  }

  async function loadJobs() {
    try {
      const filters = {
        status:     statusRef.current   || undefined,
        type:       typeRef.current     || undefined,
        project_id: projectRef.current  || undefined,
      };
      const r = await api.jobsList(filters);
      setJobs(r.jobs ?? []);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Load failed");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  function startPolling() {
    if (pollingRef.current) return;
    pollingRef.current = setInterval(() => {
      if (visibleRef.current) loadJobs();
    }, 10_000);
  }

  function stopPolling() {
    if (pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
  }

  // Initial load
  useEffect(() => {
    loadProjects();
    loadJobs();
    startPolling();
    return () => stopPolling();
  }, []);

  // Re-load when filter state changes (useEffect reads latest state via refs)
  useEffect(() => {
    setLoading(true);
    loadJobs();
  }, [statusFilter, typeFilter, projectFilter]);

  // Visibility change — polling only when visible
  useEffect(() => {
    const onVis = () => { visibleRef.current = document.visibilityState === "visible"; };
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, []);

  function handleFilterChange(setter: (v: string) => void, value: string) {
    setter(value);
    // loadJobs() triggered automatically via useEffect on filter state
  }

  async function handleCancel(job: JobMeta) {
    setCancelling(job.job_id);
    try {
      await api.jobsCancel(job.job_id);
      setConfirmJob(null);
      loadJobs();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Cancel failed");
    } finally {
      setCancelling(null);
    }
  }

  return (
    <div className="p-6 max-w-5xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Gauge className="w-6 h-6 text-blue-400" />
          <div>
            <h1 className="text-xl font-semibold">{t("nav.jobs", "Jobs")}</h1>
            <p className="text-xs text-muted-foreground">{t("pageDesc.jobs", "Laufende und abgeschlossene Jobs")}</p>
          </div>
        </div>
        <button
          onClick={() => { setRefreshing(true); loadJobs(); }}
          disabled={refreshing}
          className="flex items-center gap-2 px-3 py-2 text-sm border rounded-md hover:bg-accent transition-colors disabled:opacity-50"
        >
          <RefreshCw className={`w-4 h-4 ${refreshing ? "animate-spin" : ""}`} />
          {t("tools.refresh")}
        </button>
      </div>

      {error && (
        <div className="bg-destructive/10 border border-destructive/30 rounded-lg px-4 py-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {/* Info box */}
      <div className="rounded-xl border bg-muted/30 p-4 space-y-2">
        <h3 className="text-sm font-semibold">{t("jobs.infoTitle", "Jobs Übersicht")}</h3>
        <p className="text-xs text-muted-foreground leading-relaxed">
          {t("jobs.infoText", "Zeigt alle Jobs inkl. Image/Video/Music-Generation. Artifacts können direkt heruntergeladen werden.")}
        </p>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-3">
        <select
          value={statusFilter}
          onChange={e => handleFilterChange(setStatusFilter, e.target.value)}
          className="bg-card border border-border rounded px-3 py-2 text-sm"
        >
          <option value="">— Status —</option>
          <option value="queued">Queued</option>
          <option value="running">Running</option>
          <option value="succeeded">Succeeded</option>
          <option value="failed">Failed</option>
          <option value="cancelled">Cancelled</option>
        </select>
        <select
          value={typeFilter}
          onChange={e => handleFilterChange(setTypeFilter, e.target.value)}
          className="bg-card border border-border rounded px-3 py-2 text-sm"
        >
          <option value="">— Type —</option>
          <option value="noop">noop (Smoke Test)</option>
        </select>
        <select
          value={projectFilter}
          onChange={e => handleFilterChange(setProjectFilter, e.target.value)}
          className="bg-card border border-border rounded px-3 py-2 text-sm"
        >
          <option value="">— Project —</option>
          {projectOptions.map(p => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </select>
      </div>

      {/* Job list */}
      {loading && jobs.length === 0 ? (
        <div className="flex justify-center py-12">
          <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
        </div>
      ) : jobs.length === 0 ? (
        <div className="text-center py-16 text-muted-foreground">
          <Gauge className="w-10 h-10 mx-auto mb-3 opacity-30" />
          <p>{t("jobs.empty", "Noch keine Jobs")}</p>
        </div>
      ) : (
        <div className="space-y-3">
          {jobs.map(job => {
            const isNoop      = job.type === "noop";
            const canCancel   = job.status === "queued" || job.status === "running";
            const hasArtifacts = job.artifacts && job.artifacts.length > 0;

            return (
              <div key={job.job_id} className="bg-card border border-border rounded-xl p-4 space-y-3">
                {/* Header row */}
                <div className="flex items-start gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-mono text-xs bg-muted px-2 py-0.5 rounded">
                        {job.job_id}
                      </span>
                      {isNoop && (
                        <span className="text-xs bg-muted/50 text-muted-foreground px-2 py-0.5 rounded border">
                          Smoke Test
                        </span>
                      )}
                      <StatusBadge status={job.status} />
                      <span className="text-xs text-muted-foreground">
                        {job.provider} · {job.type}
                      </span>
                    </div>
                    <div className="flex gap-4 mt-2 text-xs text-muted-foreground flex-wrap">
                      {job.project_id && (
                        <span>Project: <span className="text-foreground">{job.project_id}</span></span>
                      )}
                      {job.agent_id && (
                        <span>Agent: <span className="text-foreground">{job.agent_id}</span></span>
                      )}
                      <span>Created: <span className="text-foreground">{fmtDate(job.created_at)}</span></span>
                      {job.started_at && (
                        <span>Started: <span className="text-foreground">{fmtDate(job.started_at)}</span></span>
                      )}
                      {job.finished_at && (
                        <span>Finished: <span className="text-foreground">{fmtDate(job.finished_at)}</span></span>
                      )}
                    </div>
                  </div>

                  {/* Cancel button */}
                  {canCancel && !isNoop && (
                    <button
                      onClick={() => setConfirmJob(job)}
                      disabled={cancelling === job.job_id}
                      className="shrink-0 px-3 py-1.5 text-xs rounded border border-red-500/40 text-red-400 hover:bg-red-900/30 disabled:opacity-50"
                    >
                      {cancelling === job.job_id ? "…" : "Cancel"}
                    </button>
                  )}
                </div>

                {/* Progress bar (running) */}
                {job.status === "running" && job.progress_percent !== null && (
                  <ProgressBar percent={job.progress_percent} message={job.progress_message} />
                )}

                {/* Error (failed) */}
                {job.status === "failed" && job.error && (
                  <div className="flex items-start gap-2 text-xs text-red-400 bg-red-950/30 rounded p-2">
                    <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                    <span>{job.error}</span>
                  </div>
                )}

                {/* Artifacts (succeeded/running) */}
                {hasArtifacts && (
                  <div className="flex flex-wrap gap-2">
                    {job.artifacts.map((a, i) => (
                      <a
                        key={i}
                        href={a.download_url ?? "#"}
                        target="_blank"
                        rel="noreferrer"
                        className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded border border-border hover:bg-accent transition-colors"
                      >
                        <Download className="w-3.5 h-3.5" />
                        <span className="truncate max-w-[200px]">{a.filename}</span>
                        <span className="text-muted-foreground">({fmtBytes(a.size)})</span>
                      </a>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      <ConfirmDialog
        open={!!confirmJob}
        title={t("jobs.cancelConfirmTitle", "Job abbrechen?")}
        message={t("jobs.cancelConfirmMsg", "Job '{{id}}' wirklich abbrechen?", { id: confirmJob?.job_id })}
        onConfirm={() => confirmJob && handleCancel(confirmJob)}
        onCancel={() => setConfirmJob(null)}
        variant="danger"
      />
    </div>
  );
}
