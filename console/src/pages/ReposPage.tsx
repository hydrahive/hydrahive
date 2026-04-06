import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { GitBranch, Plus, Trash2, TestTube, Check, X, Pencil } from "lucide-react";

interface Repo {
  id: string;
  name: string;
  url: string;
  token: string;
  token_preview: string;
  branch: string;
  provider: string;
  agents: string[];
  projects: string[];
}

interface AgentEntry {
  config: { id: string; identity: string };
}

const EMPTY: Omit<Repo, "token_preview"> = {
  id: "", name: "", url: "", token: "", branch: "main", provider: "github", agents: [], projects: [],
};

export function ReposPage() {
  const [repos, setRepos] = useState<Repo[]>([]);
  const [agents, setAgents] = useState<[string, string][]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [showForm, setShowForm] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);
  const [form, setForm] = useState({ ...EMPTY });
  const [saving, setSaving] = useState(false);
  const [saveErr, setSaveErr] = useState("");
  const [testResult, setTestResult] = useState<Record<string, { ok: boolean; info: string }>>({});
  const [testing, setTesting] = useState<string | null>(null);

  async function load() {
    try {
      const [reposData, agentsData] = await Promise.all([
        api.get<{ repos: Repo[] }>("/admin/repos"),
        api.agents() as Promise<Record<string, AgentEntry>>,
      ]);
      setRepos(reposData.repos);
      setAgents(Object.entries(agentsData).map(([id, a]) => [id, (a as AgentEntry).config?.identity || id]));
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Fehler");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  function openNew() {
    setForm({ ...EMPTY });
    setEditId(null);
    setSaveErr("");
    setShowForm(true);
  }

  function openEdit(repo: Repo) {
    setForm({ ...repo, token: "" }); // Token nicht vorladen (Sicherheit)
    setEditId(repo.id);
    setSaveErr("");
    setShowForm(true);
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setSaveErr("");
    try {
      if (editId) {
        await api.put(`/admin/repos/${editId}`, form);
      } else {
        await api.post("/admin/repos", form);
      }
      setShowForm(false);
      setEditId(null);
      await load();
    } catch (e) {
      setSaveErr(e instanceof Error ? e.message : "Fehler");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(id: string) {
    if (!confirm(`Repo "${id}" wirklich löschen?`)) return;
    await api.delete(`/admin/repos/${id}`);
    await load();
  }

  async function handleTest(id: string) {
    setTesting(id);
    try {
      const r = await api.post<{ ok: boolean; repo_name?: string; error?: string; default_branch?: string }>(
        `/admin/repos/${id}/test`, {}
      );
      setTestResult(prev => ({
        ...prev,
        [id]: {
          ok: r.ok,
          info: r.ok ? `${r.repo_name} (${r.default_branch})` : (r.error || "Fehler"),
        },
      }));
    } catch (e) {
      setTestResult(prev => ({ ...prev, [id]: { ok: false, info: e instanceof Error ? e.message : "Fehler" } }));
    } finally {
      setTesting(null);
    }
  }

  function set(key: string, val: unknown) {
    setForm(f => ({ ...f, [key]: val }));
  }

  function toggleAgent(agentId: string) {
    setForm(f => ({
      ...f,
      agents: f.agents.includes(agentId) ? f.agents.filter(a => a !== agentId) : [...f.agents, agentId],
    }));
  }

  if (loading) return <div className="p-6 text-muted-foreground">Lade...</div>;

  return (
    <div className="p-6 space-y-6 max-w-4xl">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold flex items-center gap-2">
            <GitBranch className="h-5 w-5" /> Git-Repos
          </h2>
          <p className="text-xs text-muted-foreground mt-1">
            Repos mit Credentials verwalten und Agenten zuweisen. Agenten nutzen automatisch den richtigen Token.
          </p>
        </div>
        <button onClick={openNew} className="flex items-center gap-1.5 px-3 py-2 text-sm bg-primary text-primary-foreground rounded-lg hover:bg-primary/90">
          <Plus className="h-4 w-4" /> Repo hinzufügen
        </button>
      </div>

      {error && <p className="text-sm text-destructive">{error}</p>}

      {/* Repo-Liste */}
      {repos.length === 0 && !showForm && (
        <div className="rounded-xl border border-dashed border-border p-8 text-center text-muted-foreground text-sm">
          Noch keine Repos angelegt. Klicke "Repo hinzufügen" um loszulegen.
        </div>
      )}

      <div className="space-y-3">
        {repos.map(repo => (
          <div key={repo.id} className="rounded-xl border bg-card p-4">
            <div className="flex items-center justify-between">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-semibold text-sm">{repo.name}</span>
                  <span className="text-xs px-1.5 py-0.5 rounded bg-muted text-muted-foreground">{repo.provider}</span>
                  <span className="text-xs text-muted-foreground font-mono">{repo.branch}</span>
                </div>
                <p className="text-xs text-muted-foreground mt-0.5 truncate">{repo.url}</p>
                <div className="flex items-center gap-2 mt-1.5">
                  <span className="text-xs text-muted-foreground">Token: {repo.token_preview}</span>
                  {repo.agents.length > 0 && (
                    <span className="text-xs text-muted-foreground">
                      Agenten: {repo.agents.join(", ")}
                    </span>
                  )}
                </div>
                {testResult[repo.id] && (
                  <div className={`flex items-center gap-1 mt-1.5 text-xs ${testResult[repo.id].ok ? "text-green-600" : "text-red-500"}`}>
                    {testResult[repo.id].ok ? <Check className="h-3 w-3" /> : <X className="h-3 w-3" />}
                    {testResult[repo.id].info}
                  </div>
                )}
              </div>
              <div className="flex items-center gap-1.5">
                <button onClick={() => handleTest(repo.id)} disabled={testing === repo.id}
                  className="p-2 rounded-lg hover:bg-muted transition-colors" title="Verbindung testen">
                  <TestTube className={`h-4 w-4 ${testing === repo.id ? "animate-pulse" : "text-muted-foreground"}`} />
                </button>
                <button onClick={() => openEdit(repo)}
                  className="p-2 rounded-lg hover:bg-muted transition-colors" title="Bearbeiten">
                  <Pencil className="h-4 w-4 text-muted-foreground" />
                </button>
                <button onClick={() => handleDelete(repo.id)}
                  className="p-2 rounded-lg hover:bg-muted transition-colors" title="Löschen">
                  <Trash2 className="h-4 w-4 text-destructive" />
                </button>
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Form Modal */}
      {showForm && (
        <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4" onClick={() => setShowForm(false)}>
          <form onSubmit={handleSave} onClick={e => e.stopPropagation()}
            className="bg-background rounded-2xl border shadow-xl p-6 w-full max-w-lg space-y-4 max-h-[85vh] overflow-y-auto">
            <h3 className="text-lg font-semibold">{editId ? "Repo bearbeiten" : "Neues Repo"}</h3>

            {!editId && (
              <div>
                <label className="text-xs font-medium text-muted-foreground">ID</label>
                <input value={form.id} onChange={e => set("id", e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, ""))}
                  placeholder="hydrahive-main" required
                  className="w-full mt-1 rounded-lg border bg-background px-3 py-2 text-sm" />
              </div>
            )}

            <div>
              <label className="text-xs font-medium text-muted-foreground">Name</label>
              <input value={form.name} onChange={e => set("name", e.target.value)} placeholder="HydraHive Main Repo" required
                className="w-full mt-1 rounded-lg border bg-background px-3 py-2 text-sm" />
            </div>

            <div>
              <label className="text-xs font-medium text-muted-foreground">URL</label>
              <input value={form.url} onChange={e => set("url", e.target.value)} placeholder="https://github.com/org/repo" required
                className="w-full mt-1 rounded-lg border bg-background px-3 py-2 text-sm" />
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs font-medium text-muted-foreground">Provider</label>
                <select value={form.provider} onChange={e => set("provider", e.target.value)}
                  className="w-full mt-1 rounded-lg border bg-background px-3 py-2 text-sm">
                  <option value="github">GitHub</option>
                  <option value="gitea">Gitea</option>
                  <option value="gitlab">GitLab</option>
                  <option value="other">Andere</option>
                </select>
              </div>
              <div>
                <label className="text-xs font-medium text-muted-foreground">Branch</label>
                <input value={form.branch} onChange={e => set("branch", e.target.value)} placeholder="main"
                  className="w-full mt-1 rounded-lg border bg-background px-3 py-2 text-sm" />
              </div>
            </div>

            <div>
              <label className="text-xs font-medium text-muted-foreground">
                Token {editId && <span className="text-muted-foreground/60">(leer = beibehalten)</span>}
              </label>
              <input value={form.token} onChange={e => set("token", e.target.value)} type="password"
                placeholder={editId ? "Leer lassen um Token beizubehalten" : "ghp_..."}
                className="w-full mt-1 rounded-lg border bg-background px-3 py-2 text-sm font-mono" />
            </div>

            {/* Agent-Zuweisung */}
            {agents.length > 0 && (
              <div>
                <label className="text-xs font-medium text-muted-foreground">Agenten zuweisen</label>
                <div className="mt-1 rounded-lg border bg-muted/30 p-2 space-y-1 max-h-40 overflow-y-auto">
                  {agents.map(([aid, label]) => (
                    <label key={aid} className="flex items-center gap-2 px-2 py-1 rounded text-sm cursor-pointer hover:bg-muted/60">
                      <input type="checkbox" checked={form.agents.includes(aid)} onChange={() => toggleAgent(aid)} className="rounded" />
                      <span className="text-foreground">{label}</span>
                      <span className="text-muted-foreground text-xs">({aid})</span>
                    </label>
                  ))}
                </div>
              </div>
            )}

            {saveErr && <p className="text-sm text-destructive">{saveErr}</p>}

            <div className="flex justify-end gap-2 pt-2">
              <button type="button" onClick={() => setShowForm(false)}
                className="px-4 py-2 text-sm rounded-lg border hover:bg-muted">Abbrechen</button>
              <button type="submit" disabled={saving}
                className="px-4 py-2 text-sm rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50">
                {saving ? "Speichere..." : (editId ? "Aktualisieren" : "Anlegen")}
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
