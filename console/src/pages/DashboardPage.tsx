import { useCallback, useEffect, useMemo, useState, useRef } from "react";
import { useHeaderSlot } from "@/components/layout/HeaderSlotContext";
import { Bot, FolderKanban, Activity, Cpu, ArrowRight, ShieldCheck, Radar, Workflow, RefreshCw, Clock3, Layers3, AlertTriangle, Siren, TimerReset, BarChart2, LayoutDashboard, Plus, X, Save, Pencil, Trash2, FileText, Link2, MonitorPlay, Globe, Brain, Zap, RotateCcw, GitBranch, MessageSquare } from "lucide-react";
import { api, AuditEntry, GpuInfo, HeartbeatTaskStatus, UpdateStatus } from "@/lib/api";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";
import { useAuth } from "@/hooks/useAuth";
import { useNavigate } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import { cn } from "@/lib/utils";
import { ActivityPage } from "@/pages/ActivityPage";
import { UsagePage } from "@/pages/UsagePage";
import { AuditPage } from "@/pages/AuditPage";

type BuiltinTab = "overview" | "activity" | "usage" | "audit";

type CodexStatus = { configured: boolean; account_id: string | null; models?: string[]; rate_limits?: Record<string,string> };
type MinimaxModel = {
  name: string; label: string;
  interval_total: number; interval_used: number; interval_pct: number; interval_reset_in_s: number;
  weekly_total: number; weekly_used: number; weekly_pct: number;
};
type MinimaxUsage = { available: boolean; reason?: string; fetched_at?: string; models?: MinimaxModel[] };

interface CustomTab {
  id: string;
  label: string;
  icon: string;
  type: "markdown" | "links" | "iframe";
  content?: string;
  url?: string;
}

interface DashboardConfig {
  custom_tabs: CustomTab[];
  overview_widgets: { hidden: string[]; order: string[] };
}

const BUILTIN_TABS: { id: BuiltinTab; labelKey: string; icon: React.ElementType }[] = [
  { id: "overview",  labelKey: "dashboard.tabOverview",  icon: LayoutDashboard },
  { id: "activity",  labelKey: "dashboard.tabActivity",  icon: Activity },
  { id: "usage",     labelKey: "dashboard.tabUsage",     icon: BarChart2 },
  { id: "audit",     labelKey: "dashboard.tabAudit",     icon: ShieldCheck },
];

const TAB_ICON_MAP: Record<string, React.ElementType> = {
  FileText, Link2, MonitorPlay, Globe, LayoutDashboard, Activity, BarChart2, ShieldCheck,
  Bot, FolderKanban, Cpu, Radar, Workflow,
};

export function DashboardPage() {
  const { t } = useTranslation();
  const [searchParams, setSearchParams] = useSearchParams();
  const [config, setConfig] = useState<DashboardConfig>({ custom_tabs: [], overview_widgets: { hidden: [], order: [] } });
  const [editTab, setEditTab] = useState<CustomTab | null>(null);
  const [showEditor, setShowEditor] = useState(false);

  // Config laden
  useEffect(() => {
    api.get<DashboardConfig>("/dashboard/config").then(setConfig).catch(() => {});
  }, []);

  const rawTab = searchParams.get("tab") ?? "overview";
  const builtinIds = BUILTIN_TABS.map(t => t.id) as string[];
  const activeTab = builtinIds.includes(rawTab) || config.custom_tabs.some(ct => ct.id === rawTab) ? rawTab : "overview";

  function switchTab(tab: string) {
    if (tab === "overview") setSearchParams({}, { replace: true });
    else setSearchParams({ tab }, { replace: true });
  }

  async function saveConfig(newConfig: DashboardConfig) {
    setConfig(newConfig);
    await api.put("/dashboard/config", newConfig).catch(() => {});
  }

  function addOrUpdateTab(tab: CustomTab) {
    const existing = config.custom_tabs.findIndex(ct => ct.id === tab.id);
    const tabs = [...config.custom_tabs];
    if (existing >= 0) tabs[existing] = tab;
    else tabs.push(tab);
    saveConfig({ ...config, custom_tabs: tabs });
    setShowEditor(false);
    setEditTab(null);
    switchTab(tab.id);
  }

  function deleteTab(tabId: string) {
    saveConfig({ ...config, custom_tabs: config.custom_tabs.filter(ct => ct.id !== tabId) });
    if (activeTab === tabId) switchTab("overview");
  }

  const BuiltinContent = activeTab === "activity" ? ActivityPage
    : activeTab === "usage" ? UsagePage
    : activeTab === "audit" ? AuditPage
    : null;

  const customTab = config.custom_tabs.find(ct => ct.id === activeTab);
  const companionActive = useRef(localStorage.getItem("hh_companion") === "1");

  useHeaderSlot(
    <div className="mt-2">
      {!companionActive.current && (
        <p className="text-[10px] text-muted-foreground/40 italic mb-1.5">
          {t("dashboard.easterHint", { defaultValue: "Psst... ein kleines Wesen wartet darauf, dich zu begleiten. Finde die Version. Klopfe fünfmal. Es wird kommen." })}
        </p>
      )}
      <div className="flex gap-1 overflow-x-auto scrollbar-none pb-px">
        {BUILTIN_TABS.map(tab => (
          <button
            key={tab.id}
            onClick={() => switchTab(tab.id)}
            className={cn(
              "flex items-center gap-2 px-4 py-2.5 text-sm font-medium rounded-t-lg transition-colors border-b-2 -mb-px whitespace-nowrap",
              activeTab === tab.id
                ? "border-primary text-foreground bg-background"
                : "border-transparent text-muted-foreground hover:text-foreground hover:bg-muted"
            )}
          >
            <tab.icon size={14} />
            {t(tab.labelKey, { defaultValue: tab.id.charAt(0).toUpperCase() + tab.id.slice(1) })}
          </button>
        ))}
        {config.custom_tabs.map(ct => {
          const Icon = TAB_ICON_MAP[ct.icon] || FileText;
          return (
            <button
              key={ct.id}
              onClick={() => switchTab(ct.id)}
              className={cn(
                "group flex items-center gap-2 px-4 py-2.5 text-sm font-medium rounded-t-lg transition-colors border-b-2 -mb-px whitespace-nowrap",
                activeTab === ct.id
                  ? "border-primary text-foreground bg-background"
                  : "border-transparent text-muted-foreground hover:text-foreground hover:bg-muted"
              )}
            >
              <Icon size={14} />
              {ct.label}
              <span
                onClick={e => { e.stopPropagation(); setEditTab(ct); setShowEditor(true); }}
                className="hidden group-hover:inline-flex ml-1 text-muted-foreground/50 hover:text-primary"
              >
                <Pencil size={10} />
              </span>
            </button>
          );
        })}
        <button
          onClick={() => { setEditTab(null); setShowEditor(true); }}
          className="flex items-center gap-1 px-3 py-2.5 text-sm text-muted-foreground hover:text-primary transition-colors border-b-2 border-transparent -mb-px"
          title={t("dashboard.addTab", { defaultValue: "Tab hinzufügen" })}
        >
          <Plus size={14} />
        </button>
      </div>
    </div>,
    [activeTab, config.custom_tabs, t]
  );

  return (
    <div className="flex flex-col h-full">
      <div className="flex-1 overflow-auto">
        {BuiltinContent ? <BuiltinContent />
          : customTab ? <CustomTabContent tab={customTab} />
          : <DashboardOverview config={config} onConfigChange={saveConfig} />}
      </div>

      {/* Tab-Editor Modal */}
      {showEditor && (
        <TabEditorModal
          tab={editTab}
          onSave={addOrUpdateTab}
          onDelete={editTab ? () => { deleteTab(editTab.id); setShowEditor(false); setEditTab(null); } : undefined}
          onClose={() => { setShowEditor(false); setEditTab(null); }}
        />
      )}
    </div>
  );
}

/* ── Custom Tab Content ─────────────────────────────────────────── */

function CustomTabContent({ tab }: { tab: CustomTab }) {
  if (tab.type === "iframe" && tab.url) {
    return (
      <div className="h-full">
        <iframe src={tab.url} className="w-full h-full border-0" title={tab.label} sandbox="allow-scripts allow-same-origin allow-forms" />
      </div>
    );
  }
  return (
    <div className="p-6 max-w-4xl prose prose-sm dark:prose-invert">
      <ReactMarkdown>{tab.content || "*Noch kein Inhalt.*"}</ReactMarkdown>
    </div>
  );
}

/* ── Tab Editor Modal ───────────────────────────────────────────── */

function TabEditorModal({ tab, onSave, onDelete, onClose }: {
  tab: CustomTab | null;
  onSave: (tab: CustomTab) => void;
  onDelete?: () => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [label, setLabel] = useState(tab?.label || "");
  const [icon, setIcon] = useState(tab?.icon || "FileText");
  const [type, setType] = useState<CustomTab["type"]>(tab?.type || "markdown");
  const [content, setContent] = useState(tab?.content || "");
  const [url, setUrl] = useState(tab?.url || "");

  function save() {
    const id = tab?.id || label.toLowerCase().replace(/[^a-z0-9]/g, "-").replace(/-+/g, "-").slice(0, 30) || `tab-${Date.now()}`;
    onSave({ id, label: label || "Neuer Tab", icon, type, content, url });
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center justify-center">
      <div className="bg-card border rounded-2xl shadow-2xl max-w-lg w-full mx-4 p-6 space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold">{tab ? t("dashboard.editTab", { defaultValue: "Tab bearbeiten" }) : t("dashboard.addTab", { defaultValue: "Neuer Tab" })}</h2>
          <button onClick={onClose} className="rounded-lg p-1.5 hover:bg-muted"><X size={16} /></button>
        </div>

        <div className="space-y-3">
          <div>
            <label className="text-xs font-medium text-muted-foreground">{t("dashboard.tabName", { defaultValue: "Name" })}</label>
            <input value={label} onChange={e => setLabel(e.target.value)} placeholder="Mein Tab"
              className="w-full mt-1 rounded-lg border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring" />
          </div>

          <div>
            <label className="text-xs font-medium text-muted-foreground">{t("dashboard.tabIcon", { defaultValue: "Icon" })}</label>
            <select value={icon} onChange={e => setIcon(e.target.value)}
              className="w-full mt-1 rounded-lg border bg-background px-3 py-2 text-sm">
              {Object.keys(TAB_ICON_MAP).map(k => <option key={k} value={k}>{k}</option>)}
            </select>
          </div>

          <div>
            <label className="text-xs font-medium text-muted-foreground">{t("dashboard.tabType", { defaultValue: "Typ" })}</label>
            <div className="flex gap-2 mt-1">
              {(["markdown", "iframe"] as const).map(tp => (
                <button key={tp} onClick={() => setType(tp)}
                  className={cn("flex-1 px-3 py-2 text-xs rounded-lg border transition-colors",
                    type === tp ? "border-primary bg-primary/10 text-primary" : "hover:bg-muted")}>
                  {tp === "markdown" ? "Markdown / Notizen" : "iFrame (URL)"}
                </button>
              ))}
            </div>
          </div>

          {type === "markdown" ? (
            <div>
              <label className="text-xs font-medium text-muted-foreground">{t("dashboard.tabContent", { defaultValue: "Inhalt (Markdown)" })}</label>
              <textarea value={content} onChange={e => setContent(e.target.value)} rows={8} placeholder="## Meine Notizen\n\n- Link 1\n- Link 2"
                className="w-full mt-1 rounded-lg border bg-background px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-ring" />
            </div>
          ) : (
            <div>
              <label className="text-xs font-medium text-muted-foreground">{t("dashboard.tabUrl", { defaultValue: "URL" })}</label>
              <input value={url} onChange={e => setUrl(e.target.value)} placeholder="https://grafana.local/d/..."
                className="w-full mt-1 rounded-lg border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring" />
            </div>
          )}
        </div>

        <div className="flex justify-between pt-2">
          {onDelete ? (
            <button onClick={onDelete} className="flex items-center gap-1.5 px-3 py-2 text-xs text-red-500 hover:bg-red-500/10 rounded-lg transition-colors">
              <Trash2 size={14} /> {t("common.delete", { defaultValue: "Löschen" })}
            </button>
          ) : <div />}
          <button onClick={save}
            className="flex items-center gap-1.5 px-4 py-2 text-sm rounded-lg bg-primary text-primary-foreground hover:bg-primary/90">
            <Save size={14} /> {t("common.save", { defaultValue: "Speichern" })}
          </button>
        </div>
      </div>
    </div>
  );
}

const WIDGET_DEFS = [
  { id: "status",     label: "Core-Status",    icon: "Radar" },
  { id: "metrics",    label: "Metriken",       icon: "BarChart2" },
  { id: "context",    label: "Context-Metriken", icon: "Brain" },
  { id: "agents",     label: "Agenten",        icon: "Bot" },
  { id: "heartbeat",  label: "Heartbeats",     icon: "Activity" },
  { id: "gpu",        label: "GPU",            icon: "Cpu" },
  { id: "update",     label: "Update-Status",  icon: "RefreshCw" },
  { id: "audit",      label: "Audit-Log",      icon: "ShieldCheck" },
  { id: "codex",      label: "Codex",          icon: "Cpu" },
  { id: "minimax",    label: "MiniMax",        icon: "Zap" },
];

function DashboardOverview({ config, onConfigChange }: { config: DashboardConfig; onConfigChange: (c: DashboardConfig) => void }) {
  const [showWidgetSettings, setShowWidgetSettings] = useState(false);
  const hidden = new Set(config.overview_widgets?.hidden || []);

  function toggleWidget(id: string) {
    const newHidden = hidden.has(id)
      ? [...config.overview_widgets.hidden].filter(h => h !== id)
      : [...(config.overview_widgets.hidden || []), id];
    onConfigChange({ ...config, overview_widgets: { ...config.overview_widgets, hidden: newHidden } });
  }

  function isVisible(id: string) { return !hidden.has(id); }
  const { t } = useTranslation();
  const { user } = useAuth();
  const navigate = useNavigate();
  const [status, setStatus] = useState<Record<string, any> | null>(null);
  const [healthy, setHealthy] = useState<boolean | null>(null);
  const [gpu, setGpu] = useState<GpuInfo | null>(null);
  const [heartbeatTasks, setHeartbeatTasks] = useState<HeartbeatTaskStatus[]>([]);
  const [update, setUpdate] = useState<UpdateStatus | null>(null);
  const [oauthUsage, setOauthUsage] = useState<Record<string,unknown> | null>(null);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [projectMap, setProjectMap] = useState<Record<string, any>>({});
  const [agentMap, setAgentMap] = useState<Record<string, any>>({});
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [sessionMetrics, setSessionMetrics] = useState<Record<string, any>>({});
  const [codex, setCodex] = useState<CodexStatus | null>(null);
  const [minimax, setMinimax] = useState<MinimaxUsage | null>(null);

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
      api.oauthUsage(),
      api.sessionMetrics(),
      api.openaiCodexStatus(),
      api.minimaxUsage(),
    ]).then((results) => {
      if (!alive) return;
      const [healthRes, statusRes, gpuRes, hbRes, updateRes, auditRes, projectsRes, agentsRes, oauthRes, smRes, codexRes, minimaxRes] = results;
      setHealthy(healthRes.status === "fulfilled");
      if (statusRes.status === "fulfilled") setStatus(statusRes.value);
      if (gpuRes.status === "fulfilled") setGpu(gpuRes.value);
      if (hbRes.status === "fulfilled") setHeartbeatTasks(hbRes.value.tasks);
      if (updateRes.status === "fulfilled") setUpdate(updateRes.value);
      if (auditRes.status === "fulfilled") setAudit(auditRes.value.logs);
      if (projectsRes.status === "fulfilled") setProjectMap(projectsRes.value as Record<string, any>);
      if (agentsRes.status === "fulfilled") setAgentMap(agentsRes.value as Record<string, any>);
      if (oauthRes.status === "fulfilled") setOauthUsage(oauthRes.value as Record<string, unknown>);
      if (smRes.status === "fulfilled") setSessionMetrics(smRes.value as Record<string, any>);
      if (codexRes.status === "fulfilled") setCodex(codexRes.value as CodexStatus);
      if (minimaxRes.status === "fulfilled") setMinimax(minimaxRes.value as MinimaxUsage);
      setLastUpdated(new Date());
      setIsRefreshing(false);
    });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    let alive = true;
    const poll = () => { api.oauthUsage().then(d => { if (alive) setOauthUsage(d as Record<string,unknown>); }).catch(() => {}); };
    const t = setInterval(poll, 3000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  useEffect(() => {
    let alive = true;
    const poll = () => {
      Promise.allSettled([api.openaiCodexStatus(), api.minimaxUsage()]).then(([cx, mu]) => {
        if (!alive) return;
        if (cx.status === "fulfilled") setCodex(cx.value as CodexStatus);
        if (mu.status === "fulfilled") setMinimax(mu.value as MinimaxUsage);
      });
    };
    const t = setInterval(poll, 3000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  useEffect(() => {
    let disposed = false;
    loadDashboard(false);
    const timer = setInterval(() => { if (!disposed) loadDashboard(true); }, 15000);
    return () => { disposed = true; clearInterval(timer); };
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
  const problemAgents = useMemo(() => {
    return Object.entries(agentMap)
      .map(([id, entry]) => {
        if (id.endsWith("_template") || id.endsWith("-template")) return null;
        const runtimeState = entry?.runtime;
        if (!runtimeState) return { id, severity: "warn" as const, summary: t("agents.noRuntime"), detail: t("dashboard.agentConfiguredNotStarted") };
        if (runtimeState.status !== "running") return { id, severity: "critical" as const, summary: `Runtime ${runtimeState.status}`, detail: t("dashboard.agentNotRunning") };
        if (runtimeState.restart_count > 0) return { id, severity: "warn" as const, summary: `${runtimeState.restart_count} Restarts`, detail: t("dashboard.agentRestarted") };
        const heartbeatAge = Number(runtimeState.last_heartbeat_age ?? 0);
        const heartbeatTimeout = Number(runtimeState.heartbeat_timeout ?? 0);
        if (heartbeatTimeout > 0 && heartbeatAge > heartbeatTimeout * 0.75) return { id, severity: "warn" as const, summary: t("dashboard.heartbeatLate"), detail: t("dashboard.heartbeatLateDetail", { age: heartbeatAge.toFixed(0) }) };
        return null;
      })
      .filter((entry): entry is { id: string; severity: "warn" | "critical"; summary: string; detail: string } => entry !== null)
      .sort((a, b) => (a.severity === b.severity ? a.id.localeCompare(b.id) : a.severity === "critical" ? -1 : 1));
  }, [agentMap, t]);
  const projectSignals = useMemo(() => {
    return activeProjects.map((id) => {
      const entry = projectMap[id];
      if (!entry) return { id, title: id, summary: t("dashboard.sessionNoProject"), meta: t("dashboard.sessionNoProjectDetail"), tone: "warn" as const };
      const workerCount = Array.isArray(entry.workers) ? entry.workers.length : 0;
      return { id, title: entry.name || id, summary: `Boss ${entry.boss || "-"}`, meta: `${workerCount} Worker · ${entry.matrix_room ? t("dashboard.matrixActive") : t("dashboard.noMatrixRoom")}`, tone: "ok" as const };
    });
  }, [activeProjects, projectMap, t]);
  const attentionItems = useMemo(() => {
    const items: { tone: "critical" | "warn" | "info"; title: string; detail: string }[] = [];
    if (healthy === false) items.push({ tone: "critical", title: t("dashboard.coreDisturbed2"), detail: t("dashboard.coreDisturbed2Detail") });
    if (updateState === "error") items.push({ tone: "critical", title: t("dashboard.updateError"), detail: update?.error || t("dashboard.updateErrorFallback") });
    else if (updateState === "running") items.push({ tone: "info", title: t("dashboard.updateRunning2"), detail: t("dashboard.updateRunning2Detail") });
    else if (updateAvailable) items.push({ tone: "warn", title: t("dashboard.updateAlertTitle"), detail: t("dashboard.updateAlertAvailable", { commit: update?.commit ?? t("dashboard.commitUnknown") }) });
    if (problemAgents.length > 0) {
      const critical = problemAgents.filter((entry) => entry.severity === "critical").length;
      items.push({ tone: critical > 0 ? "critical" : "warn", title: problemAgents.length !== 1 ? t("dashboard.agentSignalCountPlural", { count: problemAgents.length }) : t("dashboard.agentSignalCount", { count: problemAgents.length }), detail: critical > 0 ? t("dashboard.criticalAgents", { critical }) : t("dashboard.heartbeatWarn") });
    }
    if (hottestGpu && (hottestGpu.temp_c ?? 0) >= 80) items.push({ tone: "warn", title: t("dashboard.gpuHot", { temp: hottestGpu.temp_c ?? "-" }), detail: t("dashboard.gpuHotDetail", { name: hottestGpu.name }) });
    if (items.length === 0) items.push({ tone: "info", title: t("dashboard.noIssues"), detail: t("dashboard.noIssuesDetail") });
    return items.slice(0, 4);
  }, [healthy, hottestGpu, problemAgents, update?.commit, update?.error, updateAvailable, updateState, t]);

  const greeting = (() => {
    const h = new Date().getHours();
    if (h < 12) return t("dashboard.goodMorning", { defaultValue: "Guten Morgen" });
    if (h < 18) return t("dashboard.goodAfternoon", { defaultValue: "Guten Tag" });
    return t("dashboard.goodEvening", { defaultValue: "Guten Abend" });
  })();

  const activityItems = audit.slice(0, 6).map((entry) => {
    let dotColor = "bg-muted";
    if (entry.action.toLowerCase().includes("start") || entry.action.toLowerCase().includes("created")) dotColor = "bg-green-400";
    else if (entry.action.toLowerCase().includes("error") || entry.action.toLowerCase().includes("fail")) dotColor = "bg-red-400";
    else if (entry.action.toLowerCase().includes("stop") || entry.action.toLowerCase().includes("delete")) dotColor = "bg-orange-400";
    return { dotColor, text: `${entry.action} — ${entry.target}`, time: new Date(entry.timestamp).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" }) };
  });

  const totalCacheHitRate = useMemo(() => {
    const entries = Object.values(sessionMetrics) as any[];
    if (entries.length === 0) return null;
    const total = entries.reduce((sum: number, m: any) => sum + (m.cache_hit_rate ?? 0), 0);
    return Math.round((total / entries.length) * 100);
  }, [sessionMetrics]);

  const totalToolRounds = useMemo(() => {
    const entries = Object.values(sessionMetrics) as any[];
    return entries.reduce((sum: number, m: any) => sum + (m.tool_rounds_total ?? 0), 0);
  }, [sessionMetrics]);

  if (status === null) {
    return (
      <div className="space-y-6 p-6">
        <div className="flex items-center gap-4">
          <div className="animate-pulse rounded-lg bg-muted/50 h-8 w-64" />
          <div className="animate-pulse rounded-full bg-muted/50 h-6 w-32" />
        </div>
        <div className="grid gap-4 grid-cols-2 xl:grid-cols-4">
          {[...Array(4)].map((_, i) => (
            <div key={i} className="bg-card border border-border rounded-xl p-4">
              <div className="animate-pulse rounded-lg bg-muted/50 h-4 w-20 mb-3" />
              <div className="animate-pulse rounded-lg bg-muted/50 h-8 w-12" />
            </div>
          ))}
        </div>
        <div className="grid gap-4 grid-cols-[2fr_1fr]">
          <div className="bg-card border border-border rounded-xl p-5">
            <div className="animate-pulse rounded-lg bg-muted/50 h-5 w-32 mb-4" />
            <div className="space-y-3">{[...Array(4)].map((_, i) => <div key={i} className="animate-pulse rounded-lg bg-muted/50 h-10 w-full" />)}</div>
          </div>
          <div className="bg-card border border-border rounded-xl p-5">
            <div className="animate-pulse rounded-lg bg-muted/50 h-5 w-28 mb-4" />
            <div className="space-y-3">{[...Array(3)].map((_, i) => <div key={i} className="animate-pulse rounded-lg bg-muted/50 h-12 w-full" />)}</div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6 p-6">
      {/* HERO ZONE */}
      <div className="flex flex-col gap-4">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div>
            <h1 className="text-2xl font-bold tracking-tight">{greeting}{user ? `, ${user.username || ""}` : ""}</h1>
            <p className="text-sm text-muted-foreground mt-0.5">HydraHive Übersicht</p>
          </div>
          <div className={cn("flex items-center gap-2 rounded-full px-3 py-1.5 text-xs font-medium", healthy === false ? "bg-destructive/15 text-destructive" : "bg-green-500/15 text-green-400")}>
            <span className={cn("h-1.5 w-1.5 rounded-full", healthy === false ? "bg-destructive" : "bg-green-400")} />
            {healthy === false ? t("dashboard.coreOffline") : t("dashboard.coreOnline")}
          </div>
        </div>

        {/* Slim Metric Cards */}
        <div className="grid gap-4 grid-cols-2 xl:grid-cols-4">
          <div className="bg-card border border-border rounded-xl p-4">
            <p className="text-xs text-muted-foreground font-medium">{t("dashboard.agentsLabel", { defaultValue: "Agents" })}</p>
            <p className="text-3xl font-bold tracking-tight mt-1" style={{ color: "hsl(var(--candy-violet))" }}>{agents ?? "..."}</p>
          </div>
          <div className="bg-card border border-border rounded-xl p-4">
            <p className="text-xs text-muted-foreground font-medium">{t("dashboard.projectsLabel", { defaultValue: "Projects" })}</p>
            <p className="text-3xl font-bold tracking-tight mt-1" style={{ color: "hsl(var(--candy-cyan))" }}>{projects ?? "..."}</p>
          </div>
          <div className="bg-card border border-border rounded-xl p-4">
            <p className="text-xs text-muted-foreground font-medium">{t("dashboard.runtimeLabel", { defaultValue: "Runtime" })}</p>
            <p className="text-3xl font-bold tracking-tight mt-1" style={{ color: "hsl(var(--candy-lime))" }}>{running}</p>
          </div>
          <div className="bg-card border border-border rounded-xl p-4">
            <p className="text-xs text-muted-foreground font-medium">{hottestGpu ? "GPU Temp" : "Uptime"}</p>
            <p className="text-3xl font-bold tracking-tight mt-1" style={{ color: "hsl(var(--candy-amber))" }}>
              {hottestGpu ? `${hottestGpu.temp_c ?? "-"}°C` : (status?.uptime ? `${Math.floor(Number(status.uptime) / 86400)}d` : "...")}
            </p>
          </div>
        </div>
      </div>

      {/* TWO-COLUMN LAYOUT */}
      <div className="grid gap-4 grid-cols-[2fr_1fr]">
        {/* LEFT COLUMN */}
        <div className="space-y-4">
          <div className="bg-card border border-border rounded-xl p-5">
            <div className="flex items-center gap-2 mb-4">
              <Activity className="h-4 w-4 text-muted-foreground" />
              <h2 className="text-sm font-semibold">Activity Stream</h2>
            </div>
            <div className="space-y-2">
              {activityItems.length === 0 ? (
                <p className="text-sm text-muted-foreground py-2">Keine Aktivitäten</p>
              ) : activityItems.map((item, i) => (
                <div key={i} className="flex items-center gap-3 py-1.5">
                  <span className={cn("h-1.5 w-1.5 rounded-full shrink-0", item.dotColor)} />
                  <span className="text-sm text-foreground flex-1 truncate">{item.text}</span>
                  <span className="text-xs text-muted-foreground shrink-0">{item.time}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="bg-card border border-border rounded-xl p-5">
            <div className="flex items-center gap-2 mb-4">
              <Brain className="h-4 w-4 text-primary" />
              <h2 className="text-sm font-semibold">Context-Metriken</h2>
            </div>
            <div className="space-y-3">
              {totalCacheHitRate !== null ? (
                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <span className="text-xs text-muted-foreground">Token-Cache</span>
                    <span className="text-xs font-medium" style={{ color: "hsl(var(--candy-cyan))" }}>{totalCacheHitRate}%</span>
                  </div>
                  <div className="h-1.5 bg-muted rounded-full overflow-hidden">
                    <div className="h-full rounded-full" style={{ width: `${totalCacheHitRate}%`, backgroundColor: "hsl(var(--candy-cyan))" }} />
                  </div>
                </div>
              ) : (
                <p className="text-xs text-muted-foreground">Keine aktiven Sessions</p>
              )}
              {totalToolRounds > 0 && (
                <div className="flex items-center justify-between">
                  <span className="text-xs text-muted-foreground">Tool-Loops</span>
                  <span className="text-xs font-medium text-foreground">{totalToolRounds} total</span>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* RIGHT COLUMN */}
        <div className="space-y-4">
          <div className="bg-card border border-border rounded-xl p-5"
            style={{ borderLeftWidth: "3px", borderLeftColor: attentionItems.some(i => i.tone === "critical") ? "hsl(var(--destructive))" : attentionItems.some(i => i.tone === "warn") ? "hsl(var(--candy-amber))" : "hsl(var(--candy-violet))" }}
          >
            <div className="flex items-center gap-2 mb-3">
              <AlertTriangle className="h-4 w-4 text-muted-foreground" />
              <h2 className="text-sm font-semibold">Attention</h2>
            </div>
            <div className="space-y-2">
              {attentionItems.map((item, i) => (
                <div key={i} className="flex items-start gap-2 py-1">
                  {item.tone === "warn" && <span className="text-amber-400 mt-0.5 shrink-0">●</span>}
                  {item.tone === "critical" && <span className="text-destructive mt-0.5 shrink-0">●</span>}
                  {item.tone === "info" && <span className="text-violet-400 mt-0.5 shrink-0">●</span>}
                  <div className="min-w-0">
                    <p className="text-sm text-foreground truncate">{item.title}</p>
                    <p className="text-xs text-muted-foreground truncate">{item.detail}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="bg-card border border-border rounded-xl p-5">
            <div className="flex items-center gap-2 mb-3">
              <Zap className="h-4 w-4 text-primary" />
              <h2 className="text-sm font-semibold">Quick Actions</h2>
            </div>
            <div className="space-y-2">
              <button onClick={() => navigate("/agents/new")} className="w-full flex items-center justify-between gap-3 rounded-lg border border-border bg-card px-3 py-2.5 text-sm text-foreground transition-colors hover:bg-muted hover:border-primary/30">
                <span className="flex items-center gap-2"><Plus className="h-4 w-4 text-primary" />Neuer Agent</span>
                <ArrowRight className="h-3.5 w-3.5 text-muted-foreground" />
              </button>
              <button onClick={() => navigate("/admin/backups")} className="w-full flex items-center justify-between gap-3 rounded-lg border border-border bg-card px-3 py-2.5 text-sm text-foreground transition-colors hover:bg-muted hover:border-primary/30">
                <span className="flex items-center gap-2"><ShieldCheck className="h-4 w-4 text-primary" />Backup starten</span>
                <ArrowRight className="h-3.5 w-3.5 text-muted-foreground" />
              </button>
              <button onClick={() => navigate("/my-agent")} className="w-full flex items-center justify-between gap-3 rounded-lg border border-border bg-card px-3 py-2.5 text-sm text-foreground transition-colors hover:bg-muted hover:border-primary/30">
                <span className="flex items-center gap-2"><MessageSquare className="h-4 w-4 text-primary" />Session öffnen</span>
                <ArrowRight className="h-3.5 w-3.5 text-muted-foreground" />
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* FULL-WIDTH: OAuth */}
      {oauthUsage && (oauthUsage.available || oauthUsage.message) ? (
        <div className="bg-card border border-border rounded-xl p-4">
          <div className="flex items-center gap-3">
            <Activity className="h-4 w-4 text-muted-foreground shrink-0" />
            <span className="text-sm font-medium">Claude OAuth</span>
            <button onClick={() => { api.oauthUsageFetch().then(d => { if (d && (d as any).available) api.oauthUsage().then(c => setOauthUsage(c as Record<string,unknown>)).catch(() => {}); }).catch(() => {}); }} className="p-0.5 rounded hover:bg-muted transition-colors" aria-label="OAuth Refresh">
              <RefreshCw className="h-3 w-3 text-muted-foreground" />
            </button>
            {!oauthUsage.available ? (
              <span className="text-xs text-muted-foreground ml-auto">{String(oauthUsage.message)}</span>
            ) : (
              <div className="flex items-center gap-4 ml-auto">
                {(["5h", "7d"] as const).map(w => {
                  const d = oauthUsage[w] as { utilization_pct: number; label: string } | undefined;
                  if (!d) return null;
                  const pct = d.utilization_pct ?? 0;
                  const color = pct >= 90 ? "bg-red-500" : pct >= 70 ? "bg-orange-500" : pct >= 40 ? "bg-yellow-500" : "bg-green-500";
                  return (
                    <div key={w} className="flex items-center gap-2 min-w-[140px]">
                      <span className="text-xs text-muted-foreground whitespace-nowrap">{d.label}</span>
                      <div className="h-1.5 flex-1 bg-muted rounded-full overflow-hidden min-w-[60px]">
                        <div className={cn("h-full rounded-full", color)} style={{ width: `${Math.min(100, pct)}%` }} />
                      </div>
                      <span className="text-[10px] text-muted-foreground w-8 text-right">{pct}%</span>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      ) : null}

      {/* FULL-WIDTH: Codex + MiniMax */}
      {(codex?.configured || minimax?.available) ? (
        <div className="grid gap-4 grid-cols-2">
          {codex?.configured ? (
            <div className="bg-card border border-border rounded-xl p-4">
              <div className="flex items-center gap-2 mb-3">
                <Cpu className="h-4 w-4 text-muted-foreground" />
                <span className="text-sm font-medium">Codex — Token-Plan</span>
              </div>
              <div className="space-y-2">
                {(() => {
                  const rl = codex.rate_limits || {};
                  const primary = parseInt(rl["x-codex-primary-used-percent"] ?? "", 10);
                  const secondary = parseInt(rl["x-codex-secondary-used-percent"] ?? "", 10);
                  return [
                    { label: "Session (5h)", pct: isNaN(primary) ? 0 : primary },
                    { label: "Woche (7d)", pct: isNaN(secondary) ? 0 : secondary },
                  ].map((b, idx) => {
                    const color = b.pct >= 90 ? "bg-red-500" : b.pct >= 70 ? "bg-orange-500" : b.pct >= 40 ? "bg-yellow-500" : "bg-green-500";
                    return (
                      <div key={idx} className="flex items-center gap-3">
                        <span className="text-xs text-muted-foreground w-28">{b.label}</span>
                        <div className="h-1.5 flex-1 bg-muted rounded-full overflow-hidden">
                          <div className={cn("h-full rounded-full", color)} style={{ width: `${Math.min(100, b.pct)}%` }} />
                        </div>
                        <span className="text-[10px] text-muted-foreground w-10 text-right">{b.pct}%</span>
                      </div>
                    );
                  });
                })()}
              </div>
            </div>
          ) : null}

          {minimax?.available ? (
            <div className="bg-card border border-border rounded-xl p-4">
              <div className="flex items-center gap-2 mb-3">
                <Zap className="h-4 w-4 text-muted-foreground" />
                <span className="text-sm font-medium">MiniMax — Token-Plan</span>
              </div>
              <div className="space-y-2">
                {(minimax.models ?? []).slice(0, 2).map(m => (
                  <div key={m.name}>
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-xs font-medium">{m.label}</span>
                      <span className="text-[10px] text-muted-foreground">{m.interval_pct}%</span>
                    </div>
                    <div className="h-1 bg-muted rounded-full overflow-hidden">
                      <div className="h-full rounded-full bg-violet-400" style={{ width: `${Math.min(100, m.interval_pct)}%` }} />
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
