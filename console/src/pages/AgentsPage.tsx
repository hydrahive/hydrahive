import { useEffect, useMemo, useRef, useState } from "react";
import { Bot, RefreshCw, Circle, Plus, X, Save, Trash2, Pencil, ScrollText, BookOpen, Timer, MessageSquare, ShieldAlert, Radar, Workflow, Cpu } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { api, HeartbeatTaskStatus, McpServer } from "@/lib/api";
import { SkillsPanel } from "@/components/SkillsPanel";
import { useAuth } from "@/hooks/useAuth";

interface AgentRuntime {
  status: string;
  type: string;
  restart_count: number;
  last_heartbeat_age: number;
  heartbeat_timeout: number;
  on_failure: string;
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
  heartbeat_interval: "30s",
  heartbeat_timeout: "90s",
  heartbeat_on_failure: "restart",
};

const KNOWN_TOOLS = ["file_read", "file_write", "web_search", "http_request", "dispatch_task", "spawn_agent", "git_status", "git_diff", "gitea_repo_inspect", "gitea_repo_tree", "gitea_repo_file", "gitea_repo_commits"];
const KNOWN_MODELS = ["llama3.2:3b", "llama3.1:8b", "mistral-nemo:12b", "claude-sonnet-4-20250514", "gpt-4o"];
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

export function AgentsPage() {
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
  const [logAgent, setLogAgent] = useState<string | null>(null);
  const [logLines, setLogLines] = useState<string[]>([]);
  const [logErr, setLogErr] = useState("");
  const [hbTasks, setHbTasks] = useState<HeartbeatTaskStatus[]>([]);
  const [mcpServers, setMcpServers] = useState<McpServer[]>([]);
  const logBottomRef = useRef<HTMLDivElement>(null);
  const logIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  async function load() {
    try {
      const [agentsData, hbData, mcpData] = await Promise.allSettled([
        api.agents() as Promise<Record<string, AgentEntry>>,
        api.heartbeatTasks(),
        api.mcpServers(),
      ]);
      if (agentsData.status === "fulfilled") setAgents(agentsData.value);
      if (hbData.status === "fulfilled") setHbTasks(hbData.value.tasks);
      if (mcpData.status === "fulfilled") setMcpServers(mcpData.value.servers);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Fehler");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  useEffect(() => {
    load();
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
      setLogErr(e instanceof Error ? e.message : "Fehler beim Laden");
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
    setShowForm(true);
  }

  async function openEdit(id: string, entry: AgentEntry) {
    setSaveErr("");
    const [full, soul] = await Promise.all([
      api.get<{ config: Record<string, unknown> }>(`/agents/${id}`).catch(() => null),
      api.getAgentSoul(id).catch(() => ({ soul: "", exists: false })),
    ]);
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
      heartbeat_interval: cfg?.heartbeat?.interval ?? "30s",
      heartbeat_timeout: cfg?.heartbeat?.timeout ?? "90s",
      heartbeat_on_failure: cfg?.heartbeat?.on_failure ?? "restart",
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
      if (editId) {
        await api.updateAgent(editId, form);
      } else {
        await (api as any).createAgent(form);
      }
      closeForm();
      await load();
    } catch (e) {
      setSaveErr(e instanceof Error ? e.message : "Fehler");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(id: string) {
    if (!confirm(`Agent "${id}" deaktivieren?`)) return;
    setDeleting(id);
    try {
      await api.deleteAgent(id);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Fehler beim Loeschen");
    } finally {
      setDeleting(null);
    }
  }

  const agentList = Object.entries(agents).filter(([id]) => !id.startsWith("personal_"));
  const stats = useMemo(() => {
    const running = agentList.filter(([, agent]) => agent.runtime?.status === "running").length;
    const errors = agentList.filter(([, agent]) => agent.runtime?.status === "error").length;
    return [
      { label: "Agenten", value: agentList.length, note: "Registrierte Agentprofile" },
      { label: "Running", value: running, note: "Aktive Runtime-Prozesse" },
      { label: "Heartbeat", value: hbTasks.length, note: "Bekannte Heartbeat-Tasks" },
      { label: "Fehler", value: errors, note: "Agenten mit Runtime-Fehler" },
    ];
  }, [agentList, hbTasks.length]);

  return (
    <div className="space-y-6">
      <section className="hero-panel">
        <div className="relative z-10 shell-grid">
          <div className="space-y-5 lg:col-span-8">
            <div className="flex flex-wrap items-center gap-3">
              <span className="status-pill status-pill-ok">
                <Radar className="h-3.5 w-3.5" />
                {agentList.length} Agent{agentList.length !== 1 ? "en" : ""} geladen
              </span>
              <span className="status-pill">
                <Cpu className="h-3.5 w-3.5" />
                Runtime, Heartbeats und Skills zentral im Blick
              </span>
            </div>
            <div>
              <h1 className="shell-title">Agentenverwaltung mit Runtime-, Skill- und Log-Fokus</h1>
              <p className="shell-copy mt-3 max-w-2xl">
                Diese Flaeche ordnet Agentprofile, Heartbeats, direkte Chats, Skills und Logs in eine klarere Betriebsansicht.
                Die Funktionen bleiben gleich, aber die Oberflaeche trennt Analyse, Konfiguration und Laufzeit jetzt sauberer.
              </p>
            </div>
          </div>
          <div className="lg:col-span-4">
            <div className="app-panel app-panel-muted p-5">
              <div className="flex items-center gap-2 text-sm font-medium">
                <Workflow className="h-4 w-4 text-primary" />
                Agentaktionen
              </div>
              <div className="mt-4 flex flex-wrap gap-2">
                <button onClick={refresh} disabled={refreshing} className="inline-flex items-center gap-2 rounded-2xl border bg-background/70 px-4 py-2 text-sm transition hover:bg-background disabled:opacity-50">
                  <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? "animate-spin" : ""}`} />
                  Aktualisieren
                </button>
                {isAdmin && (
                  <button onClick={openNew} className="inline-flex items-center gap-2 rounded-2xl bg-primary px-4 py-2 text-sm text-primary-foreground transition hover:bg-primary/90">
                    <Plus className="h-3.5 w-3.5" />
                    Neuer Agent
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
        <section className="section-card space-y-5">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="metric-kicker">Konfiguration</p>
              <h2 className="mt-2 text-xl font-semibold tracking-tight">{editId ? `Agent bearbeiten: ${editId}` : "Neuen Agent anlegen"}</h2>
            </div>
            <button onClick={closeForm} className="rounded-2xl border p-2 transition hover:bg-accent"><X className="h-4 w-4" /></button>
          </div>
          <form onSubmit={handleSave} className="space-y-5">
            <div className="grid gap-4 md:grid-cols-2">
              <Field label="Agent-ID *" hint="Nur a-z, 0-9, _ und -">
                <Input value={form.id} onChange={(e) => set("id", e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, ""))} placeholder="z.B. steuer-agent" required disabled={!!editId} />
              </Field>
              <Field label="Anzeigename *">
                <Input value={form.identity} onChange={(e) => set("identity", e.target.value)} placeholder="z.B. Steuerbert" required />
              </Field>
              <Field label="Typ *">
                <select value={form.type} onChange={(e) => set("type", e.target.value)} className="w-full rounded-2xl border bg-background px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary">
                  <option value="specialist">specialist</option>
                  <option value="boss">boss</option>
                  <option value="worker">worker</option>
                </select>
              </Field>
              <Field label="LLM-Modell *">
                <select value={form.model} onChange={(e) => set("model", e.target.value)} className="w-full rounded-2xl border bg-background px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary">
                  {[...new Set([...KNOWN_MODELS, form.model].filter(Boolean))].map((m) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                </select>
              </Field>
            </div>

            {editId && (
              <Field label="Fallback-Modelle" hint="Bei Quota oder Overload wird automatisch das naechste Modell probiert">
                <div className="space-y-2">
                  <div className="flex min-h-7 flex-wrap gap-1.5">
                    {form.fallback_models.length === 0 ? (
                      <span className="self-center text-xs italic text-muted-foreground">Kein Fallback</span>
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
                  <div className="flex gap-2">
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
                      {["claude-haiku-4-5-20251001", "claude-sonnet-4-5-20251001", "claude-sonnet-4-20250514", "llama3.2", "llama3.1:8b", "mistral-nemo:12b"]
                        .filter((m) => m !== form.model && !form.fallback_models.includes(m))
                        .map((m) => <option key={m} value={m} />)}
                    </datalist>
                    <button type="button" onClick={() => {
                      const v = fallbackInput.trim();
                      if (v && !form.fallback_models.includes(v)) set("fallback_models", [...form.fallback_models, v]);
                      setFallbackInput("");
                    }} disabled={!fallbackInput.trim()} className="rounded-2xl border px-3 py-2 text-sm transition hover:bg-accent disabled:opacity-40">
                      <Plus className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>
              </Field>
            )}

            <div className="grid gap-4 md:grid-cols-2">
              <Field label="Temperature">
                <Input type="number" value={form.temperature} onChange={(e) => set("temperature", parseFloat(e.target.value))} min={0} max={2} step={0.1} />
              </Field>
              <Field label="Max Tokens">
                <Input type="number" value={form.max_tokens} onChange={(e) => set("max_tokens", parseInt(e.target.value))} min={256} max={32000} step={256} />
              </Field>
            </div>

            <div>
              <p className="metric-kicker mb-3">Heartbeat</p>
              <div className="grid gap-4 md:grid-cols-3">
                <Field label="Interval"><Input value={form.heartbeat_interval} onChange={(e) => set("heartbeat_interval", e.target.value)} placeholder="30s" /></Field>
                <Field label="Timeout"><Input value={form.heartbeat_timeout} onChange={(e) => set("heartbeat_timeout", e.target.value)} placeholder="90s" /></Field>
                <Field label="Bei Fehler">
                  <select value={form.heartbeat_on_failure} onChange={(e) => set("heartbeat_on_failure", e.target.value)} className="w-full rounded-2xl border bg-background px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary">
                    <option value="restart">restart</option>
                    <option value="stop">stop</option>
                    <option value="alert">alert</option>
                  </select>
                </Field>
              </div>
            </div>

            <div>
              <p className="metric-kicker mb-3">Tools</p>
              <div className="flex flex-wrap gap-2">
                {KNOWN_TOOLS.map((t) => (
                  <button key={t} type="button" onClick={() => toggleTool(t)} className={`rounded-full border px-3 py-1.5 text-xs transition ${form.tools.includes(t) ? "border-primary bg-primary text-primary-foreground" : "hover:bg-accent"}`}>
                    {t}
                  </button>
                ))}
              </div>
            </div>

            {isAdmin && mcpServers.length > 0 && (
              <div>
                <p className="metric-kicker mb-3">MCP-Server</p>
                <div className="flex flex-wrap gap-2">
                  {mcpServers.map((s) => (
                    <button
                      key={s.id}
                      type="button"
                      onClick={() => set("mcp_servers", form.mcp_servers.includes(s.id) ? form.mcp_servers.filter((x: string) => x !== s.id) : [...form.mcp_servers, s.id])}
                      className={`rounded-full border px-3 py-1.5 text-xs transition ${form.mcp_servers.includes(s.id) ? "border-primary bg-primary text-primary-foreground" : "hover:bg-accent"}`}
                    >
                      {s.name}
                    </button>
                  ))}
                </div>
              </div>
            )}

            <Field label="Soul (Persoenlichkeit)" hint="Markdown beschreibt Charakter und Kommunikationsstil des Agenten">
              <textarea
                value={form.soul}
                onChange={(e) => set("soul", e.target.value)}
                rows={6}
                placeholder={`# ${form.identity || "Agent"}\n\nDu bist ein spezialisierter KI-Agent...`}
                className="w-full resize-none rounded-2xl border bg-background px-3 py-2.5 font-mono text-sm focus:outline-none focus:ring-2 focus:ring-primary"
              />
            </Field>

            {saveErr && <p className="text-sm text-destructive">{saveErr}</p>}
            <div className="flex justify-end gap-2">
              <button type="button" onClick={closeForm} className="rounded-2xl border px-4 py-2 text-sm transition hover:bg-accent">Abbrechen</button>
              <button type="submit" disabled={saving} className="inline-flex items-center gap-2 rounded-2xl bg-primary px-4 py-2 text-sm text-primary-foreground transition hover:bg-primary/90 disabled:opacity-50">
                <Save className="h-3.5 w-3.5" />
                {saving ? "Speichern..." : editId ? "Aktualisieren" : "Agent anlegen"}
              </button>
            </div>
          </form>
        </section>
      )}

      {loading && (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {[1, 2, 3].map((i) => <div key={i} className="metric-card h-36 animate-pulse" />)}
        </div>
      )}

      {!loading && agentList.length === 0 && !showForm && (
        <div className="section-card py-14 text-center">
          <Bot className="mx-auto h-10 w-10 text-muted-foreground" />
          <p className="mt-4 text-sm text-muted-foreground">Keine Agenten. Leg den ersten an.</p>
          {isAdmin && <button onClick={openNew} className="mt-4 inline-flex items-center gap-2 rounded-2xl bg-primary px-4 py-2 text-sm text-primary-foreground transition hover:bg-primary/90"><Plus className="h-4 w-4" />Ersten Agent anlegen</button>}
        </div>
      )}

      {!loading && agentList.length > 0 && (
        <section className="space-y-4">
          {agentList.map(([id, agent]) => {
            const rt = agent.runtime;
            const status = rt?.status ?? "unbekannt";
            const color = STATUS_COLORS[status] ?? "text-muted-foreground";
            const hbAge = rt?.last_heartbeat_age;
            const hbWarn = hbAge != null && hbAge > (rt?.heartbeat_timeout ?? 90) * 0.8;
            const taskCount = hbTasks.filter((t) => t.agent_id === id).length;
            return (
              <div key={id} className="app-panel overflow-hidden">
                <div className="p-5">
                  <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
                    <div className="flex items-start gap-4">
                      <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-primary/12 text-primary">
                        <Bot className="h-5 w-5" />
                      </div>
                      <div className="space-y-2">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-lg font-semibold tracking-tight">{agent.config.identity}</span>
                          <span className="rounded-full bg-secondary px-2 py-1 text-xs text-secondary-foreground">{id}</span>
                          <span className="rounded-full bg-secondary px-2 py-1 text-xs text-secondary-foreground">{agent.config.type}</span>
                          {taskCount > 0 && <span className="status-pill status-pill-ok"><Timer className="h-3 w-3" />{taskCount} HB</span>}
                        </div>
                        <p className="text-sm text-muted-foreground">{agent.config.model}</p>
                        <div className="flex flex-wrap items-center gap-3 text-sm">
                          <span className={`inline-flex items-center gap-1.5 font-medium ${color}`}><Circle className="h-2 w-2 fill-current" />{status}</span>
                          {rt && (
                            <span className={hbWarn ? "text-orange-500" : "text-muted-foreground"}>
                              HB {hbAge?.toFixed(0)}s
                              {rt.restart_count > 0 && <span className="ml-1 text-orange-500">↺{rt.restart_count}</span>}
                            </span>
                          )}
                          {rt?.status === "error" && <span className="inline-flex items-center gap-1 text-destructive"><ShieldAlert className="h-4 w-4" />Fehlerzustand</span>}
                        </div>
                      </div>
                    </div>
                    <div className="flex flex-wrap gap-2 xl:justify-end">
                      <button onClick={() => navigate(`/agents/${id}/chat`)} className="inline-flex items-center gap-2 rounded-2xl border px-3 py-2 text-sm transition hover:bg-accent"><MessageSquare className="h-4 w-4" />Chat</button>
                      <button onClick={() => setSkillsAgent((s) => (s === id ? null : id))} className={`inline-flex items-center gap-2 rounded-2xl border px-3 py-2 text-sm transition ${skillsAgent === id ? "bg-primary/10 text-primary" : "hover:bg-accent"}`}><BookOpen className="h-4 w-4" />Skills</button>
                      <button onClick={() => openLogs(id)} className={`inline-flex items-center gap-2 rounded-2xl border px-3 py-2 text-sm transition ${logAgent === id ? "bg-primary/10 text-primary" : "hover:bg-accent"}`}><ScrollText className="h-4 w-4" />Logs</button>
                      {isAdmin && <button onClick={() => openEdit(id, agent)} className="inline-flex items-center gap-2 rounded-2xl border px-3 py-2 text-sm transition hover:bg-accent"><Pencil className="h-4 w-4" />Bearbeiten</button>}
                      {isAdmin && <button onClick={() => handleDelete(id)} disabled={deleting === id} className="inline-flex items-center gap-2 rounded-2xl border px-3 py-2 text-sm text-muted-foreground transition hover:border-destructive/20 hover:bg-destructive/10 hover:text-destructive disabled:opacity-50"><Trash2 className="h-4 w-4" />Loeschen</button>}
                    </div>
                  </div>
                </div>
                {skillsAgent === id && <SkillsPanel agentId={id} />}
              </div>
            );
          })}
        </section>
      )}

      {logAgent && (
        <section className="section-card overflow-hidden p-0">
          <div className="flex items-center justify-between border-b bg-muted/30 px-5 py-3">
            <div className="flex items-center gap-2">
              <ScrollText className="h-4 w-4 text-muted-foreground" />
              <span className="text-sm font-medium">Logs — {logAgent}</span>
              <span className="inline-flex items-center gap-1 text-xs text-green-500"><span className="h-1.5 w-1.5 animate-pulse rounded-full bg-green-500" />Live</span>
            </div>
            <button onClick={closeLogs} className="rounded-xl p-2 text-muted-foreground transition hover:bg-accent hover:text-foreground"><X className="h-4 w-4" /></button>
          </div>
          {logErr ? (
            <p className="p-4 text-sm text-destructive">{logErr}</p>
          ) : (
            <div className="h-72 overflow-y-auto bg-[#0d0d0d] p-4 font-mono text-xs leading-relaxed text-[#d4d4d4]">
              {logLines.length === 0 ? (
                <span className="text-muted-foreground">Lade Logs...</span>
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
    </div>
  );
}
