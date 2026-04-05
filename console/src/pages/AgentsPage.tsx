import { useEffect, useMemo, useRef, useState } from "react";
import { Bot, RefreshCw, Circle, Plus, X, Save, Trash2, Pencil, ScrollText, BookOpen, Timer, MessageSquare, ShieldAlert, Radar, Workflow, Cpu, ArrowRight, Activity, Search, Puzzle, Server as ServerIcon, Globe, Wrench } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { api, HeartbeatTaskStatus, McpServer, PluginInfo } from "@/lib/api";
import { SkillsPanel } from "@/components/SkillsPanel";
import { ToolGroupSelector } from "@/components/ToolGroupSelector";
import { useAuth } from "@/hooks/useAuth";
import { useTranslation } from "react-i18next";
import { agentCategory, AGENT_COLORS, cn } from "@/lib/utils";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { ToolsPage } from "@/pages/ToolsPage";
import { A2APage } from "@/pages/A2APage";
import { AgentBlueprintTab } from "@/pages/blueprint/AgentBlueprintTab";
import { PluginsPage } from "@/pages/PluginsPage";

interface AgentRuntime {
  status: string;
  type: string;
  restart_count: number;
  last_heartbeat_age: number;
  heartbeat_timeout: number;
  heartbeat_interval: number;
  on_failure: string;
  heartbeat_enabled: boolean;
}
interface AgentEntry {
  config: { type: string; identity: string; model: string };
  runtime: AgentRuntime | null;
}

const EMPTY_FORM = {
  id: "",
  type: "specialist",
  identity: "",
  model: "llama3.1:8b",
  temperature: 0.7,
  max_tokens: 4096,
  soul: "",
  tools: [] as string[],
  fallback_models: [] as string[],
  mcp_servers: [] as string[],
  allowed_agents: [] as string[],
  sources: [] as { name: string; url: string; description: string }[],
  max_tool_rounds: null as number | null,
  heartbeat_interval: "30s",
  heartbeat_timeout: "90s",
  heartbeat_on_failure: "restart",
  ollama_base_url: null as string | null,
};

// Fallback falls API-Call fehlschlägt
const KNOWN_TOOLS_FALLBACK = ["file_read", "file_write", "shell_exec", "web_search", "http_request", "read_memory", "write_memory", "ask_agent", "delegate_agent"];
const DANGER_TOOLS = new Set(["project_shell", "create_agent", "delete_agent", "create_project", "delete_project"]);
const KNOWN_MODELS = [
  // Anthropic (OAuth)
  "claude-opus-4-6",
  "claude-sonnet-4-6",
  "claude-haiku-4-5-20251001",
  // OpenAI Codex (OAuth) — Prefix openai-codex/
  "openai-codex/gpt-5.1",
  "openai-codex/gpt-5.1-codex-max",
  "openai-codex/gpt-5.1-codex-mini",
  "openai-codex/gpt-5.2",
  "openai-codex/gpt-5.2-codex",
  "openai-codex/gpt-5.3-codex",
  "openai-codex/gpt-5.3-codex-spark",
  "openai-codex/gpt-5.4",
  // Ollama (lokal)
  "llama3.2:3b",
  "llama3.1:8b",
  "mistral-nemo:12b",
];
const STATUS_COLORS: Record<string, string> = {
  running: "text-green-500",
  starting: "text-yellow-500",
  restarting: "text-orange-500",
  stopped: "text-muted-foreground",
  error: "text-destructive",
};

function Field({ label, children, hint }: { label: string; children: React.ReactNode; hint?: string }) {
  return (
    <div className="space-y-1.5">
      <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">{label}</label>
      {children}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

function Input({ value, onChange, ...props }: React.InputHTMLAttributes<HTMLInputElement> & { value: string | number; onChange: (e: React.ChangeEvent<HTMLInputElement>) => void }) {
  return (
    <input
      value={value}
      onChange={onChange}
      {...props}
      className="w-full rounded-2xl border bg-background px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
    />
  );
}

function AgentsCrudTab() {
  const { t } = useTranslation();
  const { isAdmin } = useAuth();
  const navigate = useNavigate();
  const [agents, setAgents] = useState<Record<string, AgentEntry>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);
  const [form, setForm] = useState({ ...EMPTY_FORM });
  const [saving, setSaving] = useState(false);
  const [saveErr, setSaveErr] = useState("");
  const [deleting, setDeleting] = useState<string | null>(null);
  const [fallbackInput, setFallbackInput] = useState("");
  const [skillsAgent, setSkillsAgent] = useState<string | null>(null);
  const [hbEditAgent, setHbEditAgent] = useState<string | null>(null);
  const [hbForm, setHbForm] = useState({ enabled: true, interval: "30s", timeout: "90s", on_failure: "restart" });
  const [hbSaving, setHbSaving] = useState(false);
  const [hbErr, setHbErr] = useState("");
  const [knownTools, setKnownTools] = useState<string[]>(KNOWN_TOOLS_FALLBACK);
  const [logAgent, setLogAgent] = useState<string | null>(null);
  const [logLines, setLogLines] = useState<string[]>([]);
  const [logErr, setLogErr] = useState("");
  const [hbTasks, setHbTasks] = useState<HeartbeatTaskStatus[]>([]);
  const [mcpServers, setMcpServers] = useState<McpServer[]>([]);
  const [plugins, setPlugins] = useState<PluginInfo[]>([]);
  const [agentPlugins, setAgentPlugins] = useState<string[]>([]);
  const [wksModels, setWksModels] = useState<{id:string;label:string;wks_base_url?:string}[]>([]);
  const [agentSearch, setAgentSearch] = useState("");
  const logBottomRef = useRef<HTMLDivElement>(null);
  const logIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [confirmState, setConfirmState] = useState<{action: () => void; title: string; message: string} | null>(null);

  async function load() {
    try {
      const [agentsData, hbData, mcpData, pluginsData] = await Promise.allSettled([
        api.agents() as Promise<Record<string, AgentEntry>>,
        api.heartbeatTasks(),
        api.mcpServers(),
        api.pluginsList(),
      ]);
      if (agentsData.status === "fulfilled") setAgents(agentsData.value);
      if (hbData.status === "fulfilled") setHbTasks(hbData.value.tasks);
      if (mcpData.status === "fulfilled") setMcpServers(mcpData.value.servers);
      if (pluginsData.status === "fulfilled") setPlugins(pluginsData.value.plugins.filter(p => p.enabled));
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : t("common.error"));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  useEffect(() => {
    load();
    // Alle verfügbaren Tools aus der Registry laden
    api.get<Record<string, any>>("/tools").then(tools => {
      setKnownTools(Object.keys(tools).sort());
    }).catch(e => console.error("Failed to load tools registry", e));
    api.get<{models:{id:string;label:string;provider:string;wks_base_url?:string}[]}>("/llm/available-models").then(d => {
      setWksModels(d.models.filter(m => m.provider === "wks_ollama"));
    }).catch(e => console.error("Failed to load available models", e));
  }, []);
  function refresh() {
    setRefreshing(true);
    load();
  }

  async function fetchLogs(id: string) {
    try {
      const d = await api.agentLogs(id);
      setLogLines(d.lines);
      setLogErr("");
    } catch (e) {
      setLogErr(e instanceof Error ? e.message : t("common.error"));
    }
  }

  function openLogs(id: string) {
    if (logAgent === id) {
      closeLogs();
      return;
    }
    setLogAgent(id);
    setLogLines([]);
    setLogErr("");
    fetchLogs(id);
    if (logIntervalRef.current) clearInterval(logIntervalRef.current);
    logIntervalRef.current = setInterval(() => fetchLogs(id), 3000);
  }

  function closeLogs() {
    setLogAgent(null);
    if (logIntervalRef.current) {
      clearInterval(logIntervalRef.current);
      logIntervalRef.current = null;
    }
  }

  useEffect(() => {
    logBottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logLines]);

  useEffect(() => () => {
    if (logIntervalRef.current) clearInterval(logIntervalRef.current);
  }, []);

  async function openNew() {
    setForm({ ...EMPTY_FORM });
    setEditId(null);
    setSaveErr("");
    setAgentPlugins([]);
    setShowForm(true);
  }

  async function openEdit(id: string, entry: AgentEntry) {
    setSaveErr("");
    const [full, soul, agPlugins] = await Promise.all([
      api.get<{ config: Record<string, unknown> }>(`/agents/${id}`).catch(() => null),
      api.getAgentSoul(id).catch(() => ({ soul: "", exists: false })),
      api.pluginAgentGet(id).catch(() => ({ agent_id: id, plugins: [] as string[] })),
    ]);
    setAgentPlugins(agPlugins.plugins);
    const cfg = full?.config as any;
    setFallbackInput("");
    setForm({
      id,
      type: cfg?.type ?? entry.config.type,
      identity: cfg?.identity ?? entry.config.identity,
      model: cfg?.llm?.model ?? entry.config.model,
      temperature: cfg?.llm?.temperature ?? 0.7,
      max_tokens: cfg?.llm?.max_tokens ?? 4096,
      soul: soul.soul,
      tools: cfg?.tools ?? [],
      fallback_models: cfg?.llm?.fallback_models ?? [],
      mcp_servers: cfg?.mcp_servers ?? [],
      allowed_agents: cfg?.allowed_agents ?? [],
      sources: cfg?.sources ?? [],
      max_tool_rounds: cfg?.max_tool_rounds ?? null,
      heartbeat_interval: cfg?.heartbeat?.interval ?? "30s",
      heartbeat_timeout: cfg?.heartbeat?.timeout ?? "90s",
      heartbeat_on_failure: cfg?.heartbeat?.on_failure ?? "restart",
      ollama_base_url: cfg?.llm?.ollama_base_url ?? null,
    });
    setEditId(id);
    setShowForm(true);
  }

  function closeForm() {
    setShowForm(false);
    setEditId(null);
    setSaveErr("");
  }

  function set(key: string, val: unknown) {
    setForm((f) => ({ ...f, [key]: val }));
  }

  function toggleTool(t: string) {
    setForm((f) => ({
      ...f,
      tools: f.tools.includes(t) ? f.tools.filter((x) => x !== t) : [...f.tools, t],
    }));
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setSaveErr("");
    try {
      // fallbackInput flushen falls User getippt aber nicht + gedrückt hat
      let finalForm = form;
      if (fallbackInput.trim()) {
        const v = fallbackInput.trim();
        const updated = form.fallback_models.includes(v) ? form.fallback_models : [...form.fallback_models, v];
        finalForm = { ...form, fallback_models: updated };
        setFallbackInput("");
      }
      // ollama_base_url aus WKS-Modellen ableiten falls nicht explizit gesetzt
      const allModels = [finalForm.model, ...finalForm.fallback_models];
      if (!finalForm.ollama_base_url) {
        for (const m of allModels) {
          const wks = wksModels.find(w => w.id === m);
          if (wks?.wks_base_url) { finalForm = { ...finalForm, ollama_base_url: wks.wks_base_url }; break; }
        }
      }
      if (editId) {
        await api.updateAgent(editId, finalForm);
        // Plugins separat speichern
        await api.pluginAgentSet(editId, agentPlugins).catch(() => {});
      } else {
        await (api as any).createAgent(finalForm);
        // Plugins nach Agent-Erstellung zuweisen
        if (agentPlugins.length > 0) {
          await api.pluginAgentSet(finalForm.id, agentPlugins).catch(() => {});
        }
      }
      closeForm();
      await load();
    } catch (e) {
      setSaveErr(e instanceof Error ? e.message : t("common.error"));
    } finally {
      setSaving(false);
    }
  }

  function handleDelete(id: string) {
    setConfirmState({
      title: t("confirm.titleDeactivate"),
      message: t("agents.deactivateConfirm", { id }),
      action: async () => {
        setDeleting(id);
        try {
          await api.deleteAgent(id);
          await load();
        } catch (e) {
          setError(e instanceof Error ? e.message : t("common.deleteError"));
        } finally {
          setDeleting(null);
        }
      },
    });
  }

  function openHbEdit(id: string, rt: AgentRuntime | null) {
    if (hbEditAgent === id) { setHbEditAgent(null); return; }
    setHbErr("");
    setHbForm({
      enabled:    rt?.heartbeat_enabled ?? true,
      interval:   rt ? `${Math.round(rt.heartbeat_interval)}s` : "30s",
      timeout:    rt ? `${Math.round(rt.heartbeat_timeout)}s` : "90s",
      on_failure: rt?.on_failure ?? "restart",
    });
    setHbEditAgent(id);
  }

  async function saveHbForm(id: string) {
    setHbSaving(true);
    setHbErr("");
    try {
      await api.patchAgentHeartbeat(id, hbForm);
      setHbEditAgent(null);
      await load();
    } catch (e) {
      setHbErr(e instanceof Error ? e.message : t("common.error"));
    } finally {
      setHbSaving(false);
    }
  }

  const agentList = Object.entries(agents).filter(([id]) => !id.startsWith("personal_"));
  const TYPE_ORDER = ["boss", "worker", "specialist"];
  const agentGroups = useMemo(() => {
    const q = agentSearch.toLowerCase();
    const filtered = agentList.filter(([id, agent]) => {
      if (!q) return true;
      return id.toLowerCase().includes(q) || agent.config.identity.toLowerCase().includes(q) || agent.config.type.toLowerCase().includes(q);
    });
    const groups: Record<string, [string, AgentEntry][]> = {};
    for (const entry of filtered) {
      const type = entry[1].config.type || "specialist";
      if (!groups[type]) groups[type] = [];
      groups[type].push(entry);
    }
    for (const type of Object.keys(groups)) {
      groups[type].sort((a, b) => a[1].config.identity.localeCompare(b[1].config.identity, "de"));
    }
    return TYPE_ORDER.filter((t) => groups[t]?.length).map((t) => ({ type: t, entries: groups[t] }));
  }, [agentList, agentSearch]);
  const stats = useMemo(() => {
    const running = agentList.filter(([, agent]) => agent.runtime?.status === "running").length;
    const errors = agentList.filter(([, agent]) => agent.runtime?.status === "error").length;
    return [
      { label: t("agents.agentsLabel"), value: agentList.length, note: t("agents.registeredProfiles") },
      { label: t("agents.running"), value: running, note: t("agents.activeRuntime") },
      { label: t("agents.heartbeat"), value: hbTasks.length, note: t("agents.knownTasks") },
      { label: t("agents.errors"), value: errors, note: t("agents.runtimeErrors") },
    ];
  }, [agentList, hbTasks.length, t]);

  return (
    <div className="space-y-6">
      <section className="hero-panel">
        <div className="relative z-10 shell-grid">
          <div className="space-y-5 lg:col-span-8">
            <div className="flex flex-wrap items-center gap-3">
              <span className="status-pill status-pill-ok">
                <Radar className="h-3.5 w-3.5" />
                {agentList.length !== 1 ? t("agents.loadedPlural", { count: agentList.length }) : t("agents.loaded", { count: agentList.length })}
              </span>
              <span className="status-pill">
                <Cpu className="h-3.5 w-3.5" />
                {t("agents.runtimeFocus")}
              </span>
            </div>
            <div>
              <h1 className="shell-title">{t("agents.title")}</h1>
              <p className="shell-copy mt-3 max-w-2xl">{t("agents.subtitle")}</p>
            </div>
          </div>
          <div className="lg:col-span-4">
            <div className="app-panel app-panel-muted p-5">
              <div className="flex items-center gap-2 text-sm font-medium">
                <Workflow className="h-4 w-4 text-primary" />
                {t("agents.agentActions")}
              </div>
              <div className="mt-4 grid gap-2 sm:grid-cols-2">
                <button onClick={refresh} disabled={refreshing} className="inline-flex w-full items-center justify-center gap-2 rounded-2xl border bg-background/70 px-4 py-2 text-sm transition hover:bg-background disabled:opacity-50">
                  <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? "animate-spin" : ""}`} />
                  {t("agents.refresh")}
                </button>
                {isAdmin && (
                  <button onClick={openNew} className="inline-flex w-full items-center justify-center gap-2 rounded-2xl bg-primary px-4 py-2 text-sm text-primary-foreground transition hover:bg-primary/90">
                    <Plus className="h-3.5 w-3.5" />
                    {t("agents.newAgent")}
                  </button>
                )}
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {stats.map((item) => (
          <div key={item.label} className="metric-card">
            <p className="metric-kicker">{item.label}</p>
            <p className="metric-value">{String(item.value)}</p>
            <p className="metric-meta">{item.note}</p>
          </div>
        ))}
      </section>

      {error && <div className="app-panel border-destructive/20 bg-destructive/10 px-4 py-3 text-sm text-destructive">{error}</div>}

      {showForm && (
        <div className="fixed inset-0 z-50 flex">
          <div className="flex-1 bg-black/40 backdrop-blur-sm" onClick={closeForm} />
          <div className="w-full max-w-2xl bg-background border-l border-border/50 flex flex-col overflow-hidden shadow-2xl">
          <div className="flex items-center justify-between px-6 py-4 border-b border-border/40 flex-shrink-0">
            <div>
              <p className="text-xs text-muted-foreground uppercase tracking-wide">{t("agents.configuration")}</p>
              <h2 className="mt-1 text-lg font-semibold tracking-tight">{editId ? t("agents.editAgent", { id: editId }) : t("agents.newAgentTitle")}</h2>
            </div>
            <button onClick={closeForm} className="rounded-2xl border p-2 transition hover:bg-accent"><X className="h-4 w-4" /></button>
          </div>
          <div className="flex-1 overflow-y-auto px-6 py-5 space-y-5">
          <form id="agent-form" onSubmit={handleSave} className="space-y-5">
            <div className="grid gap-4 md:grid-cols-2">
              <Field label={t("agents.agentId")} hint={t("agents.agentIdHint")}>
                <Input value={form.id} onChange={(e) => set("id", e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, ""))} placeholder={t("agents.agentPlaceholder")} required disabled={!!editId} />
              </Field>
              <Field label={t("agents.displayName")}>
                <Input value={form.identity} onChange={(e) => set("identity", e.target.value)} placeholder={t("agents.identityPlaceholder")} required />
              </Field>
              <Field label={t("agents.type")}>
                <select value={form.type} onChange={(e) => set("type", e.target.value)} className="w-full rounded-2xl border bg-background px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary">
                  <option value="specialist">specialist</option>
                  <option value="boss">boss</option>
                  <option value="worker">worker</option>
                </select>
              </Field>
              <Field label={t("agents.llmModel")}>
                <select value={form.model} onChange={(e) => set("model", e.target.value)} className="w-full rounded-2xl border bg-background px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary">
                  {[...new Set([...KNOWN_MODELS, form.model].filter(Boolean))].map((m) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                </select>
              </Field>
            </div>

            {editId && (
              <Field label={t("agents.fallbackModels")} hint={t("agents.fallbackModelsHint")}>
                <div className="space-y-2">
                  <div className="flex min-h-7 flex-wrap gap-1.5">
                    {form.fallback_models.length === 0 ? (
                      <span className="self-center text-xs italic text-muted-foreground">{t("agents.noFallback")}</span>
                    ) : (
                      form.fallback_models.map((m, i) => (
                        <span key={m} className="flex items-center gap-1 rounded-full bg-muted px-2 py-1 font-mono text-xs">
                          <span className="text-muted-foreground">{i + 1}.</span>
                          {m}
                          <button type="button" onClick={() => set("fallback_models", form.fallback_models.filter((x) => x !== m))} className="ml-0.5 text-muted-foreground hover:text-destructive">
                            <X className="h-3 w-3" />
                          </button>
                        </span>
                      ))
                    )}
                  </div>
                  <div className="flex flex-col gap-2 sm:flex-row">
                    <input
                      value={fallbackInput}
                      onChange={(e) => setFallbackInput(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") {
                          e.preventDefault();
                          const v = fallbackInput.trim();
                          if (v && !form.fallback_models.includes(v)) set("fallback_models", [...form.fallback_models, v]);
                          setFallbackInput("");
                        }
                      }}
                      list="fallback-suggestions"
                      placeholder="z.B. claude-haiku-4-5-20251001"
                      className="flex-1 rounded-2xl border bg-background px-3 py-2.5 font-mono text-sm focus:outline-none focus:ring-2 focus:ring-primary"
                    />
                    <datalist id="fallback-suggestions">
                      {[...KNOWN_MODELS, ...wksModels.map(m => m.id)]
                        .filter((m) => m !== form.model && !form.fallback_models.includes(m))
                        .map((m) => <option key={m} value={m} label={wksModels.find(w=>w.id===m)?.label} />)}
                    </datalist>
                    <button type="button" onClick={() => {
                      const v = fallbackInput.trim();
                      if (v && !form.fallback_models.includes(v)) set("fallback_models", [...form.fallback_models, v]);
                      setFallbackInput("");
                    }} disabled={!fallbackInput.trim()} className="rounded-2xl border px-3 py-2 text-sm transition hover:bg-accent disabled:opacity-40 sm:self-start">
                      <Plus className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>
              </Field>
            )}

            <div className="grid gap-4 md:grid-cols-2">
              <Field label={t("agents.temperature")}>
                <Input type="number" value={form.temperature} onChange={(e) => set("temperature", parseFloat(e.target.value))} min={0} max={2} step={0.1} />
              </Field>
              <Field label={t("agents.maxTokens")}>
                <Input type="number" value={form.max_tokens} onChange={(e) => set("max_tokens", parseInt(e.target.value))} min={256} max={32000} step={256} />
              </Field>
            </div>

            <div>
              <p className="metric-kicker mb-3">{t("agents.heartbeatSection")}</p>
              <div className="grid gap-4 md:grid-cols-3">
                <Field label={t("agents.interval")}><Input value={form.heartbeat_interval} onChange={(e) => set("heartbeat_interval", e.target.value)} placeholder="30s" /></Field>
                <Field label={t("agents.timeout")}><Input value={form.heartbeat_timeout} onChange={(e) => set("heartbeat_timeout", e.target.value)} placeholder="90s" /></Field>
                <Field label={t("agents.onFailure")}>
                  <select value={form.heartbeat_on_failure} onChange={(e) => set("heartbeat_on_failure", e.target.value)} className="w-full rounded-2xl border bg-background px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary">
                    <option value="restart">restart</option>
                    <option value="stop">stop</option>
                    <option value="alert">alert</option>
                  </select>
                </Field>
              </div>
            </div>

            <div>
              <div className="flex items-center gap-3 mb-3">
                <p className="metric-kicker">{t("agents.tools")}</p>
                <button type="button" onClick={() => setForm(f => ({ ...f, tools: knownTools.filter(t => !DANGER_TOOLS.has(t)) }))}
                  className="text-xs text-muted-foreground hover:text-foreground transition">
                  Alle außer ⚠
                </button>
                <button type="button" onClick={() => setForm(f => ({ ...f, tools: [] }))}
                  className="text-xs text-muted-foreground hover:text-foreground transition">
                  Keine
                </button>
              </div>
              <ToolGroupSelector
                selectedTools={form.tools}
                onChange={(tools) => setForm(f => ({ ...f, tools }))}
              />
            </div>

            <div className="grid gap-4 md:grid-cols-2">
              <Field label="Max Tool Rounds" hint="Maximale Anzahl Tool-Aufrufe pro Nachricht (leer = Standard)">
                <Input
                  type="number"
                  value={form.max_tool_rounds ?? ""}
                  onChange={(e) => set("max_tool_rounds", e.target.value === "" ? null : parseInt(e.target.value))}
                  min={1} max={50} placeholder="Standard (6)"
                />
              </Field>
            </div>

            {agentList.filter(([aid]) => aid !== editId).length > 0 && (
              <div>
                <div className="flex items-center gap-3 mb-3">
                  <Bot className="h-4 w-4 text-muted-foreground" />
                  <p className="metric-kicker">Erlaubte Agenten</p>
                  <button type="button" onClick={() => set("allowed_agents", agentList.filter(([aid]) => aid !== editId).map(([aid]) => aid))}
                    className="text-xs text-muted-foreground hover:text-foreground transition">
                    Alle
                  </button>
                  <button type="button" onClick={() => set("allowed_agents", [])}
                    className="text-xs text-muted-foreground hover:text-foreground transition">
                    Keine
                  </button>
                </div>
                <p className="text-xs text-muted-foreground mb-2">Welche Agenten darf dieser Agent via ask_agent / delegate_agent ansprechen?</p>
                <div className="rounded-xl border border-border/60 bg-card p-3 space-y-1.5 max-h-[200px] overflow-y-auto">
                  {agentList.filter(([aid]) => aid !== editId).map(([aid, ag]) => (
                    <label key={aid} className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm cursor-pointer transition-colors hover:bg-muted/60">
                      <input
                        type="checkbox"
                        checked={form.allowed_agents.includes(aid)}
                        onChange={() => set("allowed_agents", form.allowed_agents.includes(aid) ? form.allowed_agents.filter((x: string) => x !== aid) : [...form.allowed_agents, aid])}
                        className="h-4 w-4 rounded accent-primary"
                      />
                      <span className="text-foreground">{ag.config.identity || aid}</span>
                      <span className="text-xs text-muted-foreground font-mono ml-auto">{aid}</span>
                    </label>
                  ))}
                </div>
              </div>
            )}

            {isAdmin && mcpServers.length > 0 && (
              <div>
                <div className="flex items-center gap-2 mb-3">
                  <ServerIcon className="h-4 w-4 text-muted-foreground" />
                  <p className="metric-kicker">{t("agents.mcpServers")}</p>
                </div>
                <div className="rounded-xl border border-border/60 bg-card p-3 space-y-1.5">
                  {mcpServers.map((s) => (
                    <label key={s.id} className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm cursor-pointer transition-colors hover:bg-muted/60">
                      <input
                        type="checkbox"
                        checked={form.mcp_servers.includes(s.id)}
                        onChange={() => set("mcp_servers", form.mcp_servers.includes(s.id) ? form.mcp_servers.filter((x: string) => x !== s.id) : [...form.mcp_servers, s.id])}
                        className="h-4 w-4 rounded accent-primary"
                      />
                      <span className="text-foreground">{s.name}</span>
                      <span className="text-xs text-muted-foreground font-mono ml-auto">{s.transport}</span>
                    </label>
                  ))}
                </div>
              </div>
            )}

            {isAdmin && plugins.length > 0 && (
              <div>
                <div className="flex items-center gap-2 mb-3">
                  <Puzzle className="h-4 w-4 text-muted-foreground" />
                  <p className="metric-kicker">Plugins</p>
                </div>
                <div className="rounded-xl border border-border/60 bg-card p-3 space-y-1.5">
                  {plugins.map((p) => (
                    <label key={p.id} className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm cursor-pointer transition-colors hover:bg-muted/60">
                      <input
                        type="checkbox"
                        checked={agentPlugins.includes(p.id)}
                        onChange={() => setAgentPlugins(prev => prev.includes(p.id) ? prev.filter(x => x !== p.id) : [...prev, p.id])}
                        className="h-4 w-4 rounded accent-primary"
                      />
                      <span className="text-foreground">{p.name}</span>
                      {p.tools.length > 0 && (
                        <span className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground ml-auto">
                          {p.tools.length} tools
                        </span>
                      )}
                    </label>
                  ))}
                </div>
              </div>
            )}

            <div>
              <p className="metric-kicker mb-3">Quellen & Suchmaschinen</p>
              <div className="space-y-2">
                {(form.sources as { name: string; url: string; description: string }[]).map((src, i) => (
                  <div key={i} className="flex gap-2 items-start rounded-xl border border-border/60 bg-muted/20 p-2">
                    <div className="flex-1 grid grid-cols-1 gap-1.5 min-w-0">
                      <input
                        value={src.name}
                        onChange={(e) => {
                          const s = [...form.sources]; s[i] = { ...s[i], name: e.target.value }; set("sources", s);
                        }}
                        placeholder="Name (z.B. Unreal Docs)"
                        className="rounded-lg border bg-background px-2.5 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-primary"
                      />
                      <input
                        value={src.url}
                        onChange={(e) => {
                          const s = [...form.sources]; s[i] = { ...s[i], url: e.target.value }; set("sources", s);
                        }}
                        placeholder="https://..."
                        className="rounded-lg border bg-background px-2.5 py-1.5 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-primary"
                      />
                      <input
                        value={src.description}
                        onChange={(e) => {
                          const s = [...form.sources]; s[i] = { ...s[i], description: e.target.value }; set("sources", s);
                        }}
                        placeholder="Beschreibung (optional)"
                        className="rounded-lg border bg-background px-2.5 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-primary"
                      />
                    </div>
                    <button
                      type="button"
                      onClick={() => set("sources", (form.sources as any[]).filter((_: any, j: number) => j !== i))}
                      className="mt-1 rounded-lg p-1.5 text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors"
                    >✕</button>
                  </div>
                ))}
                <button
                  type="button"
                  onClick={() => set("sources", [...form.sources, { name: "", url: "", description: "" }])}
                  className="w-full rounded-xl border border-dashed border-border/60 py-2 text-xs text-muted-foreground hover:bg-accent/30 transition-colors"
                >+ Quelle hinzufügen</button>
              </div>
            </div>

            <Field label={t("agents.soul")} hint={t("agents.soulHint")}>
              <textarea
                value={form.soul}
                onChange={(e) => set("soul", e.target.value)}
                rows={6}
                placeholder={`# ${form.identity || "Agent"}\n\nDu bist ein spezialisierter KI-Agent...`}
                className="w-full resize-none rounded-2xl border bg-background px-3 py-2.5 font-mono text-sm focus:outline-none focus:ring-2 focus:ring-primary"
              />
            </Field>

            {saveErr && <p className="text-sm text-destructive">{saveErr}</p>}
          </form>
          </div>
          <div className="flex-shrink-0 border-t border-border/40 px-6 py-4 flex gap-2 justify-end">
            <button type="button" onClick={closeForm} className="rounded-2xl border px-4 py-2 text-sm transition hover:bg-accent">{t("agents.cancel")}</button>
            <button form="agent-form" type="submit" disabled={saving} className="inline-flex items-center gap-2 rounded-2xl bg-primary px-4 py-2 text-sm text-primary-foreground transition hover:bg-primary/90 disabled:opacity-50">
              <Save className="h-3.5 w-3.5" />
              {saving ? t("agents.saving") : editId ? t("common.save") : t("agents.newAgent")}
            </button>
          </div>
          </div>
        </div>
      )}

      {loading && (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {[1, 2, 3].map((i) => <div key={i} className="metric-card h-36 animate-pulse" />)}
        </div>
      )}

      {!loading && agentList.length === 0 && !showForm && (
        <div className="section-card py-14 text-center">
          <Bot className="mx-auto h-10 w-10 text-muted-foreground" />
          <p className="mt-4 text-sm text-muted-foreground">{t("agents.noRuntime")}</p>
          {isAdmin && <button onClick={openNew} className="mt-4 inline-flex items-center gap-2 rounded-2xl bg-primary px-4 py-2 text-sm text-primary-foreground transition hover:bg-primary/90"><Plus className="h-4 w-4" />{t("agents.newAgent")}</button>}
        </div>
      )}

      {!loading && agentList.length > 0 && (
        <section className="section-card overflow-hidden p-0">
          <div className="p-4 border-b border-border/40">
            <div className="mb-6 rounded-xl border bg-muted/30 p-4 space-y-2">
              <h3 className="text-sm font-semibold">{t("agents.infoTitle")}</h3>
              <p className="text-xs text-muted-foreground leading-relaxed">{t("agents.infoText")}</p>
            </div>
          </div>
          {/* Suchleiste */}
          <div className="flex items-center gap-3 px-4 py-2.5 border-b border-border/40">
            <Search className="h-3.5 w-3.5 text-muted-foreground flex-shrink-0" />
            <input
              value={agentSearch}
              onChange={(e) => setAgentSearch(e.target.value)}
              placeholder={`${agentList.length} Agenten durchsuchen…`}
              className="flex-1 bg-transparent text-sm focus:outline-none placeholder:text-muted-foreground"
            />
            {agentSearch && (
              <button onClick={() => setAgentSearch("")} className="text-muted-foreground hover:text-foreground">
                <X className="h-3.5 w-3.5" />
              </button>
            )}
          </div>
          <div className="divide-y">
            {agentGroups.flatMap(({ type, entries }) => [
              <div key={`group-${type}`} className="px-4 py-1.5 bg-muted/30 border-b border-border/30">
                <span className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">{type}</span>
                <span className="ml-2 text-xs text-muted-foreground opacity-60">{entries.length}</span>
              </div>,
              ...entries.map(([id, agent]) => {
              const rt = agent.runtime;
              const status = rt?.status ?? "unbekannt";
              const color = STATUS_COLORS[status] ?? "text-muted-foreground";
              const hbAge = rt?.last_heartbeat_age;
              const hbWarn = hbAge != null && hbAge > (rt?.heartbeat_timeout ?? 90) * 0.8;
              const taskCount = hbTasks.filter((t) => t.agent_id === id).length;
              const expanded = skillsAgent === id || hbEditAgent === id || logAgent === id;
              return (
                <div key={id}>
                  {/* Kompakte Zeile */}
                  {(() => {
                    const cat = agentCategory(id, agent.config.type);
                    const colors = AGENT_COLORS[cat];
                    return (
                  <div className={`flex items-center gap-3 px-4 py-3 transition-colors hover:brightness-95 dark:hover:brightness-110 ${colors.bg} border-l-2 ${colors.border}`}>
                    {/* Status-Dot */}
                    <Circle className={`h-2.5 w-2.5 flex-shrink-0 fill-current ${color}`} />

                    {/* Name + Badges */}
                    <div className="flex-1 min-w-0">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span className="font-medium text-sm truncate">{agent.config.identity}</span>
                        <span className="rounded-full bg-secondary px-1.5 py-0.5 text-xs text-muted-foreground font-mono">{id}</span>
                        <span className={`rounded-full px-1.5 py-0.5 text-xs font-medium ${colors.badge}`}>{colors.label}</span>
                        {taskCount > 0 && <span className="rounded-full bg-primary/10 text-primary px-1.5 py-0.5 text-xs flex items-center gap-1"><Timer className="h-2.5 w-2.5" />{taskCount}</span>}
                        {rt?.status === "error" && <span className="rounded-full bg-destructive/10 text-destructive px-1.5 py-0.5 text-xs">error</span>}
                      </div>
                      <div className="flex items-center gap-3 mt-0.5">
                        <span className="text-xs text-muted-foreground truncate max-w-[14rem]">{agent.config.model}</span>
                        {rt && <span className={`text-xs ${hbWarn ? "text-orange-500" : "text-muted-foreground"}`}>HB {hbAge?.toFixed(0)}s</span>}
                        {rt && rt.restart_count > 0 && <span className="text-xs text-muted-foreground">{rt.restart_count}x restart</span>}
                      </div>
                    </div>

                    {/* Action Icons */}
                    <div className="flex items-center gap-1 flex-shrink-0">
                      <button onClick={() => navigate(`/agents/${id}/chat`)} title={t("agents.chat")}
                        className="p-1.5 rounded-lg hover:bg-accent transition-colors text-muted-foreground hover:text-foreground">
                        <MessageSquare className="h-3.5 w-3.5" />
                      </button>
                      <button onClick={() => setSkillsAgent((s) => s === id ? null : id)} title={t("agents.skills")}
                        className={`p-1.5 rounded-lg transition-colors ${skillsAgent === id ? "bg-primary/10 text-primary" : "hover:bg-accent text-muted-foreground hover:text-foreground"}`}>
                        <BookOpen className="h-3.5 w-3.5" />
                      </button>
                      <button onClick={() => openLogs(id)} title={t("agents.logs")}
                        className={`p-1.5 rounded-lg transition-colors ${logAgent === id ? "bg-primary/10 text-primary" : "hover:bg-accent text-muted-foreground hover:text-foreground"}`}>
                        <ScrollText className="h-3.5 w-3.5" />
                      </button>
                      {isAdmin && (
                        <button onClick={() => openHbEdit(id, rt)} title={t("agents.heartbeat")}
                          className={`p-1.5 rounded-lg transition-colors ${hbEditAgent === id ? "bg-primary/10 text-primary" : "hover:bg-accent text-muted-foreground hover:text-foreground"}`}>
                          <Activity className="h-3.5 w-3.5" />
                        </button>
                      )}
                      {isAdmin && (
                        <button onClick={() => openEdit(id, agent)} title={t("agents.editBtn")}
                          className="p-1.5 rounded-lg hover:bg-accent transition-colors text-muted-foreground hover:text-foreground">
                          <Pencil className="h-3.5 w-3.5" />
                        </button>
                      )}
                      {isAdmin && (
                        <button onClick={() => handleDelete(id)} disabled={deleting === id} title={t("common.delete")}
                          className="p-1.5 rounded-lg hover:bg-destructive/10 transition-colors text-muted-foreground hover:text-destructive disabled:opacity-50">
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      )}
                    </div>
                  </div>
                    );
                  })()}

                  {/* Aufklappbare Panels */}
                  {skillsAgent === id && <div className="border-t"><SkillsPanel agentId={id} /></div>}
                  {logAgent === id && (
                    <div className="border-t">
                      <div className="flex items-center justify-between bg-muted/30 px-4 py-2">
                        <span className="flex items-center gap-2 text-xs font-medium"><ScrollText className="h-3.5 w-3.5" />{t("agents.logs")} — {id} <span className="inline-flex items-center gap-1 text-green-500"><span className="h-1.5 w-1.5 animate-pulse rounded-full bg-green-500" />live</span></span>
                        <button onClick={closeLogs} className="p-1 rounded hover:bg-accent"><X className="h-3.5 w-3.5" /></button>
                      </div>
                      {logErr ? <p className="p-4 text-sm text-destructive">{logErr}</p> : (
                        <div className="h-52 overflow-y-auto bg-[#0d0d0d] px-4 py-3 font-mono text-xs leading-relaxed text-[#d4d4d4]">
                          {logLines.length === 0 ? <span className="text-muted-foreground">{t("agents.noLogs")}</span> : logLines.map((line, i) => (
                            <div key={i} className={`whitespace-pre-wrap break-all ${line.includes(" ERROR ") || line.includes(" error ") ? "text-red-400" : line.includes(" WARNING ") ? "text-yellow-400" : ""}`}>{line}</div>
                          ))}
                          <div ref={logBottomRef} />
                        </div>
                      )}
                    </div>
                  )}
                  {hbEditAgent === id && (
                    <div className="border-t px-4 py-4 space-y-3 bg-muted/10">
                      <div className="flex items-center justify-between">
                        <p className="text-xs font-medium flex items-center gap-2"><Activity className="h-3.5 w-3.5" />{t("agents.heartbeatEdit")}</p>
                        <button onClick={() => setHbEditAgent(null)} className="p-1 rounded hover:bg-accent"><X className="h-3.5 w-3.5" /></button>
                      </div>
                      <div className="flex items-center gap-3">
                        <label className="flex items-center gap-2 cursor-pointer text-sm">
                          <input type="checkbox" checked={hbForm.enabled} onChange={(e) => setHbForm((f) => ({ ...f, enabled: e.target.checked }))} className="h-4 w-4 rounded" />
                          {t("agents.heartbeatEnabled")}
                        </label>
                      </div>
                      <div className="grid gap-3 sm:grid-cols-3">
                        {[["interval", t("agents.interval"), "30s"], ["timeout", t("agents.timeout"), "90s"]].map(([k, label, ph]) => (
                          <div key={k} className="space-y-1">
                            <label className="text-xs text-muted-foreground uppercase tracking-wide">{label}</label>
                            <input value={(hbForm as any)[k]} onChange={(e) => setHbForm((f) => ({ ...f, [k]: e.target.value }))} placeholder={ph}
                              className="w-full rounded-xl border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary" />
                          </div>
                        ))}
                        <div className="space-y-1">
                          <label className="text-xs text-muted-foreground uppercase tracking-wide">{t("agents.onFailure")}</label>
                          <select value={hbForm.on_failure} onChange={(e) => setHbForm((f) => ({ ...f, on_failure: e.target.value }))}
                            className="w-full rounded-xl border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary">
                            <option value="restart">restart</option>
                            <option value="stop">stop</option>
                            <option value="alert">alert</option>
                            <option value="ignore">ignore</option>
                          </select>
                        </div>
                      </div>
                      {hbErr && <p className="text-sm text-destructive">{hbErr}</p>}
                      <button onClick={() => saveHbForm(id)} disabled={hbSaving}
                        className="inline-flex items-center gap-2 rounded-xl bg-primary px-3 py-1.5 text-sm text-primary-foreground hover:bg-primary/90 disabled:opacity-50">
                        <Save className="h-3.5 w-3.5" />{hbSaving ? t("agents.hbSaving") : t("agents.saveHeartbeat")}
                      </button>
                    </div>
                  )}
                </div>
              );
            })
            ])}
          </div>
        </section>
      )}

      {logAgent && (
        <section className="section-card overflow-hidden p-0">
          <div className="flex items-center justify-between border-b bg-muted/30 px-5 py-3">
            <div className="flex items-center gap-2">
              <ScrollText className="h-4 w-4 text-muted-foreground" />
              <span className="text-sm font-medium">{t("agents.logs")} — {logAgent}</span>
              <span className="inline-flex items-center gap-1 text-xs text-green-500"><span className="h-1.5 w-1.5 animate-pulse rounded-full bg-green-500" />{t("dashboard.liveLabel")}</span>
            </div>
            <button onClick={closeLogs} className="rounded-xl p-2 text-muted-foreground transition hover:bg-accent hover:text-foreground"><X className="h-4 w-4" /></button>
          </div>
          {logErr ? (
            <p className="p-4 text-sm text-destructive">{logErr}</p>
          ) : (
            <div className="h-72 overflow-y-auto bg-[#0d0d0d] p-4 font-mono text-xs leading-relaxed text-[#d4d4d4]">
              {logLines.length === 0 ? (
                <span className="text-muted-foreground">{t("agents.noLogs")}</span>
              ) : (
                logLines.map((line, i) => (
                  <div key={i} className={`whitespace-pre-wrap break-all ${line.includes(" ERROR ") || line.includes(" error ") ? "text-red-400" : line.includes(" WARNING ") || line.includes(" warning ") ? "text-yellow-400" : "text-[#d4d4d4]"}`}>
                    {line}
                  </div>
                ))
              )}
              <div ref={logBottomRef} />
            </div>
          )}
        </section>
      )}
    <ConfirmDialog
      open={!!confirmState}
      title={confirmState?.title || ""}
      message={confirmState?.message || ""}
      onConfirm={() => { confirmState?.action(); setConfirmState(null); }}
      onCancel={() => setConfirmState(null)}
      variant="danger"
    />
    </div>
  );
}

// ── Tab shell ─────────────────────────────────────────────────────────────────

type AgentsTabId = "agents" | "tools" | "plugins" | "federation" | "blueprint";

const AGENTS_TABS: { id: AgentsTabId; labelKey: string; icon: React.ElementType }[] = [
  { id: "agents",     labelKey: "agents.tabAgents",     icon: Bot },
  { id: "tools",      labelKey: "agents.tabTools",      icon: Wrench },
  { id: "plugins",    labelKey: "agents.tabPlugins",    icon: Puzzle },
  { id: "federation", labelKey: "agents.tabFederation", icon: Globe },
  { id: "blueprint",  labelKey: "agents.tabBlueprint",  icon: Workflow },
];

export function AgentsPage() {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState<AgentsTabId>("agents");

  return (
    <div className="flex flex-col h-full">
      <div className="px-6 pt-6 pb-0 border-b border-border">
        <div className="flex gap-1 overflow-x-auto scrollbar-none pb-px">
          {AGENTS_TABS.map(tab => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={cn(
                "flex items-center gap-2 px-4 py-2.5 text-sm font-medium rounded-t-lg transition-colors border-b-2 -mb-px",
                activeTab === tab.id
                  ? "border-primary text-foreground bg-background"
                  : "border-transparent text-muted-foreground hover:text-foreground hover:bg-muted"
              )}
            >
              <tab.icon size={14} />
              {t(tab.labelKey, { defaultValue: tab.id })}
            </button>
          ))}
        </div>
      </div>

      <div className="flex-1 overflow-auto">
        {activeTab === "agents"     && <div className="p-6"><AgentsCrudTab /></div>}
        {activeTab === "tools"      && <ToolsPage />}
        {activeTab === "plugins"    && <PluginsPage />}
        {activeTab === "federation" && <A2APage />}
        {activeTab === "blueprint"  && <AgentBlueprintTab />}
      </div>
    </div>
  );
}
