import { useEffect, useState } from "react";
import type { LucideIcon } from "lucide-react";
import {
  Bot, Plus, Pencil, Trash2, X,
  Users, Settings, Loader2, Plug,
} from "lucide-react";
import { api } from "@/lib/api";

interface McpServer { id: string; name: string; transport?: string }

interface Agent {
  id: string;
  type: string;
  identity: string;
  model: string;
  team_id?: string;
  team_role?: string;
  soul_preview?: string;
  description?: string;
  max_tool_rounds?: number;
  execution_mode_default?: string;
  risk_policy?: string;
}

interface Team { id: string; name: string; members: TeamMember[] }
interface TeamMember { agent_id: string; role: string }

interface Project { id: string; name: string; agents?: { boss?: string; workers?: string[] } }

type Tab = "basis" | "zuweisung" | "erweitert";

interface FormState {
  id: string;
  identity: string;
  type: string;
  model: string;
  soul: string;
  team_id: string;
  team_role: string;
  execution_mode_default: string;
  risk_policy: string;
  max_tool_rounds: string;
  compaction_threshold: string;
  project_assignments: Record<string, string>;
  mcp_servers: string[];
}

const emptyForm = (): FormState => ({
  id: "", identity: "", type: "worker", model: "claude-sonnet-4-6", soul: "",
  team_id: "", team_role: "",
  execution_mode_default: "elevated", risk_policy: "interactive",
  max_tool_rounds: "", compaction_threshold: "",
  project_assignments: {},
  mcp_servers: [],
});

function slugify(s: string) {
  return s.toLowerCase().replace(/[^a-z0-9_-]/g, "-").replace(/--+/g, "-");
}

export function AgentsPage() {
  const [agents, setAgents] = useState<Record<string, Agent>>({});
  const [teams, setTeams] = useState<Team[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [mcpServers, setMcpServers] = useState<McpServer[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<Tab>("basis");

  // Dialog state
  const [dialogOpen, setDialogOpen] = useState(false);
  const [isNew, setIsNew] = useState(false);
  const [formId, setFormId] = useState("");
  const [form, setForm] = useState<FormState>(emptyForm());

  // Delete confirm
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);

  useEffect(() => { void loadAll(); }, []);

  async function loadAll() {
    setLoading(true);
    try {
      const [a, t, p, mcp] = await Promise.all([
        api.get<Record<string, Agent>>("/agents"),
        api.get<string[]>("/admin/teams").then(async (ids: string[]) => {
          const mapped: Team[] = [];
          for (const id of (ids ?? [])) {
            try { mapped.push(await api.get<Team>(`/admin/teams/${id}`)); } catch { /* skip */ }
          }
          return mapped;
        }),
        api.get<Record<string, any>>("/projects").then(rec =>
          Object.entries(rec ?? {}).map(([id, cfg]: [string, any]) => ({
            id,
            name: cfg?.identity?.name ?? cfg?.name ?? id,
            agents: cfg?.agents ?? {},
          }))
        ),
        api.get<{ servers: McpServer[] }>("/mcp/servers").then(r => r?.servers ?? []).catch(() => []),
      ]);
      setAgents(a ?? {});
      setTeams(t);
      setProjects(p);
      setMcpServers(mcp);
    } catch (e) {
      console.error("loadAll failed", e);
    } finally {
      setLoading(false);
    }
  }

  function openNew() {
    setIsNew(true);
    setFormId("");
    setForm(emptyForm());
    setActiveTab("basis");
    setDialogOpen(true);
  }

  function openEdit(id: string) {
    const ag = agents[id];
    if (!ag) return;
    setIsNew(false);
    setFormId(id);
    const agAny = ag as any;
    setForm({
      id: ag.id || id,
      identity: ag.identity || "",
      type: ag.type || "worker",
      model: agAny.llm?.model || ag.model || "claude-sonnet-4-6",
      soul: "",
      team_id: agAny.team_id || ag.team_id || "",
      team_role: agAny.team_role || ag.team_role || "",
      execution_mode_default: agAny.execution_mode_default || ag.execution_mode_default || "elevated",
      risk_policy: agAny.risk_policy || ag.risk_policy || "interactive",
      max_tool_rounds: String(agAny.max_tool_rounds ?? ag.max_tool_rounds ?? ""),
      compaction_threshold: "",
      project_assignments: {},
      mcp_servers: agAny.mcp_servers ?? [],
    });
    setActiveTab("basis");
    setDialogOpen(true);
  }

  async function handleSave() {
    if (!formId.trim()) return;
    setSaving(true);
    try {
      const payload: Record<string, unknown> = {
        id: formId.trim(),
        type: form.type,
        identity: form.identity.trim(),
        model: form.model.trim(),
        soul: form.soul,
        team_id: form.team_id || null,
        team_role: form.team_role || null,
        execution_mode_default: form.execution_mode_default,
        risk_policy: form.risk_policy,
        max_tool_rounds: form.max_tool_rounds ? parseInt(form.max_tool_rounds) : null,
        mcp_servers: form.mcp_servers,
      };
      if (isNew) {
        await api.post("/agents", payload);
      } else {
        await api.put(`/agents/${formId.trim()}`, payload);
      }
      // Projekt-Zuweisungen speichern
      for (const [projId, role] of Object.entries(form.project_assignments)) {
        const cur = projects.find(p => p.id === projId);
        const curBoss = cur?.agents?.boss || "";
        const curWorkers = cur?.agents?.workers || [];
        const wasBoss = curBoss === formId;
        const wasWorker = curWorkers.includes(formId);

        if (role === "boss") {
          await api.put(`/projects/${projId}/settings`, {
            agents_boss: formId,
            agents_workers: (cur?.agents?.workers || []).filter(w => w !== formId),
          });
        } else if (role === "worker") {
          await api.put(`/projects/${projId}/settings`, {
            agents_boss: wasBoss ? "" : (cur?.agents?.boss || ""),
            agents_workers: wasWorker
              ? (cur?.agents?.workers || [])
              : [...(cur?.agents?.workers || []), formId],
          });
        } else {
          await api.put(`/projects/${projId}/settings`, {
            agents_boss: wasBoss ? "" : (cur?.agents?.boss || ""),
            agents_workers: (cur?.agents?.workers || []).filter(w => w !== formId),
          });
        }
      }
      setDialogOpen(false);
      await loadAll();
    } catch (e) {
      console.error("save failed", e);
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(id: string) {
    setDeleting(id);
    try {
      await api.delete(`/agents/${id}`);
      setDeleteConfirm(null);
      await loadAll();
    } catch (e) {
      console.error("delete failed", e);
    } finally {
      setDeleting(null);
    }
  }

  function setField<K extends keyof FormState>(k: K, v: FormState[K]) {
    setForm(prev => ({ ...prev, [k]: v }));
  }

  const tabs: { id: Tab; label: string; icon: LucideIcon }[] = [
    { id: "basis", label: "Basis", icon: Bot },
    { id: "zuweisung", label: "Zuweisung", icon: Users },
    { id: "erweitert", label: "Erweitert", icon: Settings },
  ];

  function toggleMcp(id: string) {
    setForm(prev => ({
      ...prev,
      mcp_servers: prev.mcp_servers.includes(id)
        ? prev.mcp_servers.filter(s => s !== id)
        : [...prev.mcp_servers, id],
    }));
  }

  const typeOptions = [
    { value: "boss", label: "Boss — Haupt-KI des Projekts" },
    { value: "worker", label: "Worker — Spezialaufgaben" },
    { value: "specialist", label: "Specialist — Eigene Prompts + Tools" },
  ];

  const modelExamples = ["claude-sonnet-4-6", "claude-opus-4-7", "minimax-m2.7", "gpt-4o", "qwen3-coder"];

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="h-8 w-8 animate-spin text-indigo-400" />
      </div>
    );
  }

  return (
    <div className="p-6 space-y-6 max-w-5xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Agenten</h1>
          <p className="text-sm text-white/40 mt-1">
            Agenten sind die ausführenden KI-Einheiten. Jeder Agent hat eine eigene Persönlichkeit (Soul),
            ein LLM-Modell und eine Rollenzuweisung. Projekte nutzen einen Boss-Agenten als Haupt-KI
            und optionale Worker für Spezialaufgaben. Teams fassen Agenten mit klaren Rollen zusammen.
          </p>
        </div>
        <button
          onClick={openNew}
          className="flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm text-white hover:bg-indigo-700 transition-colors"
        >
          <Plus className="h-4 w-4" />
          Neuer Agent
        </button>
      </div>

      {/* Agent-Liste — zwei Sektionen */}
      {Object.keys(agents).length === 0 ? (
        <div className="rounded-xl border border-white/10 bg-zinc-900 p-12 text-center text-white/40">
          Keine Agenten vorhanden. Erstelle einen!
        </div>
      ) : (() => {
        const bosses: [string, Agent][] = [];
        const pool: [string, Agent][] = [];
        for (const [id, ag] of Object.entries(agents)) {
          if ((ag as any).type === "boss") bosses.push([id, ag]);
          else pool.push([id, ag]);
        }
        return (
          <>
            {bosses.length > 0 && (
              <section>
                <div className="flex items-center gap-2 mb-3">
                  <Bot className="h-4 w-4 text-indigo-400" />
                  <h2 className="text-sm font-semibold text-white">Projekt-Master</h2>
                  <span className="text-xs text-white/40">{bosses.length}</span>
                </div>
                <div className="grid gap-3">
                  {bosses.map(([id, ag]) => {
                    const team = teams.find(t => t.id === (ag as any).team_id);
                    return (
                      <div key={id} className="rounded-xl border border-white/10 bg-zinc-900 p-4">
                        <div className="flex items-start justify-between gap-4">
                          <div className="flex items-start gap-3 min-w-0">
                            <div className="mt-0.5 shrink-0">
                              <Bot className="h-5 w-5 text-indigo-400" />
                            </div>
                            <div className="min-w-0">
                              <div className="flex items-center gap-2 flex-wrap">
                                <span className="font-semibold text-white">{ag.identity || id}</span>
                                <span className="text-xs rounded px-1.5 py-0.5 bg-zinc-800 text-white/50">
                                  {ag.type || "worker"}
                                </span>
                                {(ag as any).team_id && team && (
                                  <span className="text-xs rounded px-1.5 py-0.5 bg-blue-900/40 text-blue-300">
                                    {team.name}
                                  </span>
                                )}
                                {(ag as any).team_role && (
                                  <span className="text-xs text-white/40">@{(ag as any).team_role}</span>
                                )}
                              </div>
                              <div className="text-xs text-white/30 mt-0.5 font-mono">{id}</div>
                              {(ag as any).llm?.model && (
                                <div className="text-xs text-white/40 mt-0.5">Model: {(ag as any).llm.model}</div>
                              )}
                              {ag.description && (
                                <div className="text-sm text-white/50 mt-1 line-clamp-1">{ag.description}</div>
                              )}
                            </div>
                          </div>
                          <div className="flex items-center gap-2 shrink-0">
                            <button
                              onClick={() => openEdit(id)}
                              className="rounded-lg border border-white/10 p-2 text-white/40 hover:text-white hover:border-white/30 transition-colors"
                            >
                              <Pencil className="h-3.5 w-3.5" />
                            </button>
                            {deleteConfirm === id ? (
                              <div className="flex items-center gap-1">
                                <button
                                  onClick={() => void handleDelete(id)}
                                  disabled={deleting === id}
                                  className="rounded-lg border border-red-500/30 bg-red-500/10 px-2 py-1 text-xs text-red-400 hover:bg-red-500/20 transition-colors"
                                >
                                  {deleting === id ? <Loader2 className="h-3 w-3 animate-spin" /> : "Bestätigen"}
                                </button>
                                <button
                                  onClick={() => setDeleteConfirm(null)}
                                  className="rounded-lg border border-white/10 px-2 py-1 text-xs text-white/40 hover:text-white transition-colors"
                                >
                                  Abbrechen
                                </button>
                              </div>
                            ) : (
                              <button
                                onClick={() => setDeleteConfirm(id)}
                                className="rounded-lg border border-white/10 p-2 text-white/40 hover:text-red-400 hover:border-red-500/30 transition-colors"
                              >
                                <Trash2 className="h-3.5 w-3.5" />
                              </button>
                            )}
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </section>
            )}
            {pool.length > 0 && (
              <section>
                <div className="flex items-center gap-2 mb-3">
                  <Users className="h-4 w-4 text-white/40" />
                  <h2 className="text-sm font-semibold text-white">Spezialist-Pool</h2>
                  <span className="text-xs text-white/40">{pool.length}</span>
                </div>
                <div className="grid gap-3">
                  {pool.map(([id, ag]) => {
                    const team = teams.find(t => t.id === (ag as any).team_id);
                    return (
                      <div key={id} className="rounded-xl border border-white/10 bg-zinc-900 p-4">
                        <div className="flex items-start justify-between gap-4">
                          <div className="flex items-start gap-3 min-w-0">
                            <div className="mt-0.5 shrink-0">
                              <Bot className="h-5 w-5 text-indigo-400" />
                            </div>
                            <div className="min-w-0">
                              <div className="flex items-center gap-2 flex-wrap">
                                <span className="font-semibold text-white">{ag.identity || id}</span>
                                <span className="text-xs rounded px-1.5 py-0.5 bg-zinc-800 text-white/50">
                                  {ag.type || "worker"}
                                </span>
                                {(ag as any).team_id && team && (
                                  <span className="text-xs rounded px-1.5 py-0.5 bg-blue-900/40 text-blue-300">
                                    {team.name}
                                  </span>
                                )}
                                {(ag as any).team_role && (
                                  <span className="text-xs text-white/40">@{(ag as any).team_role}</span>
                                )}
                              </div>
                              <div className="text-xs text-white/30 mt-0.5 font-mono">{id}</div>
                              {(ag as any).llm?.model && (
                                <div className="text-xs text-white/40 mt-0.5">Model: {(ag as any).llm.model}</div>
                              )}
                              {ag.description && (
                                <div className="text-sm text-white/50 mt-1 line-clamp-1">{ag.description}</div>
                              )}
                            </div>
                          </div>
                          <div className="flex items-center gap-2 shrink-0">
                            <button
                              onClick={() => openEdit(id)}
                              className="rounded-lg border border-white/10 p-2 text-white/40 hover:text-white hover:border-white/30 transition-colors"
                            >
                              <Pencil className="h-3.5 w-3.5" />
                            </button>
                            {deleteConfirm === id ? (
                              <div className="flex items-center gap-1">
                                <button
                                  onClick={() => void handleDelete(id)}
                                  disabled={deleting === id}
                                  className="rounded-lg border border-red-500/30 bg-red-500/10 px-2 py-1 text-xs text-red-400 hover:bg-red-500/20 transition-colors"
                                >
                                  {deleting === id ? <Loader2 className="h-3 w-3 animate-spin" /> : "Bestätigen"}
                                </button>
                                <button
                                  onClick={() => setDeleteConfirm(null)}
                                  className="rounded-lg border border-white/10 px-2 py-1 text-xs text-white/40 hover:text-white transition-colors"
                                >
                                  Abbrechen
                                </button>
                              </div>
                            ) : (
                              <button
                                onClick={() => setDeleteConfirm(id)}
                                className="rounded-lg border border-white/10 p-2 text-white/40 hover:text-red-400 hover:border-red-500/30 transition-colors"
                              >
                                <Trash2 className="h-3.5 w-3.5" />
                              </button>
                            )}
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </section>
            )}
          </>
        );
      })()}

      {/* Dialog */}
      {dialogOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <div className="w-full max-w-lg rounded-2xl border border-white/10 bg-zinc-900 shadow-2xl max-h-[90vh] overflow-y-auto">
            {/* Dialog Header */}
            <div className="flex items-center justify-between p-5 border-b border-white/5">
              <h2 className="text-lg font-semibold text-white">
                {isNew ? "Neuer Agent" : `Agent bearbeiten: ${formId}`}
              </h2>
              <button
                onClick={() => setDialogOpen(false)}
                className="rounded-lg p-1 text-white/40 hover:text-white transition-colors"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            {/* Tabs */}
            <div className="flex border-b border-white/5">
              {tabs.map(t => (
                <button
                  key={t.id}
                  onClick={() => setActiveTab(t.id)}
                  className={`flex items-center gap-1.5 px-4 py-2.5 text-sm transition-colors ${
                    activeTab === t.id
                      ? "text-indigo-400 border-b-2 border-indigo-400"
                      : "text-white/40 hover:text-white"
                  }`}
                >
                  <t.icon className="h-3.5 w-3.5" />
                  {t.label}
                </button>
              ))}
            </div>

            {/* Tab Content */}
            <div className="p-5 space-y-4">
              {activeTab === "basis" && (
                <>
                  <div>
                    <label className="block text-xs text-white/40 mb-1">
                      ID {isNew ? "(slug, frei wählbar)" : "(nicht änderbar)"}
                    </label>
                    {isNew ? (
                      <input
                        value={formId}
                        onChange={e => setFormId(slugify(e.target.value))}
                        placeholder="z.B. coder-main"
                        className="w-full rounded-lg bg-zinc-800 border border-white/10 px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500/60 font-mono"
                      />
                    ) : (
                      <div className="w-full rounded-lg bg-zinc-800/50 border border-white/5 px-3 py-2 text-sm text-white/40 font-mono">
                        {formId}
                      </div>
                    )}
                  </div>
                  <div>
                    <label className="block text-xs text-white/40 mb-1">Identity / Name</label>
                    <input
                      value={form.identity}
                      onChange={e => setField("identity", e.target.value)}
                      placeholder="z.B. Coder — Hauptentwickler"
                      className="w-full rounded-lg bg-zinc-800 border border-white/10 px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500/60"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-white/40 mb-1">Typ</label>
                    <select
                      value={form.type}
                      onChange={e => setField("type", e.target.value)}
                      className="w-full rounded-lg bg-zinc-800 border border-white/10 px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500/60">
                      {typeOptions.map(o => (
                        <option key={o.value} value={o.value}>{o.label}</option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label className="block text-xs text-white/40 mb-1">Modell</label>
                    <select
                      value={form.model}
                      onChange={e => setField("model", e.target.value)}
                      className="w-full rounded-lg bg-zinc-800 border border-white/10 px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500/60"
                    >
                      {!modelExamples.includes(form.model) && <option value={form.model}>{form.model}</option>}
                      {modelExamples.map(m => <option key={m} value={m}>{m}</option>)}
                    </select>
                  </div>
                  <div>
                    <label className="block text-xs text-white/40 mb-1">Soul / Persönlichkeit</label>
                    <textarea
                      value={form.soul}
                      onChange={e => setField("soul", e.target.value)}
                      rows={6}
                      placeholder="Beschreibe die Persönlichkeit, Stärken und Arbeitsweise dieses Agenten…"
                      className="w-full rounded-lg bg-zinc-800 border border-white/10 px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500/60 resize-y"
                    />
                  </div>
                </>
              )}

              {activeTab === "zuweisung" && (
                <>
                  <div>
                    <label className="block text-xs text-white/40 mb-1">Team</label>
                    <select
                      value={form.team_id}
                      onChange={e => setField("team_id", e.target.value)}
                      className="w-full rounded-lg bg-zinc-800 border border-white/10 px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500/60">
                      <option value="">Kein Team</option>
                      {teams.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
                    </select>
                  </div>
                  <div>
                    <label className="block text-xs text-white/40 mb-1">Rolle im Team</label>
                    <input
                      value={form.team_role}
                      onChange={e => setField("team_role", e.target.value)}
                      placeholder="z.B. coder, reviewer, researcher"
                      className="w-full rounded-lg bg-zinc-800 border border-white/10 px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500/60"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-white/40 mb-1">Projekt-Zuweisung</label>
                    {projects.length === 0 ? (
                      <p className="text-sm text-white/30 py-2">Keine Projekte vorhanden.</p>
                    ) : (
                      <div className="space-y-2">
                        {projects.map(proj => {
                          const curBoss = proj.agents?.boss || "";
                          const curWorkers = proj.agents?.workers || [];
                          const isBoss = curBoss === formId;
                          const isWorker = curWorkers.includes(formId);
                          const current = isBoss ? "boss" : isWorker ? "worker" : "none";
                          return (
                            <div key={proj.id} className="flex items-center justify-between rounded-lg bg-zinc-800 px-3 py-2">
                              <span className="text-sm text-white/70 truncate mr-3">{proj.name}</span>
                              <div className="flex items-center gap-3 shrink-0">
                                <label className="flex items-center gap-1 text-xs cursor-pointer">
                                  <input
                                    type="radio"
                                    name={`proj-${proj.id}`}
                                    checked={(form.project_assignments[proj.id] || current) === "boss"}
                                    onChange={() => setField("project_assignments", { ...form.project_assignments, [proj.id]: "boss" })}
                                    className="accent-indigo-500"
                                  />
                                  <span className="text-white/50">Boss</span>
                                </label>
                                <label className="flex items-center gap-1 text-xs cursor-pointer">
                                  <input
                                    type="radio"
                                    name={`proj-${proj.id}`}
                                    checked={(form.project_assignments[proj.id] || current) === "worker"}
                                    onChange={() => setField("project_assignments", { ...form.project_assignments, [proj.id]: "worker" })}
                                    className="accent-indigo-500"
                                  />
                                  <span className="text-white/50">Worker</span>
                                </label>
                                <label className="flex items-center gap-1 text-xs cursor-pointer">
                                  <input
                                    type="radio"
                                    name={`proj-${proj.id}`}
                                    checked={(form.project_assignments[proj.id] || current) === "none"}
                                    onChange={() => setField("project_assignments", { ...form.project_assignments, [proj.id]: "none" })}
                                    className="accent-indigo-500"
                                  />
                                  <span className="text-white/50">—</span>
                                </label>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                </>
              )}

              {activeTab === "erweitert" && (
                <>
                  <div>
                    <label className="block text-xs text-white/40 mb-1">Execution Mode</label>
                    <select
                      value={form.execution_mode_default}
                      onChange={e => setField("execution_mode_default", e.target.value)}
                      className="w-full rounded-lg bg-zinc-800 border border-white/10 px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500/60">
                      <option value="safe">Safe</option>
                      <option value="elevated">Elevated</option>
                      <option value="unrestricted">Unrestricted</option>
                    </select>
                  </div>
                  <div>
                    <label className="block text-xs text-white/40 mb-1">Risk Policy</label>
                    <select
                      value={form.risk_policy}
                      onChange={e => setField("risk_policy", e.target.value)}
                      className="w-full rounded-lg bg-zinc-800 border border-white/10 px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500/60">
                      <option value="interactive">Interactive (vor jeder riskanten Aktion nachfragen)</option>
                      <option value="trusted">Trusted (keine Nachfragen)</option>
                    </select>
                  </div>
                  <div>
                    <label className="block text-xs text-white/40 mb-1">Max Tool Rounds (1–200)</label>
                    <input
                      type="number"
                      min={1}
                      max={200}
                      value={form.max_tool_rounds}
                      onChange={e => setField("max_tool_rounds", e.target.value)}
                      placeholder="leer = default"
                      className="w-full rounded-lg bg-zinc-800 border border-white/10 px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500/60"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-white/40 mb-1">Kompaktierungs-Schwellwert (Tokens)</label>
                    <input
                      type="number"
                      value={form.compaction_threshold}
                      onChange={e => setField("compaction_threshold", e.target.value)}
                      placeholder="leer = global default (80k bei MiniMax 200k)"
                      className="w-full rounded-lg bg-zinc-800 border border-white/10 px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500/60"
                    />
                  </div>
                  {mcpServers.length > 0 && (
                    <div>
                      <label className="flex items-center gap-1.5 text-xs text-white/40 mb-2">
                        <Plug className="h-3 w-3" /> Erweiterungen (MCP-Server)
                      </label>
                      <div className="space-y-1.5">
                        {mcpServers.map(srv => {
                          const active = form.mcp_servers.includes(srv.id);
                          return (
                            <button
                              key={srv.id}
                              type="button"
                              onClick={() => toggleMcp(srv.id)}
                              className={`w-full flex items-center justify-between rounded-lg border px-3 py-2 text-sm transition-colors ${
                                active
                                  ? "border-indigo-500/40 bg-indigo-500/10 text-white"
                                  : "border-white/10 bg-zinc-800/50 text-white/40 hover:text-white/70"
                              }`}
                            >
                              <span className="flex items-center gap-2">
                                <Plug className="h-3.5 w-3.5 flex-shrink-0" />
                                <span>{srv.name || srv.id}</span>
                                <span className="font-mono text-[10px] opacity-50">{srv.id}</span>
                              </span>
                              <span className={`text-[10px] font-medium ${active ? "text-indigo-400" : "text-white/20"}`}>
                                {active ? "AN" : "AUS"}
                              </span>
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  )}
                </>
              )}
            </div>

            {/* Dialog Footer */}
            <div className="flex justify-end gap-2 p-5 border-t border-white/5">
              <button
                onClick={() => setDialogOpen(false)}
                className="rounded-lg border border-white/10 px-4 py-2 text-sm text-white/60 hover:text-white transition-colors">
                Abbrechen
              </button>
              <button
                onClick={() => void handleSave()}
                disabled={saving || !formId.trim() || !form.identity.trim()}
                className="flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm text-white hover:bg-indigo-700 disabled:opacity-40 transition-colors">
                {saving && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                {isNew ? "Erstellen" : "Speichern"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
