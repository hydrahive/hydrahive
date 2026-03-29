import { useEffect, useState } from "react";
import { Save, Loader2, ShieldCheck, FolderKanban, Bot } from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

interface UserEntry {
  username: string;
  role: string;
  group: string;
  allowed_projects: string[];
  allowed_agents: string[];
}
interface ProjectEntry { id: string; name: string }
interface AgentEntry   { id: string; identity: string }

export function PermissionsTab() {
  const [users,    setUsers]    = useState<UserEntry[]>([]);
  const [projects, setProjects] = useState<ProjectEntry[]>([]);
  const [agents,   setAgents]   = useState<AgentEntry[]>([]);
  const [loading,  setLoading]  = useState(true);
  const [saving,   setSaving]   = useState<Record<string, boolean>>({});
  const [toast,    setToast]    = useState<string | null>(null);
  const [local,    setLocal]    = useState<Record<string, { projects: string[]; agents: string[] }>>({});

  useEffect(() => {
    Promise.all([
      api.get<Record<string, any>>("/users"),
      api.get<Record<string, any>>("/projects"),
      api.get<Record<string, any>>("/agents"),
    ]).then(([ud, pd, ad]) => {
      const us = Object.entries(ud)
        .filter(([id]) => !id.startsWith("personal_"))
        .map(([, v]) => v as UserEntry);
      setUsers(us);
      setProjects(Object.entries(pd)
        .filter(([id]) => !id.startsWith("personal_"))
        .map(([id, v]: [string, any]) => ({ id, name: v.name || id })));
      setAgents(Object.entries(ad)
        .filter(([id]) => !id.startsWith("personal_"))
        .map(([id, v]: [string, any]) => ({ id, identity: v.config?.identity || id })));
      const init: typeof local = {};
      us.forEach(u => { init[u.username] = { projects: [...(u.allowed_projects || [])], agents: [...(u.allowed_agents || [])] }; });
      setLocal(init);
    }).finally(() => setLoading(false));
  }, []);

  function toggle(username: string, kind: "projects" | "agents", id: string) {
    setLocal(prev => {
      const cur = prev[username] ?? { projects: [], agents: [] };
      const list = cur[kind];
      return {
        ...prev,
        [username]: {
          ...cur,
          [kind]: list.includes(id) ? list.filter(x => x !== id) : [...list, id],
        },
      };
    });
  }

  async function saveUser(username: string) {
    setSaving(s => ({ ...s, [username]: true }));
    try {
      const d = local[username];
      await api.updateUser(username, { allowed_projects: d.projects, allowed_agents: d.agents });
      setToast(`${username} gespeichert`);
      setTimeout(() => setToast(null), 2500);
    } catch (e) {
      setToast(e instanceof Error ? e.message : "Fehler");
    } finally {
      setSaving(s => ({ ...s, [username]: false }));
    }
  }

  if (loading) return <div className="flex items-center justify-center h-full"><Loader2 className="h-8 w-8 animate-spin text-white/30" /></div>;

  return (
    <div className="flex flex-col h-full overflow-y-auto">
      <div className="flex items-center justify-between px-6 py-3 border-b border-white/10 shrink-0">
        <p className="text-xs text-white/40">Projekt- und Agenten-Zugriff pro User verwalten. Admins haben immer vollen Zugriff.</p>
        {toast && <span className="text-sm text-indigo-300">{toast}</span>}
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {users.filter(u => u.role !== "admin").map(user => {
          const userLocal = local[user.username] ?? { projects: [], agents: [] };
          const isSaving = saving[user.username];
          return (
            <div key={user.username} className="rounded-xl border border-white/10 bg-zinc-900/60 overflow-hidden">
              {/* Header */}
              <div className="flex items-center justify-between px-4 py-3 bg-zinc-900 border-b border-white/10">
                <div className="flex items-center gap-2">
                  <ShieldCheck className="h-4 w-4 text-indigo-400" />
                  <span className="font-medium text-white text-sm">{user.username}</span>
                  <span className="text-[0.65rem] px-1.5 py-0.5 rounded-full bg-white/10 text-white/50 uppercase tracking-wide">{user.group}</span>
                </div>
                <button onClick={() => saveUser(user.username)} disabled={isSaving}
                  className="flex items-center gap-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 px-3 py-1.5 text-xs text-white transition-colors">
                  {isSaving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />}
                  {isSaving ? "Speichere…" : "Speichern"}
                </button>
              </div>

              <div className="grid grid-cols-2 gap-0 divide-x divide-white/10">
                {/* Projects */}
                <div className="p-3">
                  <div className="flex items-center gap-1.5 mb-2">
                    <FolderKanban className="h-3.5 w-3.5 text-emerald-400" />
                    <span className="text-xs font-medium text-white/70">Projekte</span>
                  </div>
                  <div className="space-y-1">
                    {projects.map(p => (
                      <label key={p.id} className="flex items-center gap-2 cursor-pointer group">
                        <input type="checkbox"
                          checked={userLocal.projects.includes(p.id)}
                          onChange={() => toggle(user.username, "projects", p.id)}
                          className="rounded border-white/20 bg-zinc-800 accent-indigo-500" />
                        <span className={cn("text-xs", userLocal.projects.includes(p.id) ? "text-white" : "text-white/40 group-hover:text-white/60")}>{p.name}</span>
                      </label>
                    ))}
                    {projects.length === 0 && <p className="text-xs text-white/25">Keine Projekte</p>}
                  </div>
                </div>

                {/* Agents */}
                <div className="p-3">
                  <div className="flex items-center gap-1.5 mb-2">
                    <Bot className="h-3.5 w-3.5 text-blue-400" />
                    <span className="text-xs font-medium text-white/70">Agenten</span>
                  </div>
                  <div className="space-y-1">
                    {agents.map(a => (
                      <label key={a.id} className="flex items-center gap-2 cursor-pointer group">
                        <input type="checkbox"
                          checked={userLocal.agents.includes(a.id)}
                          onChange={() => toggle(user.username, "agents", a.id)}
                          className="rounded border-white/20 bg-zinc-800 accent-indigo-500" />
                        <span className={cn("text-xs", userLocal.agents.includes(a.id) ? "text-white" : "text-white/40 group-hover:text-white/60")}>{a.identity}</span>
                      </label>
                    ))}
                    {agents.length === 0 && <p className="text-xs text-white/25">Keine Agenten</p>}
                  </div>
                </div>
              </div>
            </div>
          );
        })}
        {users.filter(u => u.role !== "admin").length === 0 && (
          <p className="text-white/25 text-sm text-center mt-20">Keine normalen User vorhanden</p>
        )}
      </div>
    </div>
  );
}
