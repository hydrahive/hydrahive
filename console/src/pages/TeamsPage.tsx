import { useEffect, useState } from "react";
import { Users, Plus, Trash2, Loader2, Pencil, X } from "lucide-react";
import { api } from "@/lib/api";
import { useTranslation } from "react-i18next";
import { ConfirmDialog } from "@/components/ConfirmDialog";

interface TeamMember { agent_id: string; role: string }
interface Team {
  id?: string;
  name: string;
  members: TeamMember[];
  node_count?: number;
  installed_at?: string;
}

interface TeamDetail {
  name: string;
  members: TeamMember[];
}

export function TeamsPage() {
  const { t } = useTranslation();
  const [teams, setTeams] = useState<Team[]>([]);
  const [loading, setLoading] = useState(true);
  const [showNew, setShowNew] = useState(false);
  const [editing, setEditing] = useState<Team | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<{ teamId: string; action: () => void; title: string; message: string } | null>(null);
  const [saving, setSaving] = useState(false);

  // Form state
  const [formId, setFormId] = useState("");
  const [formName, setFormName] = useState("");
  const [formMembers, setFormMembers] = useState("");

  async function load() {
    setLoading(true);
    try {
      const data = await api.get<any[]>("/admin/teams");
      // GET /admin/teams returns list of team_id strings
      const teamDetails = await Promise.all(
        (data ?? []).map(async (id: string) => {
          try {
            const d = await api.get<TeamDetail>(`/admin/teams/${id}`);
            return { id, name: d.name, members: d.members };
          } catch { return { id, name: id, members: [] }; }
        })
      );
      setTeams(teamDetails);
    } catch {
      setTeams([]);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  function openNew() {
    setFormId(""); setFormName(""); setFormMembers("[]");
    setShowNew(true);
  }

  function openEdit(team: Team) {
    setFormId(team.id ?? "");
    setFormName(team.name);
    setFormMembers(JSON.stringify(team.members.map(m => ({ agent_id: m.agent_id, role: m.role })), null, 2));
    setEditing(team);
  }

  async function doSave() {
    if (!formId.trim() || !formName.trim()) return;
    setSaving(true);
    try {
      let members: TeamMember[] = [];
      try { members = JSON.parse(formMembers); } catch {}
      await api.put(`/admin/teams/${formId}`, { name: formName, members });
      setShowNew(false);
      setEditing(null);
      load();
    } catch (e: any) {
      alert("Speichern fehlgeschlagen: " + (e.message ?? e));
    } finally {
      setSaving(false);
    }
  }

  async function doDelete(teamId: string) {
    setDeleting(teamId);
    try {
      await api.delete(`/admin/teams/${teamId}`);
      setTeams(teams => teams.filter(t => (t.id ?? t.name) !== teamId));
    } finally {
      setDeleting(null);
      setConfirm(null);
    }
  }

  return (
    <div className="flex h-full flex-col">
      {/* Toolbar */}
      <div className="flex items-center gap-2 px-4 py-3 border-b border-white/10 shrink-0">
        <Users className="h-4 w-4 text-indigo-400" />
        <span className="text-sm font-medium text-white">Teams</span>
        <span className="text-xs text-white/30">{teams.length} insgesamt</span>
        <div className="flex-1" />
        <button
          onClick={openNew}
          className="flex items-center gap-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-700 px-3 py-1.5 text-xs text-white transition-colors">
          <Plus className="h-3.5 w-3.5" /> Neues Team
        </button>
        <button onClick={load}
          className="flex items-center gap-1.5 rounded-lg bg-zinc-800 border border-white/10 px-3 py-1.5 text-xs text-white hover:bg-zinc-700 transition-colors">
          Aktualisieren
        </button>
      </div>

      {/* Liste */}
      <div className="flex-1 overflow-y-auto p-4">
        {loading ? (
          <div className="flex items-center justify-center h-full">
            <Loader2 className="h-6 w-6 text-white/20 animate-spin" />
          </div>
        ) : teams.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-white/20">
            <Users className="h-8 w-8 mb-2" />
            <p className="text-sm">Keine Teams vorhanden</p>
            <p className="text-xs mt-1">Erstelle ein Team über "Neues Team"</p>
          </div>
        ) : (
          <div className="grid gap-3">
            {teams.map(team => (
              <div key={team.id ?? team.name}
                className="rounded-xl border border-white/10 bg-zinc-900/60 p-4 hover:border-indigo-500/30 transition-colors">
                <div className="flex items-start justify-between gap-3">
                  <div className="flex-1 min-w-0">
                    <h3 className="text-sm font-semibold text-white">{team.name}</h3>
                    <p className="text-xs text-white/40 mt-0.5">
                      {(team.members ?? []).length} Mitglied(er)
                    </p>
                    <div className="flex flex-wrap gap-1 mt-2">
                      {(team.members ?? []).map(m => (
                        <span key={m.agent_id}
                          className="rounded bg-zinc-800 px-2 py-0.5 text-[0.65rem] text-white/50">
                          {m.agent_id} ({m.role})
                        </span>
                      ))}
                    </div>
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    <button onClick={() => openEdit(team)}
                      className="p-1.5 rounded-lg text-white/40 hover:text-white hover:bg-white/5 transition-colors"
                      title="Bearbeiten">
                      <Pencil className="h-3.5 w-3.5" />
                    </button>
                    <button
                      onClick={() => setConfirm({
                        teamId: team.id ?? team.name,
                        action: () => doDelete(team.id ?? team.name),
                        title: "Team löschen",
                        message: ` '${team.name}' unwiderruflich löschen?`
                      })}
                      className="p-1.5 rounded-lg text-red-400/60 hover:text-red-400 hover:bg-red-500/10 transition-colors"
                      title="Löschen">
                      {deleting === (team.id ?? team.name) ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* New/Edit Dialog */}
      {(showNew || editing) && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
          <div className="w-full max-w-lg rounded-2xl border border-white/10 bg-zinc-900 p-6 shadow-2xl">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold text-white">{editing ? "Team bearbeiten" : "Neues Team"}</h2>
              <button onClick={() => { setShowNew(false); setEditing(null); }}
                className="p-1 rounded text-white/40 hover:text-white"><X className="h-4 w-4" /></button>
            </div>
            <div className="space-y-3">
              <div>
                <label className="block text-xs text-white/40 mb-1">Team-ID (Slug)</label>
                <input value={formId} onChange={e => setFormId(e.target.value)}
                  disabled={!!editing}
                  placeholder="z.B. dev-team"
                  className="w-full rounded-lg bg-zinc-800 border border-white/10 px-3 py-2 text-sm text-white disabled:opacity-40 focus:outline-none focus:border-indigo-500/60" />
              </div>
              <div>
                <label className="block text-xs text-white/40 mb-1">Name</label>
                <input value={formName} onChange={e => setFormName(e.target.value)}
                  placeholder="z.B. Development Team"
                  className="w-full rounded-lg bg-zinc-800 border border-white/10 px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500/60" />
              </div>
              <div>
                <label className="block text-xs text-white/40 mb-1">Mitglieder (JSON)</label>
                <textarea value={formMembers} onChange={e => setFormMembers(e.target.value)}
                  placeholder='[{"agent_id":"coder","role":"coder"},{"agent_id":"reviewer","role":"reviewer"}]'
                  rows={4}
                  className="w-full rounded-lg bg-zinc-800 border border-white/10 px-3 py-2 text-sm text-white font-mono focus:outline-none focus:border-indigo-500/60 resize-none" />
              </div>
            </div>
            <div className="flex justify-end gap-2 mt-5">
              <button onClick={() => { setShowNew(false); setEditing(null); }}
                className="rounded-lg border border-white/10 px-4 py-2 text-sm text-white/60 hover:text-white transition-colors">
                Abbrechen
              </button>
              <button onClick={doSave} disabled={saving || !formId.trim() || !formName.trim()}
                className="flex items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 text-sm text-white hover:bg-indigo-700 disabled:opacity-40 transition-colors">
                {saving && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                Speichern
              </button>
            </div>
          </div>
        </div>
      )}

      <ConfirmDialog
        open={!!confirm}
        title={confirm?.title ?? ""}
        message={confirm?.message ?? ""}
        onConfirm={() => confirm?.action()}
        onCancel={() => setConfirm(null)}
        variant="danger"
      />
    </div>
  );
}
