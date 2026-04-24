import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Bot,
  FolderKanban,
  Plus,
  RefreshCw,
  HardDrive,
  Hash,
  Users,
  Webhook,
  GitMerge,
  Trash2,
  ChevronDown,
  Radar,
  Workflow,
  MessageSquare,
  ShieldAlert,
  Boxes,
  Pencil,
  X,
  Save,
  Eye,
  EyeOff,
  KeyRound,
  Code2,
  GitBranch,
  Calendar,
  Settings,
  Phone,
  Loader2,
} from "lucide-react";
import { api } from "@/lib/api";
import { WebhooksPanel } from "@/components/WebhooksPanel";
import { AgentLinkPanel } from "@/components/AgentLinkPanel";
import { ProjectSettingsPanel } from "@/components/ProjectSettingsPanel";
import { useAuth } from "@/hooks/useAuth";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import SchedulesPage from "@/pages/SchedulesPage";

interface ProjectEntry {
  name: string;
  description: string;
  boss: string;
  workers: string[];
  members: string[];
  matrix_room: string;
  filesystem: string;
  system_user: string;
  show_swarm: boolean;
  agents?: { boss: string; workers: string[] };
}

interface CreateForm {
  id: string;
  name: string;
  description: string;
  samba: boolean;
  githubRepo: string;
  gitClone: boolean;
  gitBranch: string;
  gitToken: string;
  createAgent: boolean;
  agentName: string;
}

interface EditForm {
  name: string;
  description: string;
  members: string;
}

const EMPTY: CreateForm = { id: "", name: "", description: "", samba: true, githubRepo: "", gitClone: false, gitBranch: "main", gitToken: "", createAgent: true, agentName: "" };

function ProjectsContent() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { isAdmin } = useAuth();
  const [projects, setProjects] = useState<Record<string, ProjectEntry>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<CreateForm>(EMPTY);
  const [creating, setCreating] = useState(false);
  const [createErr, setCreateErr] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [webhookProject, setWebhookProject] = useState<string | null>(null);
  const [agentlinkProject, setAgentlinkProject] = useState<string | null>(null);
  const [settingsProject, setSettingsProject] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [confirmDel, setConfirmDel] = useState<string | null>(null);
  const [editProject, setEditProject] = useState<string | null>(null);
  const [editForm, setEditForm] = useState<EditForm>({ name: "", description: "", members: "" });
  const [creatingAgent, setCreatingAgent] = useState<string | null>(null);
  const [editSaving, setEditSaving] = useState(false);
  const [editErr, setEditErr] = useState("");
  const [sambaCreds, setSambaCreds] = useState<Record<string, {username: string; password: string} | null>>({});
  const [sambaLoading, setSambaLoading] = useState<Record<string, boolean>>({});
  const [showSambaPw, setShowSambaPw] = useState<Record<string, boolean>>({});
  const [showCodePw, setShowCodePw] = useState(false);
  const [expandedProjects, setExpandedProjects] = useState<Record<string, boolean>>({});
  const [sambaResetting, setSambaResetting] = useState<string | null>(null);
  const [codeserverPassword, setCodeserverPassword] = useState<string | null>(null);
  const [giteaCreds, setGiteaCreds] = useState<{url:string;username:string;password:string;token:string}|null>(null);
  const [giteaLoading, setGiteaLoading] = useState(false);
  const [showGiteaPw, setShowGiteaPw] = useState(false);
  const [showGiteaToken, setShowGiteaToken] = useState(false);

  async function load() {
    try {
      const p = await api.projects();
      setProjects(p as Record<string, ProjectEntry>);
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
    fetch("/api/admin/codeserver/status", { credentials: "include" })
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d?.password) setCodeserverPassword(d.password); })
      .catch(e => console.error("Failed to load code-server status", e));
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

  async function loadGiteaCreds() {
    setGiteaLoading(true);
    try {
      const data = await api.get<{url:string;username:string;password:string;token:string}>("/gitea/credentials");
      setGiteaCreds(data);
      setShowGiteaPw(true);
    } catch { /* ignore */ } finally {
      setGiteaLoading(false);
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

  async function createAgentForProject(projectId: string, projectName: string) {
    setCreatingAgent(projectId);
    try {
      await api.post("/admin/agents", {
        id: projectId,
        identity: projectName,
        type: "boss",
      });
      await api.put(`/projects/${projectId}/settings`, {
        agents_boss: projectId,
      });
      await load();
    } catch (e) {
      alert("Agent anlegen fehlgeschlagen: " + (e instanceof Error ? e.message : String(e)));
    } finally {
      setCreatingAgent(null);
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
        create_agent: form.createAgent,
        agent_name: form.agentName || form.id,
        samba: form.samba,
        github_repo: form.githubRepo.trim(),
      });
      // Git Clone nach Erstellung
      if (form.gitClone && form.githubRepo.trim()) {
        try {
          let cloneUrl = form.githubRepo.trim();
          if (!cloneUrl.startsWith("http")) cloneUrl = `https://github.com/${cloneUrl}`;
          if (!cloneUrl.endsWith(".git")) cloneUrl += ".git";
          if (form.gitToken.trim()) cloneUrl = cloneUrl.replace("https://", `https://${form.gitToken.trim()}@`);
          await api.post(`/projects/${form.id}/git-clone`, { url: cloneUrl, branch: form.gitBranch || "main" });
        } catch (cloneErr: any) {
          setCreateErr(`Projekt erstellt, aber Git-Clone fehlgeschlagen: ${cloneErr.message}`);
          setCreating(false);
          await load();
          return;
        }
      }
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
    setEditForm({ name: p.name, description: p.description, members: (p.members || []).join(", ") });
    setEditErr("");
    setEditProject(id);
  }

  async function handleEdit(e: React.FormEvent) {
    e.preventDefault();
    if (!editProject) return;
    setEditSaving(true); setEditErr("");
    try {
      await api.put(`/projects/${editProject}/settings`, {
        name: editForm.name,
        description: editForm.description,
        members: editForm.members.split(",").map(m => m.trim()).filter(Boolean),
      });
      setEditProject(null);
      await load();
    } catch (e) {
      setEditErr(e instanceof Error ? e.message : t("common.saveError"));
    } finally { setEditSaving(false); }
  }

  const projectList = Object.entries(projects);
  const stats = useMemo(() => {
    const totalMembers = projectList.reduce((acc, [, proj]) => acc + (proj.members || []).length, 0);
    return [
      { label: t("projects.projectsLabel"), value: projectList.length, note: t("projects.configuredWorkspaces") },
      { label: "Mitglieder", value: totalMembers, note: "Zugewiesene User" },
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
                    onClick={() => navigate("/projects/new")}
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

      <div className="mb-6 rounded-xl border bg-muted/30 p-4 space-y-2">
        <h3 className="text-sm font-semibold">{t("projects.infoTitle")}</h3>
        <p className="text-xs text-muted-foreground leading-relaxed">{t("projects.infoText")}</p>
      </div>

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
              <label className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">{t("projects.description")}</label>
              <input
                value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
                placeholder={t("projects.descriptionPlaceholder")}
                className="w-full rounded-2xl border bg-background px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
              />
            </div>
            {/* Master-Agent Sektion */}
            <div className="md:col-span-2 space-y-3 rounded-2xl border bg-secondary/30 px-4 py-3">
              <div className="flex items-center gap-3">
                <label className="flex items-center gap-2 text-sm font-medium cursor-pointer">
                  <input type="checkbox" checked={form.createAgent} onChange={e => setForm({ ...form, createAgent: e.target.checked })} className="h-4 w-4 rounded border" />
                  Master-Agent automatisch erstellen
                </label>
              </div>
              {form.createAgent && (
                <div className="space-y-1.5">
                  <label className="text-xs text-muted-foreground">Agent-Name <span className="opacity-50">(leer = Projekt-ID)</span></label>
                  <input value={form.agentName} onChange={e => setForm({ ...form, agentName: e.target.value })}
                    placeholder={form.id || "Mein-Projekt-Agent"}
                    className="w-full rounded-xl border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary" />
                </div>
              )}
            </div>

            <div className="md:col-span-2 space-y-3 rounded-2xl border bg-secondary/30 px-4 py-3">
              <div className="space-y-1.5">
                <label className="text-sm font-medium">Git-Repository <span className="text-muted-foreground font-normal">(optional)</span></label>
                <input value={form.githubRepo} onChange={e => setForm({ ...form, githubRepo: e.target.value })}
                  placeholder="owner/repo oder https://github.com/owner/repo"
                  className="w-full rounded-xl border bg-background px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-primary" />
              </div>
              {form.githubRepo.trim() && (
                <>
                  <label className="flex items-center gap-2 text-sm cursor-pointer">
                    <input type="checkbox" checked={form.gitClone} onChange={e => setForm({ ...form, gitClone: e.target.checked })} className="rounded border" />
                    Repository automatisch klonen
                  </label>
                  {form.gitClone && (
                    <div className="grid grid-cols-2 gap-3">
                      <div className="space-y-1">
                        <label className="text-xs text-muted-foreground">Branch</label>
                        <input value={form.gitBranch} onChange={e => setForm({ ...form, gitBranch: e.target.value })}
                          placeholder="main" className="w-full rounded-xl border bg-background px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-primary" />
                      </div>
                      <div className="space-y-1">
                        <label className="text-xs text-muted-foreground">Token <span className="opacity-50">(private Repos)</span></label>
                        <input type="password" value={form.gitToken} onChange={e => setForm({ ...form, gitToken: e.target.value })}
                          placeholder="ghp_... oder leer" className="w-full rounded-xl border bg-background px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-primary" />
                      </div>
                    </div>
                  )}
                </>
              )}
            </div>
            <div className="md:col-span-2 flex items-start gap-2 rounded-2xl bg-secondary/55 px-4 py-3 text-sm">
              <input type="checkbox" id="samba" checked={form.samba} onChange={(e) => setForm({ ...form, samba: e.target.checked })} className="h-4 w-4 rounded border" />
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
            <button onClick={() => navigate("/projects/new")} className="mt-4 inline-flex items-center gap-2 rounded-2xl bg-primary px-4 py-2 text-sm text-primary-foreground transition hover:bg-primary/90">
              <Plus className="h-4 w-4" />
              {t("projects.firstProject")}
            </button>
          )}
        </div>
      )}

      {!loading && projectList.length > 0 && (
        <section className="space-y-2">
          {projectList.map(([id, proj]) => {
            const expanded = !!expandedProjects[id];
            return (
            <div key={id} className="app-panel overflow-hidden">
              {/* Kompakte Hauptzeile */}
              <div className="flex items-center gap-3 px-4 py-3">
                <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-primary/12 text-primary">
                  <FolderKanban className="h-4 w-4" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="font-semibold">{proj.name}</span>
                    <span className="rounded-full bg-secondary px-1.5 py-0.5 text-[11px] text-secondary-foreground">{id}</span>
                    {proj.matrix_room && <span className="status-pill">{t("projects.matrixActive")}</span>}
                    {proj.agents?.boss && <span className="rounded-full bg-indigo-900/40 px-1.5 py-0.5 text-[11px] text-indigo-300">Agent: {proj.agents.boss}</span>}
                    {!proj.agents?.boss && (
                      <button
                        onClick={() => createAgentForProject(id, proj.name)}
                        disabled={creatingAgent === id}
                        className="flex items-center gap-1 rounded-full border border-dashed border-muted-foreground/30 px-1.5 py-0.5 text-[11px] text-muted-foreground/50 hover:border-primary/50 hover:text-primary transition-colors disabled:opacity-40"
                      >
                        {creatingAgent === id ? <Loader2 className="h-3 w-3 animate-spin" /> : <Plus className="h-3 w-3" />}
                        Agent anlegen
                      </button>
                    )}
                  </div>
                  <div className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-muted-foreground">
                    <span className="flex items-center gap-1"><Users className="h-3 w-3" />{(proj.members || []).length || 1} {(proj.members || []).length === 1 ? "Mitglied" : "Mitglieder"}</span>
                    <span className="flex items-center gap-1"><HardDrive className="h-3 w-3" />{proj.system_user}</span>
                    {proj.description && <span className="hidden sm:inline truncate max-w-xs">{proj.description}</span>}
                  </div>
                </div>
                {/* Aktions-Buttons */}
                <div className="flex shrink-0 items-center gap-1">
                  <button
                    onClick={() => navigate(`/chat/${id}`)}
                    className="flex items-center gap-1.5 rounded-xl bg-primary/10 px-3 py-1.5 text-xs font-medium text-primary transition hover:bg-primary/20"
                  >
                    <MessageSquare className="h-3.5 w-3.5" />
                    {t("projects.chat")}
                  </button>
                  <a
                    href={`/code/?folder=/projects/${id}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center gap-1.5 rounded-xl border px-3 py-1.5 text-xs font-medium transition hover:bg-accent"
                  >
                    <Code2 className="h-3.5 w-3.5" />
                    {t("projects.codeEditor")}
                  </a>
                  <button
                    onClick={() => setSettingsProject((p) => (p === id ? null : id))}
                    className={`flex items-center gap-1.5 rounded-xl border px-3 py-1.5 text-xs font-medium transition ${settingsProject === id ? "border-primary/30 bg-primary/10 text-primary" : "hover:bg-accent"}`}
                  >
                    <Settings className="h-3.5 w-3.5" />
                    Settings
                  </button>
                  <button
                    onClick={() => navigate(`/blueprint?project=${id}`)}
                    className="flex items-center gap-1.5 rounded-xl border px-3 py-1.5 text-xs font-medium transition hover:bg-accent"
                  >
                    <Workflow className="h-3.5 w-3.5" />
                    Blueprint
                  </button>
                  <button
                    onClick={() => setAgentlinkProject((p) => (p === id ? null : id))}
                    className={`flex items-center gap-1.5 rounded-xl border px-3 py-1.5 text-xs font-medium transition ${agentlinkProject === id ? "border-primary/30 bg-primary/10 text-primary" : "hover:bg-accent"}`}
                  >
                    <GitMerge className="h-3.5 w-3.5" />
                    AgentLink
                  </button>
                  <button
                    onClick={() => setWebhookProject((p) => (p === id ? null : id))}
                    className={`flex items-center gap-1.5 rounded-xl border px-3 py-1.5 text-xs font-medium transition ${webhookProject === id ? "border-primary/30 bg-primary/10 text-primary" : "hover:bg-accent"}`}
                  >
                    <Webhook className="h-3.5 w-3.5" />
                    Webhooks
                  </button>
                  <button
                    onClick={() => setExpandedProjects(e => ({...e, [id]: !e[id]}))}
                    className="ml-1 rounded-xl border p-1.5 text-muted-foreground transition hover:bg-accent hover:text-foreground"
                    title={expanded ? "Details einklappen" : "Details anzeigen"}
                  >
                    <ChevronDown className={`h-3.5 w-3.5 transition-transform ${expanded ? "rotate-180" : ""}`} />
                  </button>
                </div>
              </div>

              {/* Aufklappbarer Detail-Bereich */}
              {expanded && (
                <div className="border-t px-4 pb-4 pt-3">
                  <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-[1.2fr_1fr_0.9fr]">
                    {/* Workspace */}
                    <div className="rounded-2xl border bg-background/55 p-3">
                      <p className="metric-kicker">{t("projects.workspace")}</p>
                      <div className="mt-2 space-y-2 text-sm">
                        <div className="flex items-start gap-2">
                          <HardDrive className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" />
                          <div className="min-w-0">
                            <p className="text-xs font-medium">{t("projects.filesystem")}</p>
                            <p className="break-all text-xs text-muted-foreground">{proj.filesystem}</p>
                          </div>
                        </div>
                        <div className="flex items-start gap-2">
                          <Hash className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" />
                          <div className="min-w-0">
                            <p className="text-xs font-medium">{t("projects.matrixRoom")}</p>
                            <p className="break-all text-xs text-muted-foreground">{proj.matrix_room || t("projects.noMatrixRoom")}</p>
                          </div>
                        </div>
                        {proj.system_user && isAdmin && (
                          <div className="flex items-start gap-2">
                            <KeyRound className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" />
                            <div className="flex-1 min-w-0">
                              <p className="text-xs font-medium">Samba-Zugangsdaten</p>
                              {sambaCreds[id] ? (
                                <div className="mt-0.5 space-y-0.5">
                                  <p className="text-xs text-muted-foreground font-mono">{sambaCreds[id]!.username}</p>
                                  <div className="flex items-center gap-1.5">
                                    <p className="text-xs font-mono text-muted-foreground tracking-wider">
                                      {showSambaPw[id] ? sambaCreds[id]!.password : "••••••••••••"}
                                    </p>
                                    <button type="button" onClick={() => setShowSambaPw(s => ({...s, [id]: !s[id]}))}
                                      className="text-muted-foreground hover:text-foreground transition-colors">
                                      {showSambaPw[id] ? <EyeOff className="h-3 w-3" /> : <Eye className="h-3 w-3" />}
                                    </button>
                                    <button type="button" onClick={() => resetSambaPw(id)} disabled={sambaResetting === id}
                                      className="text-xs text-muted-foreground hover:text-foreground transition-colors disabled:opacity-50">
                                      {sambaResetting === id ? "…" : "Reset"}
                                    </button>
                                  </div>
                                </div>
                              ) : (
                                <button type="button" onClick={() => loadSambaCreds(id)} disabled={sambaLoading[id]}
                                  className="text-xs text-primary hover:underline disabled:opacity-50">
                                  {sambaLoading[id] ? "Lade…" : "Zugangsdaten anzeigen"}
                                </button>
                              )}
                            </div>
                          </div>
                        )}
                        {codeserverPassword && isAdmin && (
                          <div className="flex items-start gap-2">
                            <Code2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" />
                            <div className="flex-1 min-w-0">
                              <p className="text-xs font-medium">Code-Editor Zugangsdaten</p>
                              {showCodePw ? (
                                <div className="mt-0.5 flex items-center gap-1.5">
                                  <p className="text-xs font-mono text-muted-foreground select-all">{codeserverPassword}</p>
                                  <button type="button" onClick={() => setShowCodePw(false)}
                                    className="text-muted-foreground hover:text-foreground transition-colors">
                                    <EyeOff className="h-3 w-3" />
                                  </button>
                                </div>
                              ) : (
                                <button type="button" onClick={() => setShowCodePw(true)}
                                  className="text-xs text-primary hover:underline">
                                  Zugangsdaten anzeigen
                                </button>
                              )}
                            </div>
                          </div>
                        )}
                        {isAdmin && (
                          <div className="flex items-start gap-2">
                            <GitBranch className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" />
                            <div className="flex-1 min-w-0">
                              <p className="text-xs font-medium">Gitea-Zugangsdaten</p>
                              {giteaCreds ? (
                                <div className="mt-0.5 space-y-0.5">
                                  <p className="text-xs text-muted-foreground font-mono">{giteaCreds.username}</p>
                                  <div className="flex items-center gap-1.5">
                                    <p className="text-xs font-mono text-muted-foreground tracking-wider">
                                      {showGiteaPw ? giteaCreds.password : "••••••••••••"}
                                    </p>
                                    <button type="button" onClick={() => setShowGiteaPw(v => !v)}
                                      className="text-muted-foreground hover:text-foreground transition-colors">
                                      {showGiteaPw ? <EyeOff className="h-3 w-3" /> : <Eye className="h-3 w-3" />}
                                    </button>
                                  </div>
                                  <p className="text-[10px] text-muted-foreground/60 font-mono truncate">
                                    Token: {showGiteaToken ? giteaCreds.token : giteaCreds.token.slice(0,8)+"…"}
                                    <button type="button" onClick={() => setShowGiteaToken(v => !v)}
                                      className="ml-1 text-muted-foreground hover:text-foreground transition-colors align-middle">
                                      {showGiteaToken ? <EyeOff className="h-2.5 w-2.5 inline" /> : <Eye className="h-2.5 w-2.5 inline" />}
                                    </button>
                                  </p>
                                  <a href={giteaCreds.url} target="_blank" rel="noreferrer"
                                    className="text-[10px] text-primary hover:underline truncate block">{giteaCreds.url}</a>
                                </div>
                              ) : (
                                <button type="button" onClick={loadGiteaCreds} disabled={giteaLoading}
                                  className="text-xs text-primary hover:underline disabled:opacity-50">
                                  {giteaLoading ? "Lade…" : "Zugangsdaten anzeigen"}
                                </button>
                              )}
                            </div>
                          </div>
                        )}
                      </div>
                    </div>

                    {/* Team */}
                    <div className="rounded-2xl border bg-background/55 p-3 md:col-span-1">
                      <p className="metric-kicker">Mitglieder</p>
                      <div className="mt-2 space-y-2 text-sm">
                        <div className="rounded-xl bg-secondary/60 px-2.5 py-2">
                          {(proj.members || []).length > 0 ? (
                            <div className="flex flex-wrap gap-1">
                              {(proj.members || []).map((m) => (
                                <span key={m} className="rounded-full bg-background px-2 py-0.5 text-xs">{m}</span>
                              ))}
                            </div>
                          ) : (
                            <p className="text-xs text-muted-foreground">Alle User haben Zugriff</p>
                          )}
                        </div>
                      </div>
                    </div>

                    {/* Danger Zone */}
                    {isAdmin && (
                      <div className="rounded-2xl border border-destructive/15 bg-destructive/5 p-3">
                        <div className="flex items-center gap-2 text-sm font-medium text-destructive">
                          <ShieldAlert className="h-3.5 w-3.5" />
                          {t("projects.dangerZone")}
                        </div>
                        <div className="mt-3 space-y-2">
                          <button onClick={() => openEdit(id)} className="inline-flex w-full items-center justify-center gap-2 rounded-xl border px-3 py-1.5 text-xs transition hover:bg-accent">
                            <Pencil className="h-3.5 w-3.5" />
                            Projekt bearbeiten
                          </button>
                          {confirmDel === id ? (
                            <div className="space-y-1.5">
                              <button onClick={() => handleDelete(id)} disabled={deleting === id} className="w-full rounded-xl bg-destructive px-3 py-1.5 text-xs text-destructive-foreground transition hover:bg-destructive/90 disabled:opacity-50">
                                {t("projects.deleteConfirm")}
                              </button>
                              <button onClick={() => setConfirmDel(null)} className="w-full rounded-xl border px-3 py-1.5 text-xs transition hover:bg-accent">
                                {t("projects.cancel")}
                              </button>
                            </div>
                          ) : (
                            <button onClick={() => setConfirmDel(id)} disabled={!!deleting} className="inline-flex w-full items-center justify-center gap-2 rounded-xl border px-3 py-1.5 text-xs text-destructive transition hover:border-destructive/30 hover:bg-destructive/10 disabled:opacity-50">
                              <Trash2 className="h-3.5 w-3.5" />
                              {t("projects.deleteBtn")}
                            </button>
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              )}

              {settingsProject === id && <ProjectSettingsPanel projectId={id} onClose={() => setSettingsProject(null)} />}
              {agentlinkProject === id && <AgentLinkPanel projectId={id} />}
              {webhookProject === id && <WebhooksPanel projectId={id} />}
            </div>
            );
          })}
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
              <label className="text-sm font-medium">Mitglieder</label>
              <input value={editForm.members} onChange={e => setEditForm(f => ({...f, members: e.target.value}))}
                placeholder="admin, bianca, till"
                className="w-full rounded-xl border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary" />
              <p className="text-xs text-muted-foreground">Usernames kommagetrennt — wer Zugriff auf dieses Projekt hat</p>
            </div>
            {editErr && <p className="text-sm text-destructive">{editErr}</p>}
            <div className="flex gap-2 pt-1">
              <button type="submit" disabled={editSaving}
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

// ---------------------------------------------------------------- tab wrapper

type ProjectsTabId = "projects" | "schedules";

export function ProjectsPage() {
  const { t } = useTranslation();
  const [active, setActive] = useState<ProjectsTabId>("projects");

  const TABS: { id: ProjectsTabId; label: string; icon: React.ElementType }[] = useMemo(() => [
    { id: "projects",  label: t("projects.tabProjects"),  icon: FolderKanban },
    { id: "schedules", label: t("projects.tabSchedules"), icon: Calendar },
  ], [t]);

  return (
    <div className="flex flex-col h-full">
      <div className="px-6 pt-6 pb-0 border-b border-border">
        <div className="flex items-center gap-2 mb-1">
          <FolderKanban size={20} className="text-muted-foreground" />
          <h1 className="text-lg font-semibold text-foreground">{t("projects.title", { defaultValue: "Projekte" })}</h1>
        </div>
        <p className="text-xs text-muted-foreground mb-4">{t("pageDesc.projects", { defaultValue: "" })}</p>
        <div className="flex gap-1 overflow-x-auto scrollbar-none pb-px">
          {TABS.map(tab => (
            <button
              key={tab.id}
              onClick={() => setActive(tab.id)}
              className={cn(
                "flex items-center gap-2 px-4 py-2.5 text-sm font-medium rounded-t-lg transition-colors border-b-2 -mb-px",
                active === tab.id
                  ? "border-primary text-foreground bg-background"
                  : "border-transparent text-muted-foreground hover:text-foreground hover:bg-muted"
              )}
            >
              <tab.icon size={14} />
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      <div className="flex-1 overflow-auto">
        {active === "projects" ? <ProjectsContent /> : <SchedulesPage />}
      </div>
    </div>
  );
}
