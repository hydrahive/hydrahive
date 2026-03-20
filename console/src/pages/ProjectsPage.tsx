import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { FolderKanban, Plus, RefreshCw, HardDrive, Hash, Users, Webhook, GitMerge } from "lucide-react";
import { api } from "@/lib/api";
import { WebhooksPanel } from "@/components/WebhooksPanel";
import { AgentLinkPanel } from "@/components/AgentLinkPanel";

interface ProjectEntry {
  name:        string;
  description: string;
  boss:        string;
  workers:     string[];
  matrix_room: string;
  filesystem:  string;
  system_user: string;
  show_swarm:  boolean;
}

interface CreateForm {
  id:          string;
  name:        string;
  description: string;
  boss:        string;
  workers:     string;
  samba:       boolean;
}

const EMPTY: CreateForm = { id:"", name:"", description:"", boss:"", workers:"", samba:true };

export function ProjectsPage() {
  const navigate = useNavigate();
  const [projects,  setProjects]  = useState<Record<string, ProjectEntry>>({});
  const [agents,    setAgents]    = useState<string[]>([]);
  const [loading,   setLoading]   = useState(true);
  const [error,     setError]     = useState("");
  const [showForm,  setShowForm]  = useState(false);
  const [form,      setForm]      = useState<CreateForm>(EMPTY);
  const [creating,  setCreating]  = useState(false);
  const [createErr, setCreateErr] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [webhookProject,   setWebhookProject]   = useState<string | null>(null);
  const [agentlinkProject, setAgentlinkProject] = useState<string | null>(null);

  async function load() {
    try {
      const [p, a] = await Promise.all([api.projects(), api.agents()]);
      setProjects(p as Record<string, ProjectEntry>);
      setAgents(Object.keys(a as Record<string, unknown>));
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Fehler beim Laden");
    } finally { setLoading(false); setRefreshing(false); }
  }

  useEffect(() => { load(); }, []);

  function refresh() { setRefreshing(true); load(); }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setCreating(true); setCreateErr("");
    try {
      await api.createProject({
        id:          form.id,
        name:        form.name,
        description: form.description,
        boss:        form.boss,
        workers:     form.workers.split(",").map(w=>w.trim()).filter(Boolean),
        samba:       form.samba,
      });
      setShowForm(false);
      setForm(EMPTY);
      await load();
    } catch (e) {
      setCreateErr(e instanceof Error ? e.message : "Fehler");
    } finally { setCreating(false); }
  }

  const projectList = Object.entries(projects);

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Projekte</h1>
          <p className="text-sm text-muted-foreground">
            {projectList.length} Projekt{projectList.length !== 1 ? "e" : ""} konfiguriert
          </p>
        </div>
        <div className="flex gap-2">
          <button onClick={refresh} disabled={refreshing}
            className="flex items-center gap-2 px-3 py-1.5 text-sm border rounded-md hover:bg-accent transition-colors disabled:opacity-50">
            <RefreshCw className={`h-3.5 w-3.5 ${refreshing?"animate-spin":""}`} />
            Aktualisieren
          </button>
          <button onClick={() => setShowForm(true)}
            className="flex items-center gap-2 px-3 py-1.5 text-sm bg-primary text-primary-foreground rounded-md hover:bg-primary/90 transition-colors">
            <Plus className="h-3.5 w-3.5" />
            Neues Projekt
          </button>
        </div>
      </div>

      {error && <div className="bg-destructive/10 border border-destructive/20 rounded-lg px-4 py-3 text-sm text-destructive">{error}</div>}

      {/* Create Dialog */}
      {showForm && (
        <div className="bg-card border rounded-lg p-5 space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="font-medium">Neues Projekt anlegen</h2>
            <button onClick={() => { setShowForm(false); setCreateErr(""); setForm(EMPTY); }}
              className="text-muted-foreground hover:text-foreground text-sm">Abbrechen</button>
          </div>
          <form onSubmit={handleCreate} className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Projekt-ID *</label>
              <input value={form.id} onChange={e=>setForm({...form, id:e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g,"")})}
                placeholder="z.B. buchhaltung" required
                className="w-full px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary" />
              <p className="text-xs text-muted-foreground">Nur a-z, 0-9, _ und -</p>
            </div>
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Name *</label>
              <input value={form.name} onChange={e=>setForm({...form, name:e.target.value})}
                placeholder="Anzeigename" required
                className="w-full px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary" />
            </div>
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Boss-Agent *</label>
              <select value={form.boss} onChange={e=>setForm({...form, boss:e.target.value})} required
                className="w-full px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary">
                <option value="">Agent wählen...</option>
                {agents.map(a => <option key={a} value={a}>{a}</option>)}
              </select>
            </div>
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Worker-Agenten</label>
              <input value={form.workers} onChange={e=>setForm({...form, workers:e.target.value})}
                placeholder="agent1, agent2 (kommagetrennt)"
                className="w-full px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary" />
            </div>
            <div className="space-y-1.5 col-span-2">
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Beschreibung</label>
              <input value={form.description} onChange={e=>setForm({...form, description:e.target.value})}
                placeholder="Optionale Beschreibung"
                className="w-full px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary" />
            </div>
            <div className="col-span-2 flex items-center gap-2">
              <input type="checkbox" id="samba" checked={form.samba} onChange={e=>setForm({...form, samba:e.target.checked})}
                className="h-4 w-4 rounded border" />
              <label htmlFor="samba" className="text-sm">Samba-Freigabe einrichten</label>
            </div>
            {createErr && <p className="col-span-2 text-sm text-destructive">{createErr}</p>}
            <div className="col-span-2 flex justify-end gap-2">
              <button type="button" onClick={() => { setShowForm(false); setForm(EMPTY); }}
                className="px-4 py-2 text-sm border rounded-md hover:bg-accent transition-colors">Abbrechen</button>
              <button type="submit" disabled={creating}
                className="px-4 py-2 text-sm bg-primary text-primary-foreground rounded-md hover:bg-primary/90 disabled:opacity-50 transition-colors">
                {creating ? "Wird angelegt..." : "Projekt anlegen"}
              </button>
            </div>
          </form>
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="space-y-3">
          {[1,2].map(i => <div key={i} className="bg-card border rounded-lg p-4 animate-pulse h-24" />)}
        </div>
      )}

      {/* Leer */}
      {!loading && projectList.length === 0 && !showForm && (
        <div className="bg-card border rounded-lg p-12 text-center space-y-3">
          <FolderKanban className="h-10 w-10 mx-auto text-muted-foreground" />
          <p className="text-sm text-muted-foreground">Noch keine Projekte. Lege ein erstes Projekt an.</p>
          <button onClick={() => setShowForm(true)}
            className="inline-flex items-center gap-2 px-4 py-2 text-sm bg-primary text-primary-foreground rounded-md hover:bg-primary/90 transition-colors">
            <Plus className="h-4 w-4" />Erstes Projekt anlegen
          </button>
        </div>
      )}

      {/* Liste */}
      {!loading && projectList.length > 0 && (
        <div className="space-y-3">
          {projectList.map(([id, proj]) => (
            <div key={id} className="bg-card border rounded-lg overflow-hidden">
              <div className="p-4 space-y-3">
              <div className="flex items-start justify-between gap-4">
                <div className="flex items-start gap-3">
                  <div className="w-9 h-9 rounded-md bg-primary/10 flex items-center justify-center flex-shrink-0 mt-0.5">
                    <FolderKanban className="h-5 w-5 text-primary" />
                  </div>
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-sm">{proj.name}</span>
                      <span className="text-xs text-muted-foreground">({id})</span>
                    </div>
                    {proj.description && <p className="text-xs text-muted-foreground">{proj.description}</p>}
                  </div>
                </div>
                <div className="flex gap-2 flex-shrink-0">
                  <button onClick={() => setAgentlinkProject(p => p === id ? null : id)}
                    title="AgentLink Handoffs"
                    className={`p-1.5 rounded-md transition-colors ${agentlinkProject === id ? "bg-primary/10 text-primary" : "border hover:bg-accent text-muted-foreground"}`}>
                    <GitMerge className="h-3.5 w-3.5" />
                  </button>
                  <button onClick={() => setWebhookProject(p => p === id ? null : id)}
                    title="Webhooks verwalten"
                    className={`p-1.5 rounded-md transition-colors ${webhookProject === id ? "bg-primary/10 text-primary" : "border hover:bg-accent text-muted-foreground"}`}>
                    <Webhook className="h-3.5 w-3.5" />
                  </button>
                  <button onClick={() => navigate(`/chat/${id}`)}
                    className="px-3 py-1 text-xs border rounded-md hover:bg-accent transition-colors">
                    Chat öffnen
                  </button>
                </div>
              </div>
              <div className="grid grid-cols-3 gap-3 pt-1 border-t">
                <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <Users className="h-3.5 w-3.5" />
                  <span>{proj.boss}{proj.workers.length > 0 ? ` + ${proj.workers.length} Worker` : ""}</span>
                </div>
                <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <HardDrive className="h-3.5 w-3.5" />
                  <span>{proj.system_user}</span>
                </div>
                <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <Hash className="h-3.5 w-3.5" />
                  <span className="truncate">{proj.matrix_room || "kein Room"}</span>
                </div>
              </div>
              </div>
              {agentlinkProject === id && <AgentLinkPanel projectId={id} />}
              {webhookProject === id && <WebhooksPanel projectId={id} />}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
