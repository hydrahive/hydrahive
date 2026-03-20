import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { FolderOpen, RefreshCw, Plus, MessageSquare, Play, Users, User } from "lucide-react";
import { api } from "@/lib/api";

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

export function ProjectsPage() {
  const navigate = useNavigate();
  const [projects,    setProjects]    = useState<Record<string, ProjectEntry>>({});
  const [loading,     setLoading]     = useState(true);
  const [error,       setError]       = useState("");
  const [refreshing,  setRefreshing]  = useState(false);
  const [provisioning, setProvisioning] = useState<Record<string, boolean>>({});

  async function load() {
    try {
      const data = await api.projects() as Record<string, ProjectEntry>;
      setProjects(data);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Fehler beim Laden");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  useEffect(() => { load(); }, []);

  function refresh() { setRefreshing(true); load(); }

  async function provision(id: string) {
    setProvisioning(p => ({ ...p, [id]: true }));
    try {
      await api.post(`/projects/${id}/provision`, {});
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Provisionierung fehlgeschlagen");
    } finally {
      setProvisioning(p => ({ ...p, [id]: false }));
    }
  }

  const projectList = Object.entries(projects);

  return (
    <div className="p-6 space-y-6 overflow-y-auto flex-1">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Projekte</h1>
          <p className="text-sm text-muted-foreground">
            {projectList.length} Projekt{projectList.length !== 1 ? "e" : ""} konfiguriert
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={refresh}
            disabled={refreshing}
            className="flex items-center gap-2 px-3 py-1.5 text-sm border rounded-md hover:bg-accent transition-colors disabled:opacity-50"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? "animate-spin" : ""}`} />
            Aktualisieren
          </button>
          <button
            onClick={() => navigate("/projects/new")}
            className="flex items-center gap-2 px-3 py-1.5 text-sm bg-primary text-primary-foreground rounded-md hover:bg-primary/90 transition-colors"
          >
            <Plus className="h-3.5 w-3.5" />
            Neues Projekt
          </button>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="bg-destructive/10 border border-destructive/20 rounded-lg px-4 py-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="space-y-3">
          {[1, 2, 3].map(i => (
            <div key={i} className="bg-card border rounded-lg p-4 animate-pulse">
              <div className="h-4 bg-muted rounded w-1/4 mb-2" />
              <div className="h-3 bg-muted rounded w-1/2 mb-3" />
              <div className="h-3 bg-muted rounded w-1/3" />
            </div>
          ))}
        </div>
      )}

      {/* Empty */}
      {!loading && projectList.length === 0 && (
        <div className="bg-card border rounded-lg p-12 text-center space-y-3">
          <FolderOpen className="h-10 w-10 mx-auto text-muted-foreground" />
          <p className="text-sm text-muted-foreground">
            Keine Projekte gefunden. Lege Projekte unter <code className="text-xs">/projects/</code> an.
          </p>
        </div>
      )}

      {/* Liste */}
      {!loading && projectList.length > 0 && (
        <div className="space-y-3">
          {projectList.map(([id, project]) => {
            const provisioned = !!project.matrix_room;
            const isProvisioning = provisioning[id] ?? false;

            return (
              <div key={id} className="bg-card border rounded-lg p-4 space-y-3">
                {/* Zeile 1: Icon + Name + Status */}
                <div className="flex items-start gap-4">
                  <div className="w-9 h-9 rounded-md bg-primary/10 flex items-center justify-center flex-shrink-0">
                    <FolderOpen className="h-5 w-5 text-primary" />
                  </div>

                  <div className="flex-1 min-w-0 space-y-0.5">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-medium text-sm">{project.name}</span>
                      <span className="text-xs text-muted-foreground">({id})</span>
                      <span className={`text-xs px-1.5 py-0.5 rounded ${
                        provisioned
                          ? "bg-green-500/10 text-green-600"
                          : "bg-muted text-muted-foreground"
                      }`}>
                        {provisioned ? "bereit" : "nicht provisioniert"}
                      </span>
                    </div>
                    {project.description && (
                      <p className="text-xs text-muted-foreground">{project.description}</p>
                    )}
                  </div>

                  {/* Aktionen */}
                  <div className="flex items-center gap-2 flex-shrink-0">
                    {!provisioned && (
                      <button
                        onClick={() => provision(id)}
                        disabled={isProvisioning}
                        className="flex items-center gap-1.5 px-2.5 py-1 text-xs border rounded-md hover:bg-accent transition-colors disabled:opacity-50"
                      >
                        <Play className={`h-3 w-3 ${isProvisioning ? "animate-pulse" : ""}`} />
                        {isProvisioning ? "Läuft..." : "Provisionieren"}
                      </button>
                    )}
                    {provisioned && (
                      <button
                        onClick={() => navigate(`/chat/${id}`)}
                        className="flex items-center gap-1.5 px-2.5 py-1 text-xs bg-primary text-primary-foreground rounded-md hover:bg-primary/90 transition-colors"
                      >
                        <MessageSquare className="h-3 w-3" />
                        Chat
                      </button>
                    )}
                  </div>
                </div>

                {/* Zeile 2: Meta-Chips */}
                <div className="flex items-center gap-3 flex-wrap text-xs text-muted-foreground pl-13">
                  <span className="flex items-center gap-1">
                    <User className="h-3 w-3" />
                    Boss: <span className="font-mono ml-0.5">{project.boss}</span>
                  </span>
                  {project.workers.length > 0 && (
                    <span className="flex items-center gap-1">
                      <Users className="h-3 w-3" />
                      {project.workers.length} Worker{project.workers.length !== 1 ? "" : ""}
                    </span>
                  )}
                  {project.system_user && (
                    <span className="font-mono">{project.system_user}</span>
                  )}
                  {project.matrix_room && (
                    <span className="font-mono truncate max-w-[200px]" title={project.matrix_room}>
                      {project.matrix_room}
                    </span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
