/**
 * ProactivePage — Proaktive Tasks verwalten (#483)
 *
 * Erstellen, aktivieren/deaktivieren und monitoren von Background-Tasks
 * die Agenten autonom ausführen.
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Play, Pause, Plus, Trash2, Clock, Bot, RefreshCw, Loader2, CheckCircle, XCircle,
} from "lucide-react";
import { api } from "@/lib/api";

interface ProactiveTask {
  id: string;
  agent_id: string;
  project_id: string;
  prompt: string;
  interval: number;
  enabled: boolean;
  last_run: number;
  last_result: string;
  running: boolean;
}

export function ProactivePage() {
  const { t } = useTranslation();
  const [tasks, setTasks] = useState<ProactiveTask[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // New task form
  const [showForm, setShowForm] = useState(false);
  const [newAgent, setNewAgent] = useState("");
  const [newProject, setNewProject] = useState("");
  const [newPrompt, setNewPrompt] = useState("");
  const [newInterval, setNewInterval] = useState(3600);
  const [saving, setSaving] = useState(false);

  // Agents + Projects für Dropdowns
  const [agents, setAgents] = useState<{ id: string; name: string }[]>([]);
  const [projects, setProjects] = useState<{ id: string; name: string }[]>([]);

  async function load() {
    try {
      const d = await api.get<{ tasks: ProactiveTask[] }>("/admin/proactive/tasks");
      setTasks(d.tasks || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Fehler beim Laden");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // Agents und Projekte laden für Dropdowns
    api.get<any[]>("/agents").then(a => setAgents((a || []).map((x: any) => ({ id: x.id, name: x.config?.identity?.name || x.id })))).catch(() => {});
    api.get<any[]>("/projects").then(p => setProjects((p || []).map((x: any) => ({ id: x.id, name: x.config?.identity?.name || x.id })))).catch(() => {});
    const poll = setInterval(load, 10000);
    return () => clearInterval(poll);
  }, []);

  async function createTask() {
    if (!newAgent || !newProject || !newPrompt) return;
    setSaving(true);
    try {
      await api.post("/admin/proactive/tasks", {
        agent_id: newAgent,
        project_id: newProject,
        prompt: newPrompt,
        interval: newInterval,
        enabled: true,
      });
      setShowForm(false);
      setNewPrompt("");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Fehler");
    } finally {
      setSaving(false);
    }
  }

  async function deleteTask(id: string) {
    if (!confirm("Task wirklich löschen?")) return;
    try {
      await api.delete(`/admin/proactive/tasks/${id}`);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Fehler");
    }
  }

  function formatInterval(s: number): string {
    if (s < 60) return `${s}s`;
    if (s < 3600) return `${Math.round(s / 60)}min`;
    return `${Math.round(s / 3600)}h`;
  }

  function formatLastRun(ts: number): string {
    if (!ts) return "Noch nie";
    const ago = Math.round((Date.now() / 1000 - ts));
    if (ago < 60) return `vor ${ago}s`;
    if (ago < 3600) return `vor ${Math.round(ago / 60)}min`;
    return `vor ${Math.round(ago / 3600)}h`;
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="px-6 pt-6 pb-4 border-b border-border flex-shrink-0">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold tracking-tight mb-1">
              <Bot className="inline h-6 w-6 mr-2 text-primary" />
              Proactive Tasks
            </h1>
            <p className="text-xs text-muted-foreground">
              Agenten arbeiten autonom im Hintergrund — Monitoring, Code-Analyse, Reports
            </p>
          </div>
          <div className="flex gap-2">
            <button onClick={load}
              className="flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs hover:bg-muted">
              <RefreshCw className="w-3.5 h-3.5" /> Aktualisieren
            </button>
            <button onClick={() => setShowForm(!showForm)}
              className="flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs text-primary-foreground hover:bg-primary/90">
              <Plus className="w-3.5 h-3.5" /> Neuer Task
            </button>
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-6">
        {error && (
          <div className="mb-4 rounded-lg bg-red-50 dark:bg-red-950/50 p-3 text-xs text-red-600">
            {error}
          </div>
        )}

        {/* New Task Form */}
        {showForm && (
          <div className="mb-6 rounded-xl border bg-card p-4 space-y-3">
            <h3 className="text-sm font-semibold">Neuen proaktiven Task erstellen</h3>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <label className="text-xs font-medium">Agent</label>
                <select value={newAgent} onChange={e => setNewAgent(e.target.value)}
                  className="w-full rounded-lg border bg-background px-2 py-1.5 text-xs">
                  <option value="">— Agent wählen —</option>
                  {agents.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
                </select>
              </div>
              <div className="space-y-1">
                <label className="text-xs font-medium">Projekt</label>
                <select value={newProject} onChange={e => setNewProject(e.target.value)}
                  className="w-full rounded-lg border bg-background px-2 py-1.5 text-xs">
                  <option value="">— Projekt wählen —</option>
                  {projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
                </select>
              </div>
            </div>
            <div className="space-y-1">
              <label className="text-xs font-medium">Prompt (was soll der Agent tun?)</label>
              <textarea value={newPrompt} onChange={e => setNewPrompt(e.target.value)}
                placeholder="z.B. Prüfe ob alle Services laufen und erstelle einen Status-Report"
                rows={2}
                className="w-full rounded-lg border bg-background px-2 py-1.5 text-xs resize-none" />
            </div>
            <div className="space-y-1">
              <label className="text-xs font-medium">Intervall</label>
              <select value={newInterval} onChange={e => setNewInterval(Number(e.target.value))}
                className="w-full rounded-lg border bg-background px-2 py-1.5 text-xs">
                <option value={300}>Alle 5 Minuten</option>
                <option value={900}>Alle 15 Minuten</option>
                <option value={1800}>Alle 30 Minuten</option>
                <option value={3600}>Stündlich</option>
                <option value={21600}>Alle 6 Stunden</option>
                <option value={86400}>Täglich</option>
              </select>
            </div>
            <div className="flex gap-2">
              <button onClick={createTask} disabled={saving || !newAgent || !newProject || !newPrompt}
                className="flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2 text-xs text-primary-foreground hover:bg-primary/90 disabled:opacity-50">
                {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Plus className="w-3.5 h-3.5" />}
                Erstellen
              </button>
              <button onClick={() => setShowForm(false)}
                className="rounded-lg border px-4 py-2 text-xs hover:bg-muted">Abbrechen</button>
            </div>
          </div>
        )}

        {/* Task List */}
        {tasks.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
            <Bot className="h-10 w-10 mb-3" />
            <p className="text-sm">Keine proaktiven Tasks konfiguriert</p>
            <p className="text-xs mt-1">Erstelle einen Task um Agenten autonom arbeiten zu lassen</p>
          </div>
        ) : (
          <div className="space-y-3">
            {tasks.map(task => (
              <div key={task.id} className="rounded-xl border bg-card p-4">
                <div className="flex items-start justify-between">
                  <div className="flex-1">
                    <div className="flex items-center gap-2 mb-1">
                      {task.running ? (
                        <Loader2 className="w-4 h-4 text-primary animate-spin" />
                      ) : task.enabled ? (
                        <CheckCircle className="w-4 h-4 text-green-500" />
                      ) : (
                        <Pause className="w-4 h-4 text-muted-foreground" />
                      )}
                      <span className="text-sm font-medium">{task.prompt.slice(0, 80)}{task.prompt.length > 80 ? "…" : ""}</span>
                    </div>
                    <div className="flex items-center gap-4 text-xs text-muted-foreground mt-1">
                      <span className="flex items-center gap-1"><Bot className="w-3 h-3" /> {task.agent_id}</span>
                      <span className="flex items-center gap-1"><Clock className="w-3 h-3" /> {formatInterval(task.interval)}</span>
                      <span>Letzter Lauf: {formatLastRun(task.last_run)}</span>
                    </div>
                    {task.last_result && (
                      <div className="mt-2 rounded-lg bg-muted/50 p-2 text-xs text-muted-foreground max-h-20 overflow-y-auto">
                        {task.last_result}
                      </div>
                    )}
                  </div>
                  <button onClick={() => deleteTask(task.id)}
                    className="p-1.5 rounded-lg hover:bg-red-50 dark:hover:bg-red-950/50 text-muted-foreground hover:text-red-500">
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
