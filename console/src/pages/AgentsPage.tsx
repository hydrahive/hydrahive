import { useEffect, useRef, useState } from "react";
import { Bot, RefreshCw, Circle, Plus, X, Save, Trash2, Pencil, ScrollText, BookOpen, Timer, MessageSquare } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { api, HeartbeatTaskStatus } from "@/lib/api";
import { SkillsPanel } from "@/components/SkillsPanel";
import { useAuth } from "@/hooks/useAuth";

interface AgentRuntime {
  status: string; type: string; restart_count: number;
  last_heartbeat_age: number; heartbeat_timeout: number; on_failure: string;
}
interface AgentEntry {
  config: { type: string; identity: string; model: string };
  runtime: AgentRuntime | null;
}

const EMPTY_FORM = {
  id: "", type: "specialist", identity: "", model: "llama3.1:8b",
  temperature: 0.7, max_tokens: 4096, soul: "",
  tools: [] as string[], heartbeat_interval: "30s",
  heartbeat_timeout: "90s", heartbeat_on_failure: "restart",
};

const KNOWN_TOOLS = ["file_read","file_write","web_search","http_request","dispatch_task","spawn_agent"];
const KNOWN_MODELS = ["llama3.2:3b","llama3.1:8b","mistral-nemo:12b","claude-sonnet-4-20250514","gpt-4o"];
const STATUS_COLORS: Record<string,string> = {
  running:"text-green-500", starting:"text-yellow-500",
  restarting:"text-orange-500", stopped:"text-muted-foreground", error:"text-destructive",
};

function Field({ label, children, hint }: { label:string; children:React.ReactNode; hint?:string }) {
  return (
    <div className="space-y-1.5">
      <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">{label}</label>
      {children}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

function Input({ value, onChange, ...props }: React.InputHTMLAttributes<HTMLInputElement> & { value:string|number; onChange:(e:React.ChangeEvent<HTMLInputElement>)=>void }) {
  return <input value={value} onChange={onChange} {...props}
    className="w-full px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary" />;
}

export function AgentsPage() {
  const { isAdmin } = useAuth();
  const navigate = useNavigate();
  const [agents,    setAgents]    = useState<Record<string,AgentEntry>>({});
  const [loading,   setLoading]   = useState(true);
  const [error,     setError]     = useState("");
  const [refreshing,setRefreshing]= useState(false);
  const [showForm,  setShowForm]  = useState(false);
  const [editId,    setEditId]    = useState<string|null>(null);
  const [form,      setForm]      = useState({ ...EMPTY_FORM });
  const [saving,    setSaving]    = useState(false);
  const [saveErr,   setSaveErr]   = useState("");
  const [deleting,  setDeleting]  = useState<string|null>(null);
  const [skillsAgent, setSkillsAgent] = useState<string|null>(null);
  const [logAgent,  setLogAgent]  = useState<string|null>(null);
  const [logLines,  setLogLines]  = useState<string[]>([]);
  const [logErr,    setLogErr]    = useState("");
  const [hbTasks,   setHbTasks]   = useState<HeartbeatTaskStatus[]>([]);
  const logBottomRef = useRef<HTMLDivElement>(null);
  const logIntervalRef = useRef<ReturnType<typeof setInterval>|null>(null);

  async function load() {
    try {
      const [agentsData, hbData] = await Promise.allSettled([
        api.agents() as Promise<Record<string,AgentEntry>>,
        api.heartbeatTasks(),
      ]);
      if (agentsData.status === "fulfilled") setAgents(agentsData.value);
      if (hbData.status === "fulfilled") setHbTasks(hbData.value.tasks);
      setError("");
    } catch(e) { setError(e instanceof Error ? e.message : "Fehler"); }
    finally { setLoading(false); setRefreshing(false); }
  }

  useEffect(() => { load(); }, []);
  function refresh() { setRefreshing(true); load(); }

  async function fetchLogs(id: string) {
    try {
      const d = await api.agentLogs(id);
      setLogLines(d.lines);
      setLogErr("");
    } catch(e) { setLogErr(e instanceof Error ? e.message : "Fehler beim Laden"); }
  }

  function openLogs(id: string) {
    if (logAgent === id) { closeLogs(); return; }
    setLogAgent(id); setLogLines([]); setLogErr("");
    fetchLogs(id);
    if (logIntervalRef.current) clearInterval(logIntervalRef.current);
    logIntervalRef.current = setInterval(() => fetchLogs(id), 3000);
  }

  function closeLogs() {
    setLogAgent(null);
    if (logIntervalRef.current) { clearInterval(logIntervalRef.current); logIntervalRef.current = null; }
  }

  useEffect(() => {
    logBottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logLines]);

  useEffect(() => () => { if (logIntervalRef.current) clearInterval(logIntervalRef.current); }, []);

  async function openNew() {
    setForm({ ...EMPTY_FORM }); setEditId(null); setSaveErr(""); setShowForm(true);
  }

  async function openEdit(id: string, _entry: AgentEntry) {
    setSaveErr("");
    const [full, soul] = await Promise.all([
      api.get<{config: Record<string, unknown>}>(`/agents/${id}`).catch(() => null),
      api.getAgentSoul(id).catch(() => ({ soul:"", exists:false })),
    ]);
    const cfg = full?.config as any;
    setForm({
      id,
      type:                  cfg?.type                  ?? _entry.config.type,
      identity:              cfg?.identity              ?? _entry.config.identity,
      model:                 cfg?.llm?.model            ?? _entry.config.model,
      temperature:           cfg?.llm?.temperature      ?? 0.7,
      max_tokens:            cfg?.llm?.max_tokens       ?? 4096,
      soul:                  soul.soul,
      tools:                 cfg?.tools                 ?? [],
      heartbeat_interval:    cfg?.heartbeat?.interval   ?? "30s",
      heartbeat_timeout:     cfg?.heartbeat?.timeout    ?? "90s",
      heartbeat_on_failure:  cfg?.heartbeat?.on_failure ?? "restart",
    });
    setEditId(id); setShowForm(true);
  }

  function closeForm() { setShowForm(false); setEditId(null); setSaveErr(""); }

  function set(key: string, val: unknown) { setForm(f => ({ ...f, [key]: val })); }

  function toggleTool(t: string) {
    setForm(f => ({
      ...f, tools: f.tools.includes(t) ? f.tools.filter(x=>x!==t) : [...f.tools, t]
    }));
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault(); setSaving(true); setSaveErr("");
    try {
      if (editId) {
        await api.updateAgent(editId, form);
      } else {
        await (api as any).createAgent(form);
      }
      closeForm(); await load();
    } catch(e) { setSaveErr(e instanceof Error ? e.message : "Fehler"); }
    finally { setSaving(false); }
  }

  async function handleDelete(id: string) {
    if (!confirm(`Agent "${id}" deaktivieren?`)) return;
    setDeleting(id);
    try { await api.deleteAgent(id); await load(); }
    catch(e) { setError(e instanceof Error ? e.message : "Fehler beim Löschen"); }
    finally { setDeleting(null); }
  }

  const agentList = Object.entries(agents);

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Agenten</h1>
          <p className="text-sm text-muted-foreground">{agentList.length} Agent{agentList.length!==1?"en":""} registriert</p>
        </div>
        <div className="flex gap-2">
          <button onClick={refresh} disabled={refreshing}
            className="flex items-center gap-2 px-3 py-1.5 text-sm border rounded-md hover:bg-accent transition-colors disabled:opacity-50">
            <RefreshCw className={`h-3.5 w-3.5 ${refreshing?"animate-spin":""}`} />Aktualisieren
          </button>
          {isAdmin && (
            <button onClick={openNew}
              className="flex items-center gap-2 px-3 py-1.5 text-sm bg-primary text-primary-foreground rounded-md hover:bg-primary/90 transition-colors">
              <Plus className="h-3.5 w-3.5" />Neuer Agent
            </button>
          )}
        </div>
      </div>

      {error && <div className="bg-destructive/10 border border-destructive/20 rounded-lg px-4 py-3 text-sm text-destructive">{error}</div>}

      {/* Formular */}
      {showForm && (
        <div className="bg-card border rounded-lg p-5 space-y-5">
          <div className="flex items-center justify-between">
            <h2 className="font-medium">{editId ? `Agent bearbeiten: ${editId}` : "Neuen Agent anlegen"}</h2>
            <button onClick={closeForm} className="text-muted-foreground hover:text-foreground"><X className="h-4 w-4" /></button>
          </div>
          <form onSubmit={handleSave} className="space-y-5">
            {/* Grunddaten */}
            <div className="grid grid-cols-2 gap-4">
              <Field label="Agent-ID *" hint="Nur a-z, 0-9, _ und -">
                <Input value={form.id} onChange={e=>set("id",e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g,""))}
                  placeholder="z.B. steuer-agent" required disabled={!!editId} />
              </Field>
              <Field label="Anzeigename *">
                <Input value={form.identity} onChange={e=>set("identity",e.target.value)}
                  placeholder="z.B. Steuerbert" required />
              </Field>
              <Field label="Typ *">
                <select value={form.type} onChange={e=>set("type",e.target.value)}
                  className="w-full px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary">
                  <option value="specialist">specialist</option>
                  <option value="boss">boss</option>
                  <option value="worker">worker</option>
                </select>
              </Field>
              <Field label="LLM-Modell *">
                <select value={form.model} onChange={e=>set("model",e.target.value)}
                  className="w-full px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary">
                  {[...new Set([...KNOWN_MODELS, form.model].filter(Boolean))].map(m => <option key={m} value={m}>{m}</option>)}
                </select>
              </Field>
              <Field label="Temperature">
                <Input type="number" value={form.temperature} onChange={e=>set("temperature",parseFloat(e.target.value))}
                  min={0} max={2} step={0.1} />
              </Field>
              <Field label="Max Tokens">
                <Input type="number" value={form.max_tokens} onChange={e=>set("max_tokens",parseInt(e.target.value))}
                  min={256} max={32000} step={256} />
              </Field>
            </div>

            {/* Heartbeat */}
            <div>
              <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-3">Heartbeat</p>
              <div className="grid grid-cols-3 gap-4">
                <Field label="Interval">
                  <Input value={form.heartbeat_interval} onChange={e=>set("heartbeat_interval",e.target.value)} placeholder="30s" />
                </Field>
                <Field label="Timeout">
                  <Input value={form.heartbeat_timeout} onChange={e=>set("heartbeat_timeout",e.target.value)} placeholder="90s" />
                </Field>
                <Field label="Bei Fehler">
                  <select value={form.heartbeat_on_failure} onChange={e=>set("heartbeat_on_failure",e.target.value)}
                    className="w-full px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary">
                    <option value="restart">restart</option>
                    <option value="stop">stop</option>
                    <option value="alert">alert</option>
                  </select>
                </Field>
              </div>
            </div>

            {/* Tools */}
            <div>
              <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-3">Tools</p>
              <div className="flex flex-wrap gap-2">
                {KNOWN_TOOLS.map(t => (
                  <button key={t} type="button" onClick={()=>toggleTool(t)}
                    className={`px-2.5 py-1 text-xs rounded border transition-colors ${
                      form.tools.includes(t)
                        ? "bg-primary text-primary-foreground border-primary"
                        : "border hover:bg-accent"
                    }`}>
                    {t}
                  </button>
                ))}
              </div>
            </div>

            {/* Soul */}
            <Field label="Soul (Persönlichkeit)" hint="Markdown — beschreibt Charakter und Kommunikationsstil des Agenten">
              <textarea value={form.soul} onChange={e=>set("soul",e.target.value)} rows={5}
                placeholder={`# ${form.identity || "Agent"}\n\nDu bist ein spezialisierter KI-Agent...`}
                className="w-full px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary resize-none font-mono" />
            </Field>

            {saveErr && <p className="text-sm text-destructive">{saveErr}</p>}
            <div className="flex justify-end gap-2">
              <button type="button" onClick={closeForm}
                className="px-4 py-2 text-sm border rounded-md hover:bg-accent transition-colors">Abbrechen</button>
              <button type="submit" disabled={saving}
                className="flex items-center gap-2 px-4 py-2 text-sm bg-primary text-primary-foreground rounded-md hover:bg-primary/90 disabled:opacity-50 transition-colors">
                <Save className="h-3.5 w-3.5" />{saving ? "Speichern..." : editId ? "Aktualisieren" : "Agent anlegen"}
              </button>
            </div>
          </form>
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="space-y-3">
          {[1,2,3].map(i => <div key={i} className="bg-card border rounded-lg p-4 animate-pulse h-16" />)}
        </div>
      )}

      {/* Leer */}
      {!loading && agentList.length === 0 && !showForm && (
        <div className="bg-card border rounded-lg p-12 text-center space-y-3">
          <Bot className="h-10 w-10 mx-auto text-muted-foreground" />
          <p className="text-sm text-muted-foreground">Keine Agenten. Leg den ersten an.</p>
          {isAdmin && (
            <button onClick={openNew}
              className="inline-flex items-center gap-2 px-4 py-2 text-sm bg-primary text-primary-foreground rounded-md hover:bg-primary/90 transition-colors">
              <Plus className="h-4 w-4" />Ersten Agent anlegen
            </button>
          )}
        </div>
      )}

      {/* Liste */}
      {!loading && agentList.length > 0 && (
        <div className="space-y-3">
          {agentList.map(([id, agent]) => {
            const rt       = agent.runtime;
            const status   = rt?.status ?? "unbekannt";
            const color    = STATUS_COLORS[status] ?? "text-muted-foreground";
            const hbAge    = rt?.last_heartbeat_age;
            const hbWarn   = hbAge != null && hbAge > (rt?.heartbeat_timeout ?? 90) * 0.8;
            const taskCount = hbTasks.filter(t => t.agent_id === id).length;
            return (
              <div key={id} className="bg-card border rounded-lg overflow-hidden">
              <div className="p-4 flex items-start gap-4">
                <div className="w-9 h-9 rounded-md bg-primary/10 flex items-center justify-center flex-shrink-0">
                  <Bot className="h-5 w-5 text-primary" />
                </div>
                <div className="flex-1 min-w-0 space-y-1">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-sm">{agent.config.identity}</span>
                    <span className="text-xs text-muted-foreground">({id})</span>
                    <span className="text-xs px-1.5 py-0.5 rounded bg-secondary text-secondary-foreground">{agent.config.type}</span>
                    {taskCount > 0 && (
                      <span className="inline-flex items-center gap-1 text-xs px-1.5 py-0.5 rounded bg-primary/10 text-primary" title={`${taskCount} Heartbeat-Task${taskCount > 1 ? "s" : ""}`}>
                        <Timer className="h-3 w-3" />{taskCount}
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-muted-foreground">{agent.config.model}</p>
                </div>
                <div className="flex items-center gap-2 flex-shrink-0">
                  <div className={`flex items-center gap-1.5 text-sm font-medium ${color}`}>
                    <Circle className="h-2 w-2 fill-current" />{status}
                  </div>
                  {rt && (
                    <span className={`text-xs ${hbWarn ? "text-orange-500" : "text-muted-foreground"}`}>
                      HB {hbAge?.toFixed(0)}s
                      {rt.restart_count > 0 && <span className="ml-1 text-orange-500">↺{rt.restart_count}</span>}
                    </span>
                  )}
                  <button onClick={() => navigate(`/agents/${id}/chat`)} title="Direkt chatten"
                    className="p-1.5 rounded hover:bg-accent transition-colors text-muted-foreground hover:text-foreground">
                    <MessageSquare className="h-3.5 w-3.5" />
                  </button>
                  <button onClick={() => setSkillsAgent(s => s === id ? null : id)} title="Skills verwalten"
                    className={`p-1.5 rounded transition-colors ${skillsAgent === id ? "bg-primary/10 text-primary" : "hover:bg-accent text-muted-foreground hover:text-foreground"}`}>
                    <BookOpen className="h-3.5 w-3.5" />
                  </button>
                  <button onClick={() => openLogs(id)} title="Logs anzeigen"
                    className={`p-1.5 rounded transition-colors ${logAgent === id ? "bg-primary/10 text-primary" : "hover:bg-accent text-muted-foreground hover:text-foreground"}`}>
                    <ScrollText className="h-3.5 w-3.5" />
                  </button>
                  {isAdmin && (
                    <button onClick={() => openEdit(id, agent)}
                      className="p-1.5 rounded hover:bg-accent transition-colors text-muted-foreground hover:text-foreground">
                      <Pencil className="h-3.5 w-3.5" />
                    </button>
                  )}
                  {isAdmin && (
                    <button onClick={() => handleDelete(id)} disabled={deleting === id}
                      className="p-1.5 rounded hover:bg-destructive/10 transition-colors text-muted-foreground hover:text-destructive disabled:opacity-50">
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  )}
                </div>
              </div>
              {skillsAgent === id && <SkillsPanel agentId={id} />}
              </div>
            );
          })}
        </div>
      )}

      {/* Log-Panel */}
      {logAgent && (
        <div className="bg-card border rounded-lg overflow-hidden">
          <div className="flex items-center justify-between px-4 py-2 border-b bg-muted/30">
            <div className="flex items-center gap-2">
              <ScrollText className="h-4 w-4 text-muted-foreground" />
              <span className="text-sm font-medium">Logs — {logAgent}</span>
              <span className="flex items-center gap-1 text-xs text-green-500">
                <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />Live
              </span>
            </div>
            <button onClick={closeLogs} className="text-muted-foreground hover:text-foreground">
              <X className="h-4 w-4" />
            </button>
          </div>
          {logErr
            ? <p className="p-4 text-sm text-destructive">{logErr}</p>
            : (
              <div className="h-72 overflow-y-auto p-3 font-mono text-xs leading-relaxed bg-[#0d0d0d] text-[#d4d4d4]">
                {logLines.length === 0
                  ? <span className="text-muted-foreground">Lade Logs…</span>
                  : logLines.map((line, i) => (
                    <div key={i} className={`whitespace-pre-wrap break-all ${
                      line.includes(" ERROR ") || line.includes(" error ") ? "text-red-400" :
                      line.includes(" WARNING ") || line.includes(" warning ") ? "text-yellow-400" :
                      "text-[#d4d4d4]"
                    }`}>{line}</div>
                  ))
                }
                <div ref={logBottomRef} />
              </div>
            )
          }
        </div>
      )}
    </div>
  );
}
