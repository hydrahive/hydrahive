import { useCallback, useEffect, useMemo, useState } from "react";
import { Bot, FolderKanban, Activity, Cpu, ArrowRight, ShieldCheck, Radar, Workflow, RefreshCw, Clock3, Layers3, AlertTriangle, Siren, TimerReset } from "lucide-react";
import { api, AuditEntry, GpuInfo, HeartbeatTaskStatus, UpdateStatus } from "@/lib/api";
import { useTranslation } from "react-i18next";

export function DashboardPage() {
  const { t } = useTranslation();
  const [status, setStatus] = useState<Record<string, any> | null>(null);
  const [healthy, setHealthy] = useState<boolean | null>(null);
  const [gpu, setGpu] = useState<GpuInfo | null>(null);
  const [heartbeatTasks, setHeartbeatTasks] = useState<HeartbeatTaskStatus[]>([]);
  const [update, setUpdate] = useState<UpdateStatus | null>(null);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [projectMap, setProjectMap] = useState<Record<string, any>>({});
  const [agentMap, setAgentMap] = useState<Record<string, any>>({});
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);

  const loadDashboard = useCallback(async (silent = false) => {
    let alive = true;
    if (!silent) setIsRefreshing(true);
    Promise.allSettled([
      api.health(),
      api.status(),
      api.gpuInfo(),
      api.heartbeatTasks(),
      api.updateStatus(),
      api.auditLogs({ limit: 5 }),
      api.projects(),
      api.agents(),
    ]).then((results) => {
      if (!alive) return;
      const [healthRes, statusRes, gpuRes, hbRes, updateRes, auditRes, projectsRes, agentsRes] = results;
      setHealthy(healthRes.status === "fulfilled");
      if (statusRes.status === "fulfilled") setStatus(statusRes.value);
      if (gpuRes.status === "fulfilled") setGpu(gpuRes.value);
      if (hbRes.status === "fulfilled") setHeartbeatTasks(hbRes.value.tasks);
      if (updateRes.status === "fulfilled") setUpdate(updateRes.value);
      if (auditRes.status === "fulfilled") setAudit(auditRes.value.logs);
      if (projectsRes.status === "fulfilled") setProjectMap(projectsRes.value as Record<string, any>);
      if (agentsRes.status === "fulfilled") setAgentMap(agentsRes.value as Record<string, any>);
      setLastUpdated(new Date());
      setIsRefreshing(false);
    });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    let disposed = false;
    loadDashboard(false);
    const timer = setInterval(() => {
      if (!disposed) loadDashboard(true);
    }, 15000);
    return () => {
      disposed = true;
      clearInterval(timer);
    };
  }, [loadDashboard]);

  const runtime = status?.runtime as Record<string, any> | undefined;
  const running = runtime ? Object.values(runtime).filter((a: any) => a.status === "running").length : 0;
  const agents = status?.discovery?.count ?? null;
  const projects = status?.projects?.count ?? null;
  const activeProjects = (status?.sessions?.active_projects as string[] | undefined) ?? [];
  const gpuList = gpu?.available && gpu.gpus ? gpu.gpus : [];
  const hottestGpu = gpuList.length > 0 ? [...gpuList].sort((a, b) => (b.temp_c ?? -1) - (a.temp_c ?? -1))[0] : null;
  const updateState = update?.status ?? "unknown";
  const updateAvailable = Boolean(update?.available);
  const runningHeartbeats = heartbeatTasks.length;
  const updateIsUrgent = updateState === "running" || updateState === "error" || updateAvailable;
  const problemAgents = useMemo(() => {
    return Object.entries(agentMap)
      .map(([id, entry]) => {
        if (id.endsWith("_template") || id.endsWith("-template")) return null;
        const runtimeState = entry?.runtime;
        if (!runtimeState) {
          return { id, severity: "warn", summary: t("agents.noRuntime"), detail: "Agent ist konfiguriert, aber aktuell nicht gestartet." };
        }
        if (runtimeState.status !== "running") {
          return { id, severity: "critical", summary: `Runtime ${runtimeState.status}`, detail: "Agent meldet keinen laufenden Zustand." };
        }
        if (runtimeState.restart_count > 0) {
          return { id, severity: "warn", summary: `${runtimeState.restart_count} Restarts`, detail: "Runtime wurde bereits neu gestartet." };
        }
        const heartbeatAge = Number(runtimeState.last_heartbeat_age ?? 0);
        const heartbeatTimeout = Number(runtimeState.heartbeat_timeout ?? 0);
        if (heartbeatTimeout > 0 && heartbeatAge > heartbeatTimeout * 0.75) {
          return { id, severity: "warn", summary: "Heartbeat spaet", detail: `${heartbeatAge.toFixed(0)}s seit letztem Heartbeat.` };
        }
        return null;
      })
      .filter((entry): entry is { id: string; severity: "warn" | "critical"; summary: string; detail: string } => entry !== null)
      .sort((a, b) => (a.severity === b.severity ? a.id.localeCompare(b.id) : a.severity === "critical" ? -1 : 1));
  }, [agentMap, t]);
  const projectSignals = useMemo(() => {
    return activeProjects.map((id) => {
      const entry = projectMap[id];
      if (!entry) {
        return {
          id,
          title: id,
          summary: "Aktive Session ohne Projektdefinition",
          meta: "Der Session-Manager meldet Aktivitaet, aber es gibt kein passendes Projektobjekt.",
          tone: "warn",
        };
      }
      const workerCount = Array.isArray(entry.workers) ? entry.workers.length : 0;
      return {
        id,
        title: entry.name || id,
        summary: `Boss ${entry.boss || "-"}`,
        meta: `${workerCount} Worker · ${entry.matrix_room ? "Matrix aktiv" : "keine Matrix-Room-ID"}`,
        tone: "ok",
      };
    });
  }, [activeProjects, projectMap]);

  const cards = [
    {
      icon: Activity,
      label: t("dashboard.coreLabel", { defaultValue: "Core" }),
      value: healthy === null ? "..." : healthy ? t("dashboard.coreOnline") : t("dashboard.coreOffline"),
      meta: healthy === false ? t("dashboard.coreApiUnstable") : t("dashboard.coreApiOk"),
      state: healthy === false ? "problem" : "ok",
    },
    {
      icon: Bot,
      label: t("dashboard.agentsLabel"),
      value: agents ?? "...",
      meta: t("dashboard.agentsNote"),
      state: "ok",
    },
    {
      icon: FolderKanban,
      label: t("dashboard.projectsLabel"),
      value: projects ?? "...",
      meta: t("dashboard.projectsNote"),
      state: "ok",
    },
    {
      icon: Cpu,
      label: t("dashboard.runtimeLabel"),
      value: running,
      meta: t("dashboard.runtimeNote"),
      state: "ok",
    },
  ];

  const healthTone = healthy === false ? "bg-destructive/12 text-destructive" : "status-pill-ok";
  const systemFacts = useMemo(
    () => [
      { label: "Discovery", value: agents ?? "...", note: t("dashboard.discoveryNote") },
      { label: "Projects", value: projects ?? "...", note: t("dashboard.projectsActive") },
      { label: "Runtime", value: running, note: t("dashboard.runtimeRunning") },
      { label: "Heartbeats", value: runningHeartbeats, note: t("dashboard.heartbeatNote") },
      { label: "Sessions", value: activeProjects.length, note: t("dashboard.sessionsNote") },
    ],
    [activeProjects.length, agents, projects, running, runningHeartbeats, t],
  );
  const attentionItems = useMemo(() => {
    const items: { tone: "critical" | "warn" | "info"; title: string; detail: string }[] = [];
    if (healthy === false) {
      items.push({ tone: "critical", title: t("dashboard.coreDisturbed2"), detail: t("dashboard.coreDisturbed2Detail") });
    }
    if (updateState === "error") {
      items.push({ tone: "critical", title: t("dashboard.updateError"), detail: update?.error || "Der letzte Update-Lauf hat einen Fehler gemeldet." });
    } else if (updateState === "running") {
      items.push({ tone: "info", title: t("dashboard.updateRunning2"), detail: t("dashboard.updateRunning2Detail") });
    } else if (updateAvailable) {
      items.push({
        tone: "warn",
        title: t("dashboard.updateAlertTitle"),
        detail: t("dashboard.updateAlertAvailable", { commit: update?.commit ?? t("dashboard.commitUnknown") }),
      });
    }
    if (problemAgents.length > 0) {
      const critical = problemAgents.filter((entry) => entry.severity === "critical").length;
      items.push({
        tone: critical > 0 ? "critical" : "warn",
        title: problemAgents.length !== 1
          ? t("dashboard.agentSignalCountPlural", { count: problemAgents.length })
          : t("dashboard.agentSignalCount", { count: problemAgents.length }),
        detail: critical > 0
          ? t("dashboard.criticalAgents", { critical })
          : t("dashboard.heartbeatWarn"),
      });
    }
    if (hottestGpu && (hottestGpu.temp_c ?? 0) >= 80) {
      items.push({
        tone: "warn",
        title: t("dashboard.gpuHot", { temp: hottestGpu.temp_c ?? "-" }),
        detail: `${hottestGpu.name} liegt über dem normalen Temperaturfenster.`,
      });
    }
    if (items.length === 0) {
      items.push({ tone: "info", title: t("dashboard.noIssues"), detail: t("dashboard.noIssuesDetail") });
    }
    return items.slice(0, 4);
  }, [healthy, hottestGpu, problemAgents, update?.commit, update?.error, updateAvailable, updateState, t]);
  const attentionTone = attentionItems.some((item) => item.tone === "critical")
    ? "bg-destructive/12 text-destructive"
    : attentionItems.some((item) => item.tone === "warn")
      ? "bg-accent/15 text-accent"
      : "status-pill-ok";

  return (
    <div className="space-y-6">
      <section className="hero-panel">
        <div className="relative z-10 shell-grid">
          <div className="space-y-5 lg:col-span-8">
            <div className="flex flex-wrap items-center gap-3">
              <span className={healthTone + " status-pill"}>
                <span className={"dot " + (healthy === false ? "bg-destructive" : "bg-primary")} />
                {healthy === false ? t("dashboard.coreDisturbed") : t("dashboard.coreReachable")}
              </span>
              <span className="status-pill">
                <Radar className="h-3.5 w-3.5" />
                {t("dashboard.dashboardLabel")}
              </span>
            </div>

            <div>
              <h1 className="shell-title">{t("dashboard.title")}</h1>
              <p className="shell-copy mt-3 max-w-2xl">
                {t("dashboard.subtitle")}
              </p>
            </div>
          </div>

          <div className="lg:col-span-4">
            <div className="app-panel app-panel-muted p-5">
              <div className="flex items-center gap-2 text-sm font-medium">
                <Workflow className="h-4 w-4 text-primary" />
                {t("dashboard.focusToday")}
              </div>
              <div className="mt-4 space-y-3 text-sm text-muted-foreground">
                <div className="flex items-start justify-between gap-3">
                  <span>{t("dashboard.focus1")}</span>
                  <ArrowRight className="mt-0.5 h-4 w-4 flex-shrink-0 text-accent" />
                </div>
                <div className="flex items-start justify-between gap-3">
                  <span>{t("dashboard.focus2")}</span>
                  <ArrowRight className="mt-0.5 h-4 w-4 flex-shrink-0 text-accent" />
                </div>
                <div className="flex items-start justify-between gap-3">
                  <span>{t("dashboard.focus3")}</span>
                  <ArrowRight className="mt-0.5 h-4 w-4 flex-shrink-0 text-accent" />
                </div>
              </div>
              <div className="mt-4 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                <span className={`status-pill ${attentionTone}`}>
                  <Radar className="h-3.5 w-3.5" />
                  {attentionItems[0]?.title}
                </span>
                <span className="status-pill">
                  <TimerReset className="h-3.5 w-3.5" />
                  {lastUpdated ? t("dashboard.updateSync", { time: lastUpdated.toLocaleTimeString("de-DE") }) : t("dashboard.firstSync")}
                </span>
                {isRefreshing && (
                  <span className="status-pill">
                    <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                    {t("dashboard.liveRefresh")}
                  </span>
                )}
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="grid gap-4 xl:grid-cols-[1.2fr_1fr]">
        <div className="section-card">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="metric-kicker">{t("dashboard.attention")}</p>
              <h2 className="mt-2 text-xl font-semibold tracking-tight">{t("dashboard.attentionTitle")}</h2>
            </div>
            <span className={`status-pill ${attentionTone}`}>
              <Siren className="h-3.5 w-3.5" />
              {attentionItems.some((item) => item.tone === "critical")
                ? t("dashboard.critical")
                : attentionItems.some((item) => item.tone === "warn")
                  ? t("dashboard.warn")
                  : t("dashboard.stable")}
            </span>
          </div>
          <div className="mt-5 grid gap-3 md:grid-cols-2">
            {attentionItems.map((item) => (
              <div key={`${item.title}-${item.detail}`} className={`rounded-2xl border px-4 py-4 ${item.tone === "critical" ? "border-destructive/20 bg-destructive/5" : item.tone === "warn" ? "border-accent/20 bg-accent/5" : "bg-background/55"}`}>
                <div className="flex items-start gap-3">
                  <div className={`rounded-2xl p-2 ${item.tone === "critical" ? "bg-destructive/10 text-destructive" : item.tone === "warn" ? "bg-accent/15 text-accent" : "bg-primary/10 text-primary"}`}>
                    {item.tone === "critical" ? <AlertTriangle className="h-4 w-4" /> : item.tone === "warn" ? <Siren className="h-4 w-4" /> : <ShieldCheck className="h-4 w-4" />}
                  </div>
                  <div>
                    <p className="text-sm font-medium">{item.title}</p>
                    <p className="mt-1 text-xs text-muted-foreground">{item.detail}</p>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="section-card">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="metric-kicker">{t("dashboard.liveLabel")}</p>
              <h2 className="mt-2 text-lg font-semibold tracking-tight">{t("dashboard.realtimeState")}</h2>
            </div>
            <span className="status-pill status-pill-ok">15s</span>
          </div>
          <div className="mt-4 space-y-3 text-sm text-muted-foreground">
            <div className="rounded-2xl bg-secondary/55 px-4 py-3">
              <div className="flex items-center justify-between gap-3">
                <span>{t("dashboard.polling")}</span>
                <span className="status-pill">{isRefreshing ? t("layout.running") : t("layout.ready")}</span>
              </div>
            </div>
            <div className="rounded-2xl bg-secondary/55 px-4 py-3">
              <div className="text-xs uppercase tracking-[0.18em] text-muted-foreground">{t("dashboard.lastSync")}</div>
              <div className="mt-2 font-medium text-foreground">{lastUpdated ? lastUpdated.toLocaleTimeString("de-DE") : t("dashboard.noSync")}</div>
            </div>
            <div className="rounded-2xl bg-secondary/55 px-4 py-3">
              <div className="text-xs uppercase tracking-[0.18em] text-muted-foreground">{t("dashboard.activeObservers")}</div>
              <div className="mt-2 text-foreground">{runningHeartbeats} Heartbeats · {activeProjects.length} Sessions · {problemAgents.length} Signale</div>
            </div>
          </div>
        </div>
      </section>

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {cards.map(({ icon: Icon, label, value, meta, state }) => (
          <div key={label} className="metric-card">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="metric-kicker">{label}</p>
                <p className={"metric-value " + (state === "problem" ? "text-destructive" : "")}>{String(value)}</p>
              </div>
              <div className="rounded-2xl bg-secondary p-3 text-secondary-foreground">
                <Icon className="h-5 w-5" />
              </div>
            </div>
            <p className="metric-meta">{meta}</p>
          </div>
        ))}
      </section>

      <section className="grid gap-4 xl:grid-cols-[1.4fr_1fr]">
        <div className="section-card">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="metric-kicker">{t("dashboard.overviewKicker")}</p>
              <h2 className="mt-2 text-xl font-semibold tracking-tight">{t("dashboard.overviewTitle")}</h2>
            </div>
            <span className="status-pill status-pill-ok">{t("dashboard.overview")}</span>
          </div>
          <div className="mt-5 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            {systemFacts.map((item) => (
              <div key={item.label} className="rounded-2xl border bg-background/55 p-4">
                <p className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">{item.label}</p>
                <p className="mt-3 text-2xl font-semibold tracking-tight">{String(item.value)}</p>
                <p className="mt-2 text-sm text-muted-foreground">{item.note}</p>
              </div>
            ))}
          </div>
        </div>

          <div className={`section-card ${updateIsUrgent ? "border-red-400/30 bg-gradient-to-br from-red-500/15 via-red-500/10 to-rose-500/10 shadow-[0_0_0_1px_rgba(248,113,113,0.12),0_18px_40px_rgba(239,68,68,0.14)] backdrop-blur" : ""}`}>
          <div className="flex items-center gap-2">
            {updateIsUrgent ? <Siren className="h-4 w-4 text-red-400" /> : <RefreshCw className="h-4 w-4 text-primary" />}
            <h2 className={`text-lg font-semibold tracking-tight ${updateIsUrgent ? "text-red-100" : ""}`}>
              {updateIsUrgent ? t("dashboard.updateAlertTitle") : t("dashboard.updateStatus")}
            </h2>
          </div>
          {updateIsUrgent && (
            <p className="mt-2 text-sm text-red-100/80">
              {updateState === "running"
                ? t("dashboard.updateAlertRunning")
                : updateAvailable
                  ? t("dashboard.updateAlertAvailable", { commit: update?.commit ?? t("dashboard.commitUnknown") })
                  : t("dashboard.updateAlertError")}
            </p>
          )}
          <div className="mt-4 space-y-3 text-sm text-muted-foreground">
            <div className={`rounded-2xl px-4 py-3 ${updateIsUrgent ? "bg-red-500/10" : "bg-secondary/55"}`}>
              <div className="flex items-center justify-between gap-3">
                <span>Status</span>
                <span className={updateState === "ok" && !updateAvailable ? "status-pill status-pill-ok" : "status-pill bg-red-500/20 text-red-100"}>
                  {updateState === "running"
                    ? t("layout.running")
                    : updateAvailable
                      ? t("layout.updateAvailable")
                      : updateState === "error"
                        ? t("dashboard.updateError")
                        : updateState}
                </span>
              </div>
            </div>
            <div className={`rounded-2xl px-4 py-3 ${updateIsUrgent ? "bg-red-500/10" : "bg-secondary/55"}`}>
              <div className="text-xs uppercase tracking-[0.18em] text-muted-foreground">{t("dashboard.commit")}</div>
              <div className="mt-2 font-mono text-foreground">{update?.commit ?? t("dashboard.commitUnknown")}</div>
            </div>
            {update?.error && <div className="rounded-2xl border border-red-400/25 bg-red-500/10 px-4 py-3 text-red-100">{update.error}</div>}
          </div>
        </div>
      </section>

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-[1.15fr_1fr_1fr]">
        <div className="section-card">
          <div className="flex items-center gap-2">
            <Cpu className="h-4 w-4 text-primary" />
            <h2 className="text-lg font-semibold tracking-tight">{t("dashboard.gpuSignal")}</h2>
          </div>
          <div className="mt-4 space-y-3 text-sm text-muted-foreground">
            {!gpu?.available || gpuList.length === 0 ? (
              <div className="rounded-2xl bg-secondary/55 px-4 py-3">{t("dashboard.noGpu")}</div>
            ) : (
              gpuList.slice(0, 2).map((entry) => (
                <div key={entry.name} className="rounded-2xl bg-secondary/55 px-4 py-3">
                  <div className="flex items-center justify-between gap-3">
                    <span className="font-medium text-foreground">{entry.name}</span>
                    <span className="status-pill">{entry.temp_c ?? "-"}°C</span>
                  </div>
                  <div className="mt-3 grid grid-cols-2 gap-3 text-xs">
                    <div>GPU: {entry.util_gpu_pct ?? "-"}%</div>
                    <div>VRAM: {entry.util_mem_pct ?? "-"}%</div>
                    <div>Power: {entry.power_draw_w ?? "-"}W</div>
                    <div>Used: {entry.mem_used_mb ?? "-"} MB</div>
                  </div>
                </div>
              ))
            )}
            {hottestGpu && <div className="text-xs text-muted-foreground">{t("dashboard.hottestGpu", { name: hottestGpu.name, temp: hottestGpu.temp_c ?? "-" })}</div>}
          </div>
        </div>

        <div className="section-card">
          <div className="flex items-center gap-2">
            <Clock3 className="h-4 w-4 text-primary" />
            <h2 className="text-lg font-semibold tracking-tight">{t("dashboard.heartbeatTasks")}</h2>
          </div>
          <div className="mt-4 space-y-3">
            {heartbeatTasks.length === 0 ? (
              <div className="rounded-2xl bg-secondary/55 px-4 py-3 text-sm text-muted-foreground">{t("dashboard.noHeartbeat")}</div>
            ) : (
              heartbeatTasks.slice(0, 4).map((task) => (
                <div key={task.task_id} className="rounded-2xl bg-secondary/55 px-4 py-3 text-sm">
                  <div className="flex items-center justify-between gap-3">
                    <span className="font-medium text-foreground">{task.agent_id}</span>
                    <span className="status-pill">{task.interval ? `${task.interval}s` : task.schedule ?? t("dashboard.manual")}</span>
                  </div>
                  <div className="mt-2 text-xs text-muted-foreground">{task.message}</div>
                </div>
              ))
            )}
          </div>
        </div>

        <div className="section-card md:col-span-2 xl:col-span-1">
          <div className="flex items-center gap-2">
            <Layers3 className="h-4 w-4 text-primary" />
            <h2 className="text-lg font-semibold tracking-tight">{t("dashboard.lastAudit")}</h2>
          </div>
          <div className="mt-4 space-y-3">
            {audit.length === 0 ? (
              <div className="rounded-2xl bg-secondary/55 px-4 py-3 text-sm text-muted-foreground">{t("dashboard.noAudit")}</div>
            ) : (
              audit.map((entry) => (
                <div key={entry.id} className="rounded-2xl bg-secondary/55 px-4 py-3 text-sm">
                  <div className="flex items-center justify-between gap-3">
                    <span className="font-medium text-foreground">{entry.action}</span>
                    <span className="text-xs text-muted-foreground">{new Date(entry.timestamp).toLocaleTimeString("de-DE")}</span>
                  </div>
                  <div className="mt-2 text-xs text-muted-foreground">{entry.user} to {entry.target}</div>
                </div>
              ))
            )}
          </div>
        </div>
      </section>

      <section className="grid gap-4 xl:grid-cols-[1.4fr_1fr]">
        <div className="section-card">
          <div className="flex items-center gap-2">
            <ShieldCheck className="h-4 w-4 text-primary" />
            <h2 className="text-lg font-semibold tracking-tight">{t("dashboard.agentSignals")}</h2>
          </div>
          <div className="mt-4 space-y-3">
            {problemAgents.length === 0 ? (
              <div className="rounded-2xl bg-secondary/55 px-4 py-3 text-sm text-muted-foreground">
                {t("dashboard.noAgentSignals")}
              </div>
            ) : (
              problemAgents.slice(0, 5).map((agent) => (
                <div key={agent.id} className="rounded-2xl bg-secondary/55 px-4 py-3">
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                    <div>
                      <div className="text-sm font-medium text-foreground">{agent.id}</div>
                      <div className="mt-1 text-xs text-muted-foreground">{agent.detail}</div>
                    </div>
                    <span className={agent.severity === "critical" ? "status-pill bg-destructive/12 text-destructive" : "status-pill bg-accent/15 text-accent"}>
                      {agent.summary}
                    </span>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>

        <div className="section-card">
          <div className="flex items-center gap-2">
            <Workflow className="h-4 w-4 text-primary" />
            <h2 className="text-lg font-semibold tracking-tight">{t("dashboard.activeProjects")}</h2>
          </div>
          <div className="mt-4 space-y-3 text-sm text-muted-foreground">
            {projectSignals.length === 0 ? (
              <div className="rounded-2xl bg-secondary/55 px-4 py-3">{t("dashboard.noActiveSessions")}</div>
            ) : (
              projectSignals.slice(0, 5).map((project) => (
                <div key={project.id} className="rounded-2xl bg-secondary/55 px-4 py-3">
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                    <span className="font-medium text-foreground">{project.title}</span>
                    <span className={project.tone === "warn" ? "status-pill bg-accent/15 text-accent" : "status-pill status-pill-ok"}>
                      {t("dashboard.active")}
                    </span>
                  </div>
                  <div className="mt-2 text-xs text-muted-foreground">{project.summary}</div>
                  <div className="mt-1 text-xs text-muted-foreground">{project.meta}</div>
                </div>
              ))
            )}
          </div>
        </div>
      </section>
    </div>
  );
}
