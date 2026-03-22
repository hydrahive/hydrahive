import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { FolderKanban, Plus, RefreshCw, HardDrive, Hash, Users, Webhook, GitMerge, Trash2, ArrowRight, Radar, Workflow } from "lucide-react";
import { api } from "@/lib/api";
import { WebhooksPanel } from "@/components/WebhooksPanel";
import { AgentLinkPanel } from "@/components/AgentLinkPanel";
import { useAuth } from "@/hooks/useAuth";

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

const EMPTY: CreateForm = { id: "", name: "", description: "", boss: "", workers: "", samba: true };

export function ProjectsPage() {
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

  async function load() {
    try {
      const [p, a] = await Promise.all([api.projects(), api.agents()]);
      setProjects(p as Record<string, ProjectEntry>);
      setAgents(Object.keys(a as Record<string, unknown>));
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Fehler beim Laden");
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
      setCreateErr(e instanceof Error ? e.message : "Fehler");
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
      setError(e instanceof Error ? e.message : "Fehler beim Loeschen");
    } finally {
      setDeleting(null);
    }
  }

  const projectList = Object.entries(projects);
  const stats = useMemo(() => {
    const swarm = projectList.filter(([, proj]) => proj.show_swarm).length;
    const workers = projectList.reduce((acc, [, proj]) => acc + proj.workers.length, 0);
    return [
      { label: "Projekte", value: projectList.length, note: "Konfigurierte Flaechen" },
      { label: "Swarm", value: swarm, note: "Mit Worker-Sicht aktiv" },
      { label: "Worker", value: workers, note: "Delegierte Agenten" },
    ];
  }, [projectList]);

  return (
    <div className="space-y-6">
      <section className="hero-panel">
        <div className="relative z-10 shell-grid">
          <div className="space-y-5 lg:col-span-8">
            <div className="flex flex-wrap items-center gap-3">
              <span className="status-pill status-pill-ok">
                <Radar className="h-3.5 w-3.5" />
                {projectList.length} Projekt{projectList.length !== 1 ? "e" : ""} aktiv
              </span>
              <span className="status-pill">
                <Workflow className="h-3.5 w-3.5" />
                Queue-, Handoff- und Webhook-Flaechen gebuendelt
              </span>
            </div>

            <div>
              <h1 className="shell-title">Projekte als steuerbare Arbeitsraeume</h1>
              <p className="shell-copy mt-3 max-w-2xl">
                Projekte verbinden Agenten, Filesystem, Matrix, Handoffs und Webhooks. Diese Seite ist jetzt auf dieselbe
                Shell gezogen wie das Dashboard: klarere Kopfstruktur, bessere Karten und weniger visuelles Rauschen.
              </p>
            </div>
          </div>

          <div className="lg:col-span-4">
            <div className="app-panel app-panel-muted p-5">
              <div className="flex items-center gap-2 text-sm font-medium">
                <FolderKanban className="h-4 w-4 text-primary" />
                Projektaktionen
              </div>
              <div className="mt-4 flex flex-wrap gap-2">
                <button
                  onClick={refresh}
                  disabled={refreshing}
                  className="inline-flex items-center gap-2 rounded-2xl border bg-background/70 px-4 py-2 text-sm transition hover:bg-background disabled:opacity-50"
                >
                  <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? "animate-spin" : ""}`} />
                  Aktualisieren
                </button>
                {isAdmin && (
                  <button
                    onClick={() => setShowForm(true)}
                    className="inline-flex items-center gap-2 rounded-2xl bg-primary px-4 py-2 text-sm text-primary-foreground transition hover:bg-primary/90"
                  >
                    <Plus className="h-3.5 w-3.5" />
                    Neues Projekt
                  </button>
                )}
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="grid gap-4 md:grid-cols-3">
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
              <p className="metric-kicker">Anlage</p>
              <h2 className="mt-2 text-xl font-semibold tracking-tight">Neues Projekt anlegen</h2>
            </div>
            <button
              onClick={() => {
                setShowForm(false);
                setCreateErr("");
                setForm(EMPTY);
              }}
              className="rounded-2xl border px-4 py-2 text-sm transition hover:bg-accent"
            >
              Abbrechen
            </button>
          </div>
          <form onSubmit={handleCreate} className="grid gap-4 md:grid-cols-2">
            <div className="space-y-1.5">
              <label className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">Projekt-ID *</label>
              <input
                value={form.id}
                onChange={(e) => setForm({ ...form, id: e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, "") })}
                placeholder="z.B. buchhaltung"
                required
                className="w-full rounded-2xl border bg-background px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
              />
              <p className="text-xs text-muted-foreground">Nur a-z, 0-9, _ und -</p>
            </div>
            <div className="space-y-1.5">
              <label className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">Name *</label>
              <input
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="Anzeigename"
                required
                className="w-full rounded-2xl border bg-background px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
              />
            </div>
            <div className="space-y-1.5">
              <label className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">Boss-Agent *</label>
              <select
                value={form.boss}
                onChange={(e) => setForm({ ...form, boss: e.target.value })}
                required
                className="w-full rounded-2xl border bg-background px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
              >
                <option value="">Agent waehlen...</option>
                {agents.map((a) => (
                  <option key={a} value={a}>
                    {a}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1.5">
              <label className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">Worker-Agenten</label>
              <input
                value={form.workers}
                onChange={(e) => setForm({ ...form, workers: e.target.value })}
                placeholder="agent1, agent2"
                className="w-full rounded-2xl border bg-background px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
              />
            </div>
            <div className="space-y-1.5 md:col-span-2">
              <label className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">Beschreibung</label>
              <input
                value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
                placeholder="Optionale Beschreibung"
                className="w-full rounded-2xl border bg-background px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
              />
            </div>
            <div className="md:col-span-2 flex items-center gap-2 rounded-2xl bg-secondary/55 px-4 py-3 text-sm">
              <input
                type="checkbox"
                id="samba"
                checked={form.samba}
                onChange={(e) => setForm({ ...form, samba: e.target.checked })}
                className="h-4 w-4 rounded border"
              />
              <label htmlFor="samba">Samba-Freigabe einrichten</label>
            </div>
            {createErr && <p className="md:col-span-2 text-sm text-destructive">{createErr}</p>}
            <div className="md:col-span-2 flex justify-end gap-2">
              <button type="button" onClick={() => { setShowForm(false); setForm(EMPTY); }} className="rounded-2xl border px-4 py-2 text-sm transition hover:bg-accent">
                Abbrechen
              </button>
              <button type="submit" disabled={creating} className="rounded-2xl bg-primary px-4 py-2 text-sm text-primary-foreground transition hover:bg-primary/90 disabled:opacity-50">
                {creating ? "Wird angelegt..." : "Projekt anlegen"}
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
          <p className="mt-4 text-sm text-muted-foreground">Noch keine Projekte. Lege ein erstes Projekt an.</p>
          {isAdmin && (
            <button onClick={() => setShowForm(true)} className="mt-4 inline-flex items-center gap-2 rounded-2xl bg-primary px-4 py-2 text-sm text-primary-foreground transition hover:bg-primary/90">
              <Plus className="h-4 w-4" />
              Erstes Projekt anlegen
            </button>
          )}
        </div>
      )}

      {!loading && projectList.length > 0 && (
        <section className="space-y-4">
          {projectList.map(([id, proj]) => (
            <div key={id} className="app-panel overflow-hidden">
              <div className="p-5">
                <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
                  <div className="flex items-start gap-4">
                    <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-primary/12 text-primary">
                      <FolderKanban className="h-5 w-5" />
                    </div>
                    <div className="space-y-2">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-lg font-semibold tracking-tight">{proj.name}</span>
                        <span className="rounded-full bg-secondary px-2 py-1 text-xs text-secondary-foreground">{id}</span>
                        {proj.show_swarm && <span className="status-pill status-pill-ok">Swarm sichtbar</span>}
                      </div>
                      {proj.description && <p className="max-w-2xl text-sm text-muted-foreground">{proj.description}</p>}
                      <div className="grid gap-3 pt-1 text-sm text-muted-foreground md:grid-cols-3">
                        <div className="flex items-center gap-2">
                          <Users className="h-4 w-4" />
                          <span>{proj.boss}{proj.workers.length > 0 ? ` + ${proj.workers.length} Worker` : ""}</span>
                        </div>
                        <div className="flex items-center gap-2">
                          <HardDrive className="h-4 w-4" />
                          <span>{proj.system_user}</span>
                        </div>
                        <div className="flex items-center gap-2">
                          <Hash className="h-4 w-4" />
                          <span className="truncate">{proj.matrix_room || "kein Room"}</span>
                        </div>
                      </div>
                    </div>
                  </div>

                  <div className="flex flex-wrap gap-2 xl:max-w-[24rem] xl:justify-end">
                    <button
                      onClick={() => setAgentlinkProject((p) => (p === id ? null : id))}
                      className={`inline-flex items-center gap-2 rounded-2xl border px-3 py-2 text-sm transition ${agentlinkProject === id ? "bg-primary/10 text-primary" : "hover:bg-accent"}`}
                    >
                      <GitMerge className="h-4 w-4" />
                      AgentLink
                    </button>
                    <button
                      onClick={() => setWebhookProject((p) => (p === id ? null : id))}
                      className={`inline-flex items-center gap-2 rounded-2xl border px-3 py-2 text-sm transition ${webhookProject === id ? "bg-primary/10 text-primary" : "hover:bg-accent"}`}
                    >
                      <Webhook className="h-4 w-4" />
                      Webhooks
                    </button>
                    <button onClick={() => navigate(`/chat/${id}`)} className="inline-flex items-center gap-2 rounded-2xl border bg-background/70 px-3 py-2 text-sm transition hover:bg-background">
                      Chat oeffnen
                      <ArrowRight className="h-4 w-4" />
                    </button>
                    {isAdmin && (confirmDel === id ? (
                      <span className="flex items-center gap-2">
                        <button onClick={() => handleDelete(id)} disabled={deleting === id} className="rounded-2xl bg-destructive px-3 py-2 text-xs text-destructive-foreground transition hover:bg-destructive/90 disabled:opacity-50">
                          Ja, loeschen
                        </button>
                        <button onClick={() => setConfirmDel(null)} className="rounded-2xl border px-3 py-2 text-xs transition hover:bg-accent">
                          Abbrechen
                        </button>
                      </span>
                    ) : (
                      <button onClick={() => setConfirmDel(id)} disabled={!!deleting} className="inline-flex items-center gap-2 rounded-2xl border px-3 py-2 text-sm text-muted-foreground transition hover:border-destructive/20 hover:bg-destructive/10 hover:text-destructive disabled:opacity-50">
                        <Trash2 className="h-4 w-4" />
                        Loeschen
                      </button>
                    ))}
                  </div>
                </div>
              </div>
              {agentlinkProject === id && <AgentLinkPanel projectId={id} />}
              {webhookProject === id && <WebhooksPanel projectId={id} />}
            </div>
          ))}
        </section>
      )}
    </div>
  );
}
