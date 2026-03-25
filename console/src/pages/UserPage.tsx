import { useEffect, useState } from "react";
import { Users, Plus, RefreshCw, Trash2, KeyRound, ShieldCheck, User, Pencil, X, Save } from "lucide-react";
import { api } from "@/lib/api";
import { useTranslation } from "react-i18next";

interface OctoUser {
  username:          string;
  role:              string;
  matrix_id:         string;
  created_at:        string;
  allowed_projects:  string[];
  allowed_agents:    string[];
  datasources:       string[];
  wks_ip:            string;
  discord_user_id:   string;
}

interface EditForm {
  role:             string;
  allowed_projects: string[];
  allowed_agents:   string[];
  datasources:      string[];
  wks_ip:           string;
  discord_user_id:  string;
}

const EMPTY = { username: "", password: "", role: "user" };

export function UserPage() {
  const { t } = useTranslation();
  const [users,      setUsers]      = useState<Record<string, OctoUser>>({});
  const [loading,    setLoading]    = useState(true);
  const [error,      setError]      = useState("");
  const [showForm,   setShowForm]   = useState(false);
  const [form,       setForm]       = useState({ ...EMPTY });
  const [saving,     setSaving]     = useState(false);
  const [saveErr,    setSaveErr]    = useState("");
  const [deleting,   setDeleting]   = useState<string|null>(null);
  const [pwUser,     setPwUser]     = useState<string|null>(null);
  const [newPw,      setNewPw]      = useState("");
  const [pwSaving,   setPwSaving]   = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [editUser,   setEditUser]   = useState<string|null>(null);
  const [editForm,   setEditForm]   = useState<EditForm>({ role: "user", allowed_projects: [], allowed_agents: [], datasources: [], wks_ip: "", discord_user_id: "" });
  const [editSaving, setEditSaving] = useState(false);
  const [editErr,    setEditErr]    = useState("");
  const [dsInput,    setDsInput]    = useState("");
  const [allProjects, setAllProjects] = useState<{id:string;name:string}[]>([]);
  const [allAgents,   setAllAgents]   = useState<{id:string;identity:string}[]>([]);

  async function load() {
    try {
      setUsers(await api.get<Record<string,OctoUser>>("/users"));
      setError("");
    } catch(e) { setError(e instanceof Error ? e.message : "Fehler"); }
    finally { setLoading(false); setRefreshing(false); }
  }

  useEffect(() => { load(); }, []);
  function refresh() { setRefreshing(true); load(); }

  async function openEdit(u: OctoUser) {
    setEditErr("");
    setDsInput("");
    setEditForm({
      role: u.role,
      allowed_projects: u.allowed_projects ?? [],
      allowed_agents: u.allowed_agents ?? [],
      datasources: u.datasources ?? [],
      wks_ip: u.wks_ip ?? "",
      discord_user_id: u.discord_user_id ?? "",
    });
    // Load projects + agents for multi-select
    const [pData, aData] = await Promise.allSettled([
      api.get<Record<string,{name:string}>>("/projects"),
      api.get<Record<string,{config:{identity:string}}>>("/agents"),
    ]);
    if (pData.status === "fulfilled") {
      setAllProjects(Object.entries(pData.value).map(([id, v]) => ({ id, name: v.name })));
    }
    if (aData.status === "fulfilled") {
      setAllAgents(Object.entries(aData.value).filter(([id]) => !id.startsWith("personal_")).map(([id, v]) => ({ id, identity: v.config?.identity || id })));
    }
    setEditUser(u.username);
  }

  async function handleEdit(e: React.FormEvent) {
    e.preventDefault(); setEditSaving(true); setEditErr("");
    try {
      await api.updateUser(editUser!, editForm);
      setEditUser(null); await load();
    } catch(e) { setEditErr(e instanceof Error ? e.message : "Fehler"); }
    finally { setEditSaving(false); }
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault(); setSaving(true); setSaveErr("");
    try {
      await api.post("/users", form);
      setShowForm(false); setForm({ ...EMPTY }); await load();
    } catch(e) { setSaveErr(e instanceof Error ? e.message : "Fehler"); }
    finally { setSaving(false); }
  }

  async function handleDelete(username: string) {
    if (!confirm(t("users.deleteConfirm", { user: username }))) return;
    setDeleting(username);
    try { await api.delete(`/users/${username}`); await load(); }
    catch(e) { setError(e instanceof Error ? e.message : "Fehler"); }
    finally { setDeleting(null); }
  }

  async function handlePasswordChange(e: React.FormEvent) {
    e.preventDefault(); if (!pwUser) return;
    setPwSaving(true);
    try {
      await api.put(`/users/${pwUser}/password`, { password: newPw });
      setPwUser(null); setNewPw("");
    } catch(e) { alert(e instanceof Error ? e.message : "Fehler"); }
    finally { setPwSaving(false); }
  }

  const userList = Object.values(users);

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">{t("users.title")}</h1>
          <p className="text-sm text-muted-foreground">
            {t("users.subtitle", { count: userList.length })}
          </p>
        </div>
        <div className="flex gap-2">
          <button onClick={refresh} disabled={refreshing}
            className="flex items-center gap-2 px-3 py-1.5 text-sm border rounded-md hover:bg-accent transition-colors disabled:opacity-50">
            <RefreshCw className={`h-3.5 w-3.5 ${refreshing?"animate-spin":""}`}/>{t("users.refresh")}
          </button>
          <button onClick={() => setShowForm(true)}
            className="flex items-center gap-2 px-3 py-1.5 text-sm bg-primary text-primary-foreground rounded-md hover:bg-primary/90 transition-colors">
            <Plus className="h-3.5 w-3.5"/>{t("users.newUser")}
          </button>
        </div>
      </div>

      {error && <div className="bg-destructive/10 border border-destructive/20 rounded-lg px-4 py-3 text-sm text-destructive">{error}</div>}

      {showForm && (
        <div className="bg-card border rounded-lg p-5 space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="font-medium">{t("users.createTitle")}</h2>
            <button onClick={() => { setShowForm(false); setSaveErr(""); setForm({...EMPTY}); }}
              className="text-sm text-muted-foreground hover:text-foreground">{t("users.cancel")}</button>
          </div>
          <form onSubmit={handleCreate} className="grid grid-cols-3 gap-4">
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">{t("users.username")}</label>
              <input value={form.username} onChange={e=>setForm({...form,username:e.target.value.toLowerCase().replace(/[^a-z0-9_.-]/g,"")})}
                placeholder={t("users.usernamePlaceholder")} required
                className="w-full px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary" />
              <p className="text-xs text-muted-foreground">{t("users.usernameHint")}</p>
            </div>
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">{t("users.password")}</label>
              <input type="password" value={form.password} onChange={e=>setForm({...form,password:e.target.value})}
                placeholder={t("users.passwordPlaceholder")} required minLength={8}
                className="w-full px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary" />
            </div>
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">{t("users.role")}</label>
              <select value={form.role} onChange={e=>setForm({...form,role:e.target.value})}
                className="w-full px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary">
                <option value="user">user</option>
                <option value="admin">admin</option>
              </select>
            </div>
            {saveErr && <p className="col-span-3 text-sm text-destructive">{saveErr}</p>}
            <div className="col-span-3 flex justify-end gap-2">
              <button type="button" onClick={() => { setShowForm(false); setForm({...EMPTY}); }}
                className="px-4 py-2 text-sm border rounded-md hover:bg-accent transition-colors">{t("users.cancel")}</button>
              <button type="submit" disabled={saving}
                className="px-4 py-2 text-sm bg-primary text-primary-foreground rounded-md hover:bg-primary/90 disabled:opacity-50 transition-colors">
                {saving ? t("users.creating") : t("users.createBtn")}
              </button>
            </div>
          </form>
        </div>
      )}

      {pwUser && (
        <div className="bg-card border rounded-lg p-5 space-y-4">
          <h2 className="font-medium">{t("users.changePassword", { user: pwUser })}</h2>
          <form onSubmit={handlePasswordChange} className="flex gap-3">
            <input type="password" value={newPw} onChange={e=>setNewPw(e.target.value)}
              placeholder={t("users.newPassword")} required minLength={8}
              className="flex-1 px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary" />
            <button type="button" onClick={() => { setPwUser(null); setNewPw(""); }}
              className="px-4 py-2 text-sm border rounded-md hover:bg-accent">{t("users.cancel")}</button>
            <button type="submit" disabled={pwSaving}
              className="flex items-center gap-2 px-4 py-2 text-sm bg-primary text-primary-foreground rounded-md hover:bg-primary/90 disabled:opacity-50">
              <KeyRound className="h-3.5 w-3.5"/>{pwSaving ? t("users.saving") : t("users.save")}
            </button>
          </form>
        </div>
      )}

      {loading && <div className="space-y-3">{[1,2].map(i=><div key={i} className="bg-card border rounded-lg p-4 animate-pulse h-16"/>)}</div>}

      {!loading && userList.length === 0 && (
        <div className="bg-card border rounded-lg p-12 text-center space-y-3">
          <Users className="h-10 w-10 mx-auto text-muted-foreground"/>
          <p className="text-sm text-muted-foreground">{t("users.noUsers")}</p>
        </div>
      )}

      {!loading && userList.length > 0 && (
        <div className="space-y-2">
          {userList.map(u => (
            <div key={u.username} className="bg-card border rounded-lg p-4 flex items-center gap-4">
              <div className="w-9 h-9 rounded-full bg-primary/10 flex items-center justify-center flex-shrink-0">
                {u.role === "admin"
                  ? <ShieldCheck className="h-5 w-5 text-primary"/>
                  : <User className="h-5 w-5 text-primary"/>}
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-medium text-sm">{u.username}</span>
                  <span className={`text-xs px-1.5 py-0.5 rounded ${
                    u.role === "admin"
                      ? "bg-primary/10 text-primary"
                      : "bg-secondary text-secondary-foreground"
                  }`}>{u.role}</span>
                </div>
                <p className="text-xs text-muted-foreground font-mono">{u.matrix_id}</p>
              </div>
              {u.created_at && (
                <span className="text-xs text-muted-foreground flex-shrink-0">
                  {new Date(u.created_at).toLocaleDateString("de-DE")}
                </span>
              )}
              <div className="flex items-center gap-1 flex-shrink-0">
                <button onClick={() => openEdit(u)}
                  className="p-1.5 rounded hover:bg-accent transition-colors text-muted-foreground hover:text-foreground"
                  title="Benutzer bearbeiten">
                  <Pencil className="h-3.5 w-3.5"/>
                </button>
                <button onClick={() => { setPwUser(u.username); setNewPw(""); }}
                  className="p-1.5 rounded hover:bg-accent transition-colors text-muted-foreground hover:text-foreground"
                  title={t("users.changePasswordTitle")}>
                  <KeyRound className="h-3.5 w-3.5"/>
                </button>
                {u.username !== "admin" && (
                  <button onClick={() => handleDelete(u.username)} disabled={deleting === u.username}
                    className="p-1.5 rounded hover:bg-destructive/10 transition-colors text-muted-foreground hover:text-destructive disabled:opacity-50"
                    title={t("common.delete")}>
                    <Trash2 className="h-3.5 w-3.5"/>
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      <p className="text-xs text-muted-foreground">
        {t("users.footer")}
      </p>

      {/* Edit-Modal */}
      {editUser && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="w-full max-w-2xl rounded-2xl border bg-card shadow-xl overflow-y-auto max-h-[90vh]">
            <div className="flex items-center justify-between border-b px-6 py-4">
              <h2 className="font-semibold">Benutzer bearbeiten — {editUser}</h2>
              <button onClick={() => setEditUser(null)} className="rounded-xl p-2 hover:bg-accent">
                <X className="h-4 w-4" />
              </button>
            </div>
            <form onSubmit={handleEdit} className="space-y-5 p-6">

              {/* Rolle */}
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Rolle</label>
                <select value={editForm.role} onChange={e => setEditForm(f => ({ ...f, role: e.target.value }))}
                  className="w-full px-3 py-2 text-sm border rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-primary"
                  disabled={editUser === "admin"}>
                  <option value="user">user</option>
                  <option value="admin">admin</option>
                </select>
              </div>

              {/* WKS IP + Discord */}
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1.5">
                  <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Workstation IP</label>
                  <input value={editForm.wks_ip} onChange={e => setEditForm(f => ({ ...f, wks_ip: e.target.value }))}
                    placeholder="192.168.1.50"
                    className="w-full px-3 py-2 text-sm border rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-primary" />
                  <p className="text-xs text-muted-foreground">Für wks_shell_exec</p>
                </div>
                <div className="space-y-1.5">
                  <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Discord User ID</label>
                  <input value={editForm.discord_user_id} onChange={e => setEditForm(f => ({ ...f, discord_user_id: e.target.value }))}
                    placeholder="123456789012345678"
                    className="w-full px-3 py-2 text-sm border rounded-lg bg-background font-mono focus:outline-none focus:ring-2 focus:ring-primary" />
                  <p className="text-xs text-muted-foreground">Für Bot-Erkennung</p>
                </div>
              </div>

              {/* Erlaubte Projekte */}
              {allProjects.length > 0 && (
                <div className="space-y-2">
                  <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Erlaubte Projekte</label>
                  <p className="text-xs text-muted-foreground">Leer = Zugriff auf alle Projekte</p>
                  <div className="flex flex-wrap gap-2">
                    {allProjects.map(p => (
                      <button key={p.id} type="button"
                        onClick={() => setEditForm(f => ({
                          ...f,
                          allowed_projects: f.allowed_projects.includes(p.id)
                            ? f.allowed_projects.filter(x => x !== p.id)
                            : [...f.allowed_projects, p.id]
                        }))}
                        className={`rounded-full border px-3 py-1.5 text-xs transition ${editForm.allowed_projects.includes(p.id) ? "border-primary bg-primary text-primary-foreground" : "hover:bg-accent"}`}>
                        {p.name || p.id}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {/* Erlaubte Agenten */}
              {allAgents.length > 0 && (
                <div className="space-y-2">
                  <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Erlaubte Agenten</label>
                  <p className="text-xs text-muted-foreground">Leer = Zugriff auf alle Agenten</p>
                  <div className="flex flex-wrap gap-2">
                    {allAgents.map(a => (
                      <button key={a.id} type="button"
                        onClick={() => setEditForm(f => ({
                          ...f,
                          allowed_agents: f.allowed_agents.includes(a.id)
                            ? f.allowed_agents.filter(x => x !== a.id)
                            : [...f.allowed_agents, a.id]
                        }))}
                        className={`rounded-full border px-3 py-1.5 text-xs transition ${editForm.allowed_agents.includes(a.id) ? "border-primary bg-primary text-primary-foreground" : "hover:bg-accent"}`}>
                        {a.identity || a.id}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {/* Datenquellen */}
              <div className="space-y-2">
                <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Datenquellen</label>
                <p className="text-xs text-muted-foreground">z.B. nfs://server/pfad, smb://server/share</p>
                <div className="flex flex-wrap gap-1.5 min-h-7">
                  {editForm.datasources.map(ds => (
                    <span key={ds} className="flex items-center gap-1 rounded-full bg-muted px-2 py-1 text-xs font-mono">
                      {ds}
                      <button type="button" onClick={() => setEditForm(f => ({ ...f, datasources: f.datasources.filter(x => x !== ds) }))}
                        className="text-muted-foreground hover:text-destructive">
                        <X className="h-3 w-3" />
                      </button>
                    </span>
                  ))}
                </div>
                <div className="flex gap-2">
                  <input value={dsInput} onChange={e => setDsInput(e.target.value)}
                    onKeyDown={e => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        const v = dsInput.trim();
                        if (v && !editForm.datasources.includes(v)) setEditForm(f => ({ ...f, datasources: [...f.datasources, v] }));
                        setDsInput("");
                      }
                    }}
                    placeholder="nfs://192.168.1.5/data"
                    className="flex-1 px-3 py-2 text-sm border rounded-lg bg-background font-mono focus:outline-none focus:ring-2 focus:ring-primary" />
                  <button type="button" onClick={() => {
                    const v = dsInput.trim();
                    if (v && !editForm.datasources.includes(v)) setEditForm(f => ({ ...f, datasources: [...f.datasources, v] }));
                    setDsInput("");
                  }} disabled={!dsInput.trim()} className="rounded-lg border px-3 py-2 text-sm hover:bg-accent disabled:opacity-40">
                    <Plus className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>

              {editErr && <p className="text-sm text-destructive">{editErr}</p>}
              <div className="flex justify-end gap-2 pt-2">
                <button type="button" onClick={() => setEditUser(null)}
                  className="px-4 py-2 text-sm border rounded-lg hover:bg-accent transition-colors">Abbrechen</button>
                <button type="submit" disabled={editSaving}
                  className="flex items-center gap-2 px-4 py-2 text-sm bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 disabled:opacity-50 transition-colors">
                  <Save className="h-3.5 w-3.5" />{editSaving ? "Speichere..." : "Speichern"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
