import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  FolderKanban,
  Plus,
  RefreshCw,
  HardDrive,
  Hash,
  Users,
  Webhook,
  GitMerge,
  Trash2,
  ArrowRight,
  Radar,
  Workflow,
  Server,
  MessageSquare,
  ShieldAlert,
  Boxes,
  GitBranch,
  Pencil,
  X,
  Save,
  Eye,
  EyeOff,
  KeyRound,
  Code2,
} from "lucide-react";
import { api } from "@/lib/api";
import { WebhooksPanel } from "@/components/WebhooksPanel";
import { AgentLinkPanel } from "@/components/AgentLinkPanel";
import { useAuth } from "@/hooks/useAuth";
import { useTranslation } from "react-i18next";

interface ProjectEntry {
  name: string;
  description: string;
  boss: string;
  workers: string[];
  matrix_room: string;
  filesystem: string;
  system_user: string;
  show_swarm: boolean;
}

interface CreateForm {
  id: string;
  name: string;
  description: string;
  boss: string;
  workers: string;
  samba: boolean;
}

interface EditForm {
  name: string;
  description: string;
  boss: string;
  workers: string;
  show_swarm: boolean;
}

const EMPTY: CreateForm = { id: "", name: "", description: "", boss: "", workers: "", samba: true };

export function ProjectsPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { isAdmin } = useAuth();
  const [projects, setProjects] = useState<Record<string, ProjectEntry>>({});
  const [agents, setAgents] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<CreateForm>(EMPTY);
  const [creating, setCreating] = useState(false);
  const [createErr, setCreateErr] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [webhookProject, setWebhookProject] = useState<string | null>(null);
  const [agentlinkProject, setAgentlinkProject] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [confirmDel, setConfirmDel] = useState<string | null>(null);
  const [editProject, setEditProject] = useState<string | null>(null);
  const [editForm, setEditForm] = useState<EditForm>({ name: "", description: "", boss: "", workers: "", show_swarm: false });
  const [editSaving, setEditSaving] = useState(false);
  const [editErr, setEditErr] = useState("");
  const [sambaCreds, setSambaCreds] = useState<Record<string, {username: string; password: string} | null>>({});
  const [sambaLoading, setSambaLoading] = useState<Record<string, boolean>>({});
  const [showSambaPw, setShowSambaPw] = useState<Record<string, boolean>>({});
  const [sambaResetting, setSambaResetting] = useState<string | null>(null);

  async function load() {
    try {
      const [p, a] = await Promise.all([api.projects(), api.agents()]);
      setProjects(p as Record<string, ProjectEntry>);
      setAgents(Object.keys(a as Record<string, unknown>));
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
  }, []);

  function refresh() {
    setRefreshing(true);
    load();
  }

  async function loadSambaCreds(id: string) {
    setSambaLoading(l => ({...l, [id]: true}));
    try {
      const data = await api.sambaCreds(id);
      setSambaCreds(c => ({...c, [id]: data}));
      setShowSambaPw(s => ({...s, [id]: true}));
    } catch {
      setSambaCreds(c => ({...c, [id]: null}));
    } finally {
      setSambaLoading(l => ({...l, [id]: false}));
    }
  }

  async function resetSambaPw(id: string) {
    setSambaResetting(id);
    try {
      const data = await api.sambaResetPassword(id);
      setSambaCreds(c => ({...c, [id]: data}));
      setShowSambaPw(s => ({...s, [id]: true}));
    } catch { /* ignore */ } finally {
      setSambaResetting(null);
    }
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setCreating(true);
    setCreateErr("");
    try {
      await api.createProject({
        id: form.id,
        name: form.name,
        description: form.description,
        boss: form.boss,
        workers: form.workers.split(",").map((w) => w.trim()).filter(Boolean),
        samba: form.samba,
      });
      setShowForm(false);
      setForm(EMPTY);
      await load();
    } catch (e) {
      setCreateErr(e instanceof Error ? e.message : t("common.error"));
    } finally {
      setCreating(false);
    }
  }

  async function handleDelete(id: string) {
    setDeleting(id);
    setConfirmDel(null);
    try {
      await api.deleteProject(id);
      setProjects((p) => {
        const n = { ...p };
        delete n[id];
        return n;
      });
    } catch (e) {
      // 404 = bereits gelöscht → trotzdem aus der Liste entfernen
      const msg = e instanceof Error ? e.message : String(e);
      if (msg.includes("404")) {
        setProjects((p) => {
          const n = { ...p };
          delete n[id];
          return n;
        });
      } else {
        setError(e instanceof Error ? e.message : t("common.deleteError"));
      }
    } finally {
      setDeleting(null);
    }
  }

  function openEdit(id: string) {
    const p = projects[id];
    if (!p) return;
    setEditForm({ name: p.name, description: p.description, boss: p.boss, workers: p.workers.join(", "), show_swarm: p.show_swarm });
    setEditErr("");
    setEditProject(id);
  }

  async function handleEdit(e: React.FormEvent) {
    e.preventDefault();
    if (!editProject) return;
    setEditSaving(true); setEditErr("");
    try {
      await api.updateProject(editProject, {
        name: editForm.name,
        description: editForm.description,
        boss: editForm.boss,
        workers: editForm.workers.split(",").map(w => w.trim()).filter(Boolean),
        show_swarm: editForm.show_swarm,
      });
      setEditProject(null);
      await load();
    } catch (e) {
      setEditErr(e instanceof Error ? e.message : t("common.saveError"));
    } finally { setEditSaving(false); }
  }

  const projectList = Object.entries(projects);
  const stats = useMemo(() => {
    const swarm = projectList.filter(([, proj]) => proj.show_swarm).length;
    const workers = projectList.reduce((acc, [, proj]) => acc + proj.workers.length, 0);
    const matrix = projectList.filter(([, proj]) => !!proj.matrix_room).length;
    return [
      { label: t("projects.projectsLabel"), value: projectList.length, note: t("projects.configuredWorkspaces") },
      { label: t("projects.swarm"), value: swarm, note: t("projects.swarmVisible") },
      { label: t("projects.worker"), value: workers, note: t("projects.boundAgents") },
      { label: t("projects.matrix"), value: matrix, note: t("projects.connectedRoom") },
    ];
  }, [projectList, t]);

  return (
    <div className="space-y-6">
      <section className="hero-panel">
        <div className="relative z-10 shell-grid">
          <div className="space-y-5 lg:col-span-8">
            <div className="flex flex-wrap items-center gap-3">
              <span className="status-pill status-pill-ok">
                <Radar className="h-3.5 w-3.5" />
                {projectList.length !== 1
                  ? t("projects.activeCountPlural", { count: projectList.length })
                  : t("projects.activeCount", { count: projectList.length })}
              </span>
              <span className="status-pill">
                <Workflow className="h-3.5 w-3.5" />
                {t("projects.workspacesLabel")}
              </span>
            </div>

            <div>
              <h1 className="shell-title">{t("projects.title")}</h1>
              <p className="shell-copy mt-3 max-w-2xl">
                {t("projects.subtitle")}
              </p>
            </div>
          </div>

          <div className="lg:col-span-4">
            <div className="app-panel app-panel-muted p-5">
              <div className="flex items-center gap-2 text-sm font-medium">
                <FolderKanban className="h-4 w-4 text-primary" />
                {t("projects.projectActions")}
              </div>
              <div className="mt-4 grid gap-2 sm:grid-cols-2">
                <button
                  onClick={refresh}
                  disabled={refreshing}
                  className="inline-flex w-full items-center justify-center gap-2 rounded-2xl border bg-background/70 px-4 py-2 text-sm transition hover:bg-background disabled:opacity-50"
                >
                  <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? "animate-spin" : ""}`} />
                  {t("projects.refresh")}
                </button>
                {isAdmin && (
                  <button
                    onClick={() => setShowForm(true)}
                    className="inline-flex w-full items-center justify-center gap-2 rounded-2xl bg-primary px-4 py-2 text-sm text-primary-foreground transition hover:bg-primary/90"
                  >
                    <Plus className="h-3.5 w-3.5" />
                    {t("projects.newProject")}
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
        <section className="section-card space-y-4">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="metric-kicker">{t("projects.create")}</p>
              <h2 className="mt-2 text-xl font-semibold tracking-tight">{t("projects.createTitle")}</h2>
            </div>
            <button
              onClick={() => {
                setShowForm(false);
                setCreateErr("");
                setForm(EMPTY);
              }}
              className="rounded-2xl border px-4 py-2 text-sm transition hover:bg-accent"
            >
              {t("projects.cancel")}
            </button>
          </div>
          <form onSubmit={handleCreate} className="grid gap-4 md:grid-cols-2">
            <div className="space-y-1.5">
              <label className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">{t("projects.projectId")}</label>
              <input
                value={form.id}
                onChange={(e) => setForm({ ...form, id: e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, "") })}
                placeholder={t("projects.projectIdPlaceholder")}
                required
                className="w-full rounded-2xl border bg-background px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
              />
              <p className="text-xs text-muted-foreground">{t("projects.projectIdHint")}</p>
            </div>
            <div className="space-y-1.5">
              <label className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">{t("projects.name")}</label>
              <input
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder={t("projects.namePlaceholder")}
                required
                className="w-full rounded-2xl border bg-background px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
              />
            </div>
            <div className="space-y-1.5">
              <label className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">{t("projects.bossAgent")}</label>
              <select
                value={form.boss}
                onChange={(e) => setForm({ ...form, boss: e.target.value })}
                required
                className="w-full rounded-2xl border bg-background px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
              >
                <option value="">{t("projects.selectAgent")}</option>
                {agents.map((a) => (
                  <option key={a} value={a}>
                    {a}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1.5">
              <label className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">{t("projects.workerAgents")}</label>
              <input
                value={form.workers}
                onChange={(e) => setForm({ ...form, workers: e.target.value })}
                placeholder={t("projects.workerPlaceholder")}
                className="w-full rounded-2xl border bg-background px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
              />
            </div>
            <div className="space-y-1.5 md:col-span-2">
              <label className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">{t("projects.description")}</label>
              <input
                value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
                placeholder={t("projects.descriptionPlaceholder")}
                className="w-full rounded-2xl border bg-background px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
              />
            </div>
            <div className="md:col-span-2 flex items-start gap-2 rounded-2xl bg-secondary/55 px-4 py-3 text-sm">
              <input
                type="checkbox"
                id="samba"
                checked={form.samba}
                onChange={(e) => setForm({ ...form, samba: e.target.checked })}
                className="h-4 w-4 rounded border"
              />
              <label htmlFor="samba">{t("projects.sambaShare")}</label>
            </div>
            {createErr && <p className="md:col-span-2 text-sm text-destructive">{createErr}</p>}
            <div className="md:col-span-2 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
              <button type="button" onClick={() => { setShowForm(false); setForm(EMPTY); }} className="rounded-2xl border px-4 py-2 text-sm transition hover:bg-accent">
                {t("projects.cancel")}
              </button>
              <button type="submit" disabled={creating} className="rounded-2xl bg-primary px-4 py-2 text-sm text-primary-foreground transition hover:bg-primary/90 disabled:opacity-50">
                {creating ? t("projects.creating") : t("projects.createBtn")}
              </button>
            </div>
          </form>
        </section>
      )}

      {loading && (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="metric-card h-40 animate-pulse" />
          ))}
        </div>
      )}

      {!loading && projectList.length === 0 && !showForm && (
        <div className="section-card py-14 text-center">
          <FolderKanban className="mx-auto h-10 w-10 text-muted-foreground" />
          <p className="mt-4 text-sm text-muted-foreground">{t("projects.noProjects")}</p>
          {isAdmin && (
            <button onClick={() => setShowForm(true)} className="mt-4 inline-flex items-center gap-2 rounded-2xl bg-primary px-4 py-2 text-sm text-primary-foreground transition hover:bg-primary/90">
              <Plus className="h-4 w-4" />
              {t("projects.firstProject")}
            </button>
          )}
        </div>
      )}

      {!loading && projectList.length > 0 && (
        <section className="space-y-4">
          {projectList.map(([id, proj]) => (
            <div key={id} className="app-panel overflow-hidden">
              <div className="p-5 space-y-5">
                <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
                  <div className="flex items-start gap-4">
                    <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-primary/12 text-primary">
                      <FolderKanban className="h-5 w-5" />
                    </div>
                    <div className="space-y-3">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-lg font-semibold tracking-tight">{proj.name}</span>
                        <span className="rounded-full bg-secondary px-2 py-1 text-xs text-secondary-foreground">{id}</span>
                        {proj.show_swarm && <span className="status-pill status-pill-ok">{t("projects.swarmVisible2")}</span>}
                        {proj.matrix_room && <span className="status-pill">{t("projects.matrixActive")}</span>}
                      </div>
                      {proj.description && <p className="max-w-2xl text-sm text-muted-foreground">{proj.description}</p>}
                      <div className="flex flex-wrap gap-2">
                        <span className="status-pill">
                          <Users className="h-3.5 w-3.5" />
                          {t("projects.boss", { name: proj.boss })}
                        </span>
                        <span className="status-pill">
                          <Boxes className="h-3.5 w-3.5" />
                          {t("projects.workerCount", { count: proj.workers.length })}
                        </span>
                        <span className="status-pill">
                          <HardDrive className="h-3.5 w-3.5" />
                          {proj.system_user}
                        </span>
                        <span className="status-pill">
                          <GitBranch className="h-3.5 w-3.5" />
                          {t("projects.filespaceReady")}
                        </span>
                      </div>
                    </div>
                  </div>

                  <div className="grid gap-3 sm:grid-cols-2 xl:w-[26rem]">
                    <button
                      onClick={() => navigate(`/chat/${id}`)}
                      className="flex items-center justify-between rounded-3xl border bg-background/75 px-4 py-3 text-left text-sm transition hover:bg-background"
                    >
                      <span className="flex items-center gap-3">
                        <span className="rounded-2xl bg-primary/12 p-2 text-primary">
                          <MessageSquare className="h-4 w-4" />
                        </span>
                        <span>
                          <span className="block font-medium">{t("projects.chat")}</span>
                          <span className="text-xs text-muted-foreground">{t("projects.chatSubtitle")}</span>
                        </span>
                      </span>
                      <ArrowRight className="h-4 w-4 text-muted-foreground" />
                    </button>
                    <button
                      onClick={() => setAgentlinkProject((p) => (p === id ? null : id))}
                      className={`flex items-center justify-between rounded-3xl border px-4 py-3 text-left text-sm transition ${agentlinkProject === id ? "border-primary/30 bg-primary/10 text-primary" : "bg-background/75 hover:bg-background"}`}
                    >
                      <span className="flex items-center gap-3">
                        <span className={`rounded-2xl p-2 ${agentlinkProject === id ? "bg-primary/15" : "bg-secondary"}`}>
                          <GitMerge className="h-4 w-4" />
                        </span>
                        <span>
                          <span className="block font-medium">{t("projects.agentLink")}</span>
                          <span className="text-xs text-muted-foreground">{t("projects.agentLinkSubtitle")}</span>
                        </span>
                      </span>
                      <ArrowRight className="h-4 w-4 text-muted-foreground" />
                    </button>
                    <button
                      onClick={() => setWebhookProject((p) => (p === id ? null : id))}
                      className={`flex items-center justify-between rounded-3xl border px-4 py-3 text-left text-sm transition ${webhookProject === id ? "border-primary/30 bg-primary/10 text-primary" : "bg-background/75 hover:bg-background"}`}
                    >
                      <span className="flex items-center gap-3">
                        <span className={`rounded-2xl p-2 ${webhookProject === id ? "bg-primary/15" : "bg-secondary"}`}>
                          <Webhook className="h-4 w-4" />
                        </span>
                        <span>
                          <span className="block font-medium">{t("projects.webhooks")}</span>
                          <span className="text-xs text-muted-foreground">{t("projects.webhooksSubtitle")}</span>
                        </span>
                      </span>
                      <ArrowRight className="h-4 w-4 text-muted-foreground" />
                    </button>
                    <a
                      href={`/code/?folder=/projects/${id}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="flex items-center justify-between rounded-3xl border bg-background/75 px-4 py-3 text-left text-sm transition hover:bg-background"
                    >
                      <span className="flex items-center gap-3">
                        <span className="rounded-2xl bg-secondary p-2">
                          <Code2 className="h-4 w-4" />
                        </span>
                        <span>
                          <span className="block font-medium">{t("projects.codeEditor")}</span>
                          <span className="text-xs text-muted-foreground">{t("projects.openInCode")}</span>
                        </span>
                      </span>
                      <ArrowRight className="h-4 w-4 text-muted-foreground" />
                    </a>
                    <div className="rounded-3xl border bg-secondary/50 px-4 py-3">
                      <div className="flex items-center gap-3">
                        <span className="rounded-2xl bg-background p-2 text-foreground/75">
                          <Server className="h-4 w-4" />
                        </span>
                        <div>
                          <p className="text-sm font-medium">{t("projects.runtimeContext")}</p>
                          <p className="text-xs text-muted-foreground">{t("projects.runtimeContextSubtitle")}</p>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>

                <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-[1.2fr_1fr_0.9fr]">
                  <div className="rounded-3xl border bg-background/55 p-4">
                    <p className="metric-kicker">{t("projects.workspace")}</p>
                    <div className="mt-3 space-y-3 text-sm">
                      <div className="flex items-start gap-3">
                        <HardDrive className="mt-0.5 h-4 w-4 text-primary" />
                        <div>
                          <p className="font-medium">{t("projects.filesystem")}</p>
                          <p className="break-all text-muted-foreground">{proj.filesystem}</p>
                        </div>
                      </div>
                      <div className="flex items-start gap-3">
                        <Server className="mt-0.5 h-4 w-4 text-primary" />
                        <div>
                          <p className="font-medium">{t("projects.systemUser")}</p>
                          <p className="text-muted-foreground">{proj.system_user}</p>
                        </div>
                      </div>
                      <div className="flex items-start gap-3">
                        <Hash className="mt-0.5 h-4 w-4 text-primary" />
                        <div>
                          <p className="font-medium">{t("projects.matrixRoom")}</p>
                          <p className="break-all text-muted-foreground">{proj.matrix_room || t("projects.noMatrixRoom")}</p>
                        </div>
                      </div>
                      {proj.system_user && isAdmin && (
                        <div className="flex items-start gap-3">
                          <KeyRound className="mt-0.5 h-4 w-4 text-primary" />
                          <div className="flex-1 min-w-0">
                            <p className="font-medium">Samba-Zugangsdaten</p>
                            {sambaCreds[id] ? (
                              <div className="mt-1 space-y-1">
                                <p className="text-xs text-muted-foreground font-mono">{sambaCreds[id]!.username}</p>
                                <div className="flex items-center gap-2">
                                  <p className="text-xs font-mono text-muted-foreground tracking-wider">
                                    {showSambaPw[id] ? sambaCreds[id]!.password : "••••••••••••"}
                                  </p>
                                  <button type="button" onClick={() => setShowSambaPw(s => ({...s, [id]: !s[id]}))}
                                    className="text-muted-foreground hover:text-foreground transition-colors">
                                    {showSambaPw[id] ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
                                  </button>
                                  <button type="button" onClick={() => resetSambaPw(id)} disabled={sambaResetting === id}
                                    className="text-xs text-muted-foreground hover:text-foreground transition-colors disabled:opacity-50">
                                    {sambaResetting === id ? "…" : "Reset"}
                                  </button>
                                </div>
                              </div>
                            ) : (
                              <button type="button" onClick={() => loadSambaCreds(id)} disabled={sambaLoading[id]}
                                className="mt-0.5 text-xs text-primary hover:underline disabled:opacity-50">
                                {sambaLoading[id] ? "Lade…" : "Zugangsdaten anzeigen"}
                              </button>
                            )}
                          </div>
                        </div>
                      )}
                    </div>
                  </div>

                  <div className="rounded-3xl border bg-background/55 p-4 md:col-span-2 xl:col-span-1">
                    <p className="metric-kicker">{t("projects.team")}</p>
                    <div className="mt-3 space-y-3 text-sm">
                      <div className="rounded-2xl bg-secondary/60 px-3 py-3">
                        <p className="text-xs uppercase tracking-[0.16em] text-muted-foreground">{t("projects.bossLabel")}</p>
                        <p className="mt-1 font-medium">{proj.boss}</p>
                      </div>
                      <div className="rounded-2xl bg-secondary/40 px-3 py-3">
                        <p className="text-xs uppercase tracking-[0.16em] text-muted-foreground">{t("projects.workerLabel")}</p>
                        {proj.workers.length > 0 ? (
                          <div className="mt-2 flex flex-wrap gap-2">
                            {proj.workers.map((worker) => (
                              <span key={worker} className="rounded-full bg-background px-2.5 py-1 text-xs">
                                {worker}
                              </span>
                            ))}
                          </div>
                        ) : (
                          <p className="mt-1 text-muted-foreground">{t("projects.noWorkers")}</p>
                        )}
                      </div>
                    </div>
                  </div>

                  <div className="rounded-3xl border border-destructive/15 bg-destructive/5 p-4">
                    <div className="flex items-center gap-2 text-sm font-medium text-destructive">
                      <ShieldAlert className="h-4 w-4" />
                      {t("projects.dangerZone")}
                    </div>
                    <p className="mt-3 text-sm text-muted-foreground">
                      {t("projects.dangerZoneDesc")}
                    </p>
                    <div className="mt-4 space-y-2">
                      {isAdmin && (
                        <button onClick={() => openEdit(id)} className="inline-flex w-full items-center justify-center gap-2 rounded-2xl border px-3 py-2 text-sm transition hover:bg-accent">
                          <Pencil className="h-4 w-4" />
                          Projekt bearbeiten
                        </button>
                      )}
                      {isAdmin && (confirmDel === id ? (
                        <div className="space-y-2">
                          <button onClick={() => handleDelete(id)} disabled={deleting === id} className="w-full rounded-2xl bg-destructive px-3 py-2 text-sm text-destructive-foreground transition hover:bg-destructive/90 disabled:opacity-50">
                            {t("projects.deleteConfirm")}
                          </button>
                          <button onClick={() => setConfirmDel(null)} className="w-full rounded-2xl border px-3 py-2 text-sm transition hover:bg-accent">
                            {t("projects.cancel")}
                          </button>
                        </div>
                      ) : (
                        <button onClick={() => setConfirmDel(id)} disabled={!!deleting} className="inline-flex w-full items-center justify-center gap-2 rounded-2xl border px-3 py-2 text-sm text-destructive transition hover:border-destructive/30 hover:bg-destructive/10 disabled:opacity-50">
                          <Trash2 className="h-4 w-4" />
                          {t("projects.deleteBtn")}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
              {agentlinkProject === id && <AgentLinkPanel projectId={id} />}
              {webhookProject === id && <WebhooksPanel projectId={id} />}
            </div>
          ))}
        </section>
      )}
    {/* Edit-Dialog */}
    {editProject && (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4">
        <div className="w-full max-w-lg rounded-2xl border bg-card shadow-2xl">
          <div className="flex items-center justify-between border-b px-6 py-4">
            <h2 className="font-semibold">Projekt bearbeiten — <span className="font-mono text-sm text-muted-foreground">{editProject}</span></h2>
            <button onClick={() => setEditProject(null)} className="rounded-lg p-1.5 hover:bg-muted"><X className="h-4 w-4" /></button>
          </div>
          <form onSubmit={handleEdit} className="space-y-4 p-6">
            <div className="space-y-1.5">
              <label className="text-sm font-medium">Name</label>
              <input value={editForm.name} onChange={e => setEditForm(f => ({...f, name: e.target.value}))} required
                className="w-full rounded-xl border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary" />
            </div>
            <div className="space-y-1.5">
              <label className="text-sm font-medium">Beschreibung</label>
              <input value={editForm.description} onChange={e => setEditForm(f => ({...f, description: e.target.value}))}
                className="w-full rounded-xl border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary" />
            </div>
            <div className="space-y-1.5">
              <label className="text-sm font-medium">Boss-Agent</label>
              <select value={editForm.boss} onChange={e => setEditForm(f => ({...f, boss: e.target.value}))} required
                className="w-full rounded-xl border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary">
                <option value="">— Agent wählen —</option>
                {agents.map(a => <option key={a} value={a}>{a}</option>)}
              </select>
            </div>
            <div className="space-y-1.5">
              <label className="text-sm font-medium">Worker-Agenten</label>
              <input value={editForm.workers} onChange={e => setEditForm(f => ({...f, workers: e.target.value}))}
                placeholder="agent1, agent2, agent3"
                className="w-full rounded-xl border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary" />
              <p className="text-xs text-muted-foreground">Kommagetrennt</p>
            </div>
            <label className="flex items-center gap-2 text-sm cursor-pointer">
              <input type="checkbox" checked={editForm.show_swarm} onChange={e => setEditForm(f => ({...f, show_swarm: e.target.checked}))} className="rounded" />
              Swarm-Ansicht anzeigen
            </label>
            {editErr && <p className="text-sm text-destructive">{editErr}</p>}
            <div className="flex gap-2 pt-1">
              <button type="submit" disabled={editSaving || !editForm.boss}
                className="flex-1 flex items-center justify-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors">
                <Save className="h-4 w-4" />
                {editSaving ? t("common.saving") : t("common.save")}
              </button>
              <button type="button" onClick={() => setEditProject(null)}
                className="rounded-xl border px-4 py-2.5 text-sm hover:bg-accent transition-colors">
                {t("common.cancel")}
              </button>
            </div>
          </form>
        </div>
      </div>
    )}
    </div>
  );
}
