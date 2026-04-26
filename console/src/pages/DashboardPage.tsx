import {
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";
import {
  Bot,
  FolderKanban,
  Activity,
  Cpu,
  Radar,
  RefreshCw,
  TimerReset,
  AlertTriangle,
  Brain,
  Zap,
  ShieldCheck,
  LayoutDashboard,
  BarChart2,
  FileText,
  Link2,
  MonitorPlay,
  Globe,
  Plus,
  Settings,
} from "lucide-react";
import { api, AuditEntry, GpuInfo, HeartbeatTaskStatus, UpdateStatus } from "@/lib/api";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/hooks/useAuth";
import { useNavigate } from "react-router-dom";
import { cn } from "@/lib/utils";
import {
  DashboardGrid,
  EditBar,
  SettingsDrawer,
  useWidgetDashboard,
  AgentMetricWidget,
  ProjectMetricWidget,
  RuntimeMetricWidget,
  GPUMetricWidget,
  ActivityStreamWidget,
  AttentionWidget,
  QuickActionsWidget,
  ContextMetricsWidget,
  OAuthWidget,
  CodexWidget,
  MiniMaxWidget,
  type WidgetComponent,
  type AttentionItem,
} from "@/components/widgets";

type CodexStatus = {
  configured: boolean;
  account_id: string | null;
  models?: string[];
  rate_limits?: Record<string, string>;
};
type MinimaxModel = {
  name: string;
  label: string;
  interval_total: number;
  interval_used: number;
  interval_pct: number;
  interval_reset_in_s: number;
  weekly_total: number;
  weekly_used: number;
  weekly_pct: number;
};
type MinimaxUsage = {
  available: boolean;
  reason?: string;
  fetched_at?: string;
  models?: MinimaxModel[];
};

const WIDGET_COMPONENTS: WidgetComponent[] = [
  { id: "agent-metric", component: AgentMetricWidget as WidgetComponent["component"] },
  { id: "project-metric", component: ProjectMetricWidget as WidgetComponent["component"] },
  { id: "runtime-metric", component: RuntimeMetricWidget as WidgetComponent["component"] },
  { id: "gpu-metric", component: GPUMetricWidget as WidgetComponent["component"] },
  { id: "attention", component: AttentionWidget as WidgetComponent["component"] },
  { id: "quick-actions", component: QuickActionsWidget as WidgetComponent["component"] },
  { id: "activity-stream", component: ActivityStreamWidget as WidgetComponent["component"] },
  { id: "context-metrics", component: ContextMetricsWidget as WidgetComponent["component"] },
  { id: "oauth", component: OAuthWidget as WidgetComponent["component"] },
  { id: "codex", component: CodexWidget as WidgetComponent["component"] },
  { id: "minimax", component: MiniMaxWidget as WidgetComponent["component"] },
];

const WIDGET_CONFIGS: Record<string, { span?: 1 | 2 | 3 | 4; rowSpan?: 1 | 2 }> = {
  "agent-metric":     { span: 1 },
  "project-metric":   { span: 1 },
  "runtime-metric":   { span: 1 },
  "gpu-metric":       { span: 1 },
  "attention":        { span: 2 },
  "quick-actions":    { span: 1, rowSpan: 2 },
  "activity-stream":  { span: 2, rowSpan: 2 },
  "context-metrics":  { span: 2, rowSpan: 2 },
  "oauth":            { span: 1 },
  "codex":            { span: 2 },
  "minimax":          { span: 3, rowSpan: 2 },
};

const WIDGET_LABELS: Record<string, string> = {
  "agent-metric":    "Agents",
  "project-metric":  "Projects",
  "runtime-metric":  "Runtime",
  "gpu-metric":      "GPU / Heartbeats",
  "attention":       "Attention",
  "quick-actions":   "Quick Actions",
  "activity-stream": "Activity",
  "context-metrics": "Context-Metriken",
  "oauth":           "Claude OAuth",
  "codex":           "Codex",
  "minimax":         "MiniMax",
};

export function DashboardOverview() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const navigate = useNavigate();

  const {
    widgets,
    isEditing,
    showSettings,
    toggleWidget,
    reorderWidgets,
    startEdit,
    cancelEdit,
    saveEdit,
    openSettings,
    closeSettings,
  } = useWidgetDashboard();

  // ── State ────────────────────────────────────────────────────────
  const [status, setStatus] = useState<Record<string, any> | null>(null);
  const [healthy, setHealthy] = useState<boolean | null>(null);
  const [gpu, setGpu] = useState<GpuInfo | null>(null);
  const [heartbeatTasks, setHeartbeatTasks] = useState<HeartbeatTaskStatus[]>([]);
  const [update, setUpdate] = useState<UpdateStatus | null>(null);
  const [oauthUsage, setOauthUsage] = useState<Record<string, unknown> | null>(null);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [projectMap, setProjectMap] = useState<Record<string, any>>({});
  const [agentMap, setAgentMap] = useState<Record<string, any>>({});
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [sessionMetrics, setSessionMetrics] = useState<Record<string, any>>({});
  const [codex, setCodex] = useState<CodexStatus | null>(null);
  const [minimax, setMinimax] = useState<MinimaxUsage | null>(null);

  // ── Data Loading ─────────────────────────────────────────────────
  const loadDashboard = useCallback(async (silent = false) => {
    Promise.allSettled([
      api.health(),
      api.status(),
      api.gpuInfo(),
      api.heartbeatTasks(),
      api.updateStatus(),
      api.auditLogs({ limit: 5 }),
      api.projects(),
      api.agents(),
      api.oauthUsage(),
      api.sessionMetrics(),
      api.openaiCodexStatus(),
      api.minimaxUsage(),
    ]).then((results) => {
      const [h, s, g, hb, u, a, p, ag, o, sm, cx, mm] = results;
      setHealthy(h.status === "fulfilled");
      if (s.status === "fulfilled") setStatus(s.value);
      if (g.status === "fulfilled") setGpu(g.value);
      if (hb.status === "fulfilled") setHeartbeatTasks(hb.value.tasks);
      if (u.status === "fulfilled") setUpdate(u.value);
      if (a.status === "fulfilled") setAudit(a.value.logs);
      if (p.status === "fulfilled") setProjectMap(p.value as Record<string, any>);
      if (ag.status === "fulfilled") setAgentMap(ag.value as Record<string, any>);
      if (o.status === "fulfilled") setOauthUsage(o.value as Record<string, unknown>);
      if (sm.status === "fulfilled") setSessionMetrics(sm.value as Record<string, any>);
      if (cx.status === "fulfilled") setCodex(cx.value as CodexStatus);
      if (mm.status === "fulfilled") setMinimax(mm.value as MinimaxUsage);
      setLastUpdated(new Date());
    });
  }, []);

  useEffect(() => {
    loadDashboard(false);
    const t2 = setInterval(() => loadDashboard(true), 15000);
    return () => clearInterval(t2);
  }, [loadDashboard]);

  useEffect(() => {
    let disposed = false;
    const poll = () => {
      Promise.allSettled([api.oauthUsage(), api.openaiCodexStatus(), api.minimaxUsage()]).then(
        ([o, cx, mm]) => {
          if (disposed) return;
          if (o.status === "fulfilled") setOauthUsage(o.value as Record<string, unknown>);
          if (cx.status === "fulfilled") setCodex(cx.value as CodexStatus);
          if (mm.status === "fulfilled") setMinimax(mm.value as MinimaxUsage);
        }
      );
    };
    const interval = setInterval(poll, 3000);
    return () => { disposed = true; clearInterval(interval); };
  }, []);

  // ── Derived Data ────────────────────────────────────────────────
  const runtime = status?.runtime as Record<string, any> | undefined;
  const running = runtime
    ? Object.values(runtime).filter((a: any) => a.status === "running").length
    : 0;
  const agents = status?.discovery?.count ?? null;
  const projects = status?.projects?.count ?? null;
  const activeProjects =
    (status?.sessions?.active_projects as string[] | undefined) ?? [];
  const gpuList = gpu?.available && gpu.gpus ? gpu.gpus : [];
  const hottestGpu =
    gpuList.length > 0
      ? [...gpuList].sort((a, b) => (b.temp_c ?? -1) - (a.temp_c ?? -1))[0]
      : null;
  const updateState = update?.status ?? "unknown";
  const updateAvailable = Boolean(update?.available);
  const runningHeartbeats = heartbeatTasks.length;

  const problemAgents = useMemo(() => {
    return Object.entries(agentMap)
      .map(([id, entry]) => {
        if (id.endsWith("_template") || id.endsWith("-template")) return null;
        const rs = entry?.runtime;
        if (!rs) return { id, severity: "warn" as const, summary: t("agents.noRuntime"), detail: t("dashboard.agentConfiguredNotStarted") };
        if (rs.status !== "running") return { id, severity: "critical" as const, summary: `Runtime ${rs.status}`, detail: t("dashboard.agentNotRunning") };
        if (rs.restart_count > 0) return { id, severity: "warn" as const, summary: `${rs.restart_count} Restarts`, detail: t("dashboard.agentRestarted") };
        const age = Number(rs.last_heartbeat_age ?? 0);
        const timeout = Number(rs.heartbeat_timeout ?? 0);
        if (timeout > 0 && age > timeout * 0.75) return { id, severity: "warn" as const, summary: t("dashboard.heartbeatLate"), detail: t("dashboard.heartbeatLateDetail", { age: age.toFixed(0) }) };
        return null;
      })
      .filter(
        (e): e is { id: string; severity: "warn" | "critical"; summary: string; detail: string } =>
          e !== null
      )
      .sort((a, b) =>
        a.severity === b.severity ? a.id.localeCompare(b.id) : a.severity === "critical" ? -1 : 1
      );
  }, [agentMap, t]);

  const projectSignals = useMemo(() => {
    return activeProjects.map((id) => {
      const entry = projectMap[id];
      if (!entry) return { id, title: id, summary: t("dashboard.sessionNoProject"), meta: t("dashboard.sessionNoProjectDetail"), tone: "warn" as const } as const;
      const workerCount = Array.isArray(entry.workers) ? entry.workers.length : 0;
      return {
        id,
        title: entry.name || id,
        summary: `Boss ${entry.boss || "-"}`,
        meta: `${workerCount} Worker · ${entry.matrix_room ? t("dashboard.matrixActive") : t("dashboard.noMatrixRoom")}`,
        tone: "ok" as const,
      };
    });
  }, [activeProjects, projectMap]);

  const attentionItems = useMemo((): AttentionItem[] => {
    const items: AttentionItem[] = [];
    if (healthy === false) items.push({ tone: "critical", title: t("dashboard.coreDisturbed2"), detail: t("dashboard.coreDisturbed2Detail") });
    if (updateState === "error") items.push({ tone: "critical", title: t("dashboard.updateError"), detail: update?.error || t("dashboard.updateErrorFallback") });
    else if (updateState === "running") items.push({ tone: "info", title: t("dashboard.updateRunning2"), detail: t("dashboard.updateRunning2Detail") });
    else if (updateAvailable) items.push({ tone: "warn", title: t("dashboard.updateAlertTitle"), detail: t("dashboard.updateAlertAvailable", { commit: update?.commit ?? t("dashboard.commitUnknown") }) });
    if (problemAgents.length > 0) {
      const critical = problemAgents.filter((e) => e.severity === "critical").length;
      items.push({
        tone: critical > 0 ? "critical" : "warn",
        title: problemAgents.length !== 1 ? t("dashboard.agentSignalCountPlural", { count: problemAgents.length }) : t("dashboard.agentSignalCount", { count: problemAgents.length }),
        detail: critical > 0 ? t("dashboard.criticalAgents", { critical }) : t("dashboard.heartbeatWarn"),
      });
    }
    if (hottestGpu && (hottestGpu.temp_c ?? 0) >= 80) items.push({ tone: "warn", title: t("dashboard.gpuHot", { temp: hottestGpu.temp_c ?? "-" }), detail: t("dashboard.gpuHotDetail", { name: hottestGpu.name }) });
    if (items.length === 0) items.push({ tone: "ok", title: t("dashboard.noIssues"), detail: t("dashboard.noIssuesDetail") });
    return items.slice(0, 4);
  }, [healthy, hottestGpu, problemAgents, update?.commit, update?.error, updateAvailable, updateState, t]);

  const hour = new Date().getHours();
  const greeting =
    hour < 12
      ? t("dashboard.goodMorning", { defaultValue: "Guten Morgen" })
      : hour < 18
      ? t("dashboard.goodAfternoon", { defaultValue: "Guten Tag" })
      : t("dashboard.goodEvening", { defaultValue: "Guten Abend" });

  const activityEntries = useMemo(
    () =>
      audit.map((e) => ({
        id: e.id,
        action: e.action,
        user: e.user || "—",
        timestamp: e.timestamp,
      })),
    [audit]
  );

  // ── Skeleton ────────────────────────────────────────────────────
  if (status === null) {
    return (
      <div className="space-y-3">
        <div className="rounded-xl border bg-card p-4 animate-pulse">
          <div className="h-5 w-48 bg-muted rounded-full mb-3" />
          <div className="h-6 w-56 bg-muted rounded-lg mb-1" />
          <div className="h-3 w-72 bg-muted rounded-lg max-w-full" />
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2">
          {[...Array(4)].map((_, i) => (
            <div key={i} className="rounded-lg border bg-card p-3 animate-pulse">
              <div className="h-3 w-16 bg-muted rounded mb-2" />
              <div className="h-5 w-10 bg-muted rounded" />
            </div>
          ))}
        </div>
        <div className="grid grid-cols-2 gap-3">
          {[...Array(6)].map((_, i) => (
            <div key={i} className="rounded-xl border bg-card p-3 animate-pulse">
              <div className="h-4 w-24 bg-muted rounded mb-2" />
              <div className="space-y-2">{[...Array(3)].map((_, j) => <div key={j} className="h-3 bg-muted rounded" />)}</div>
            </div>
          ))}
        </div>
      </div>
    );
  }

  // ── Widget props map ─────────────────────────────────────────────
  const widgetProps: Record<string, Record<string, unknown>> = {
    "agent-metric":     { agents },
    "project-metric":   { projects },
    "runtime-metric":   { running },
    "gpu-metric":       { gpuList, runningHeartbeats },
    "attention":       { items: attentionItems },
    "activity-stream": { activity: activityEntries, projectSignals },
    "context-metrics": { sessionMetrics, projectMap },
    "oauth":           { oauthUsage },
    "codex":           { codex },
    "minimax":         { minimax },
  };

  // Wrap widgets with their props
  const widgetComponents = WIDGET_COMPONENTS.map((wc) => ({
    id: wc.id,
    component: (props: { widgetId: string; isEditing?: boolean }) => {
      const extraProps = widgetProps[wc.id] ?? {};
      const Comp = wc.component as React.ComponentType<Record<string, unknown>>;
      return <Comp {...props} {...extraProps} />;
    },
  }));

  function handleRemoveWidget(id: string) {
    toggleWidget(id, false);
  }

  return (
    <div className="space-y-4">
      {/* Header bar */}
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <h1 className="text-lg font-semibold tracking-tight text-foreground">
            {greeting}, <span className="text-primary">{user?.username || ""}</span>
          </h1>
          {lastUpdated && (
            <span className="text-[10px] text-muted-foreground hidden sm:block">
              · {t("dashboard.updateSync", { time: lastUpdated.toLocaleTimeString("de-DE") })}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {!isEditing && (
            <button
              type="button"
              onClick={openSettings}
              className="flex items-center gap-1.5 rounded-md border bg-card px-3 py-1.5 text-xs font-medium text-muted-foreground hover:bg-muted transition-colors"
            >
              <Settings className="h-3 w-3" />
              Widgets
            </button>
          )}
          <EditBar
            isEditing={isEditing}
            onStartEdit={startEdit}
            onSave={saveEdit}
            onCancel={cancelEdit}
          />
        </div>
      </div>

      {/* Core status badge */}
      <div className="flex items-center gap-2">
        <span
          className={cn(
            "flex items-center gap-1.5 text-xs font-medium rounded-full px-2.5 py-1 border",
            healthy === false
              ? "bg-red-500/15 text-red-400 border-red-500/30"
              : "bg-green-500/15 text-green-400 border-green-500/30"
          )}
        >
          <span
            className={cn(
              "h-1.5 w-1.5 rounded-full",
              healthy === false ? "bg-red-400 animate-pulse" : "bg-green-400"
            )}
          />
          {healthy === false ? t("dashboard.coreOffline") : t("dashboard.coreOnline")}
        </span>
        {updateAvailable && (
          <span className="flex items-center gap-1.5 text-xs rounded-full px-2.5 py-1 bg-amber-500/15 text-amber-400 border border-amber-500/25">
            <RefreshCw className="h-3 w-3" />
            {t("layout.updateAvailable")}
          </span>
        )}
      </div>

      {/* Widget Grid */}
      <DashboardGrid
        widgets={widgetComponents}
        widgetConfigs={WIDGET_CONFIGS}
        widgetStates={widgets}
        isEditing={isEditing}
        onReorder={reorderWidgets}
        onRemoveWidget={handleRemoveWidget}
      />

      {/* Settings Drawer */}
      {showSettings && (
        <SettingsDrawer
          widgets={widgets.map((w) => ({
            id: w.id,
            label: WIDGET_LABELS[w.id] ?? w.id,
            enabled: w.enabled,
          }))}
          onToggle={toggleWidget}
          onClose={closeSettings}
        />
      )}
    </div>
  );
}

// Re-export DashboardPage for App.tsx compatibility
export { DashboardOverview as DashboardPage } from "@/pages/DashboardPage";
