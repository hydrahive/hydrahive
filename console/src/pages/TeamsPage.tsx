import { useEffect, useState } from "react";
import { Users, Plus, Trash2, Loader2, Pencil, X, Info } from "lucide-react";
import { api } from "@/lib/api";
import { useTranslation } from "react-i18next";
import { ConfirmDialog } from "@/components/ConfirmDialog";

interface TeamMember { agent_id: string; role: string }
interface Team { id: string; name: string; members: TeamMember[] }

const ROLE_SUGGESTIONS = ["boss", "coder", "reviewer", "tester", "writer", "researcher", "analyst", "deployer", "architect", "specialist"];

export function TeamsPage() {
  const { t } = useTranslation();
  const [teams, setTeams] = useState<Team[]>([]);
  const [agentIds, setAgentIds] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [showNew, setShowNew] = useState(false);
  const [editing, setEditing] = useState<Team | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<{ teamId: string; name: string; action: () => void } | null>(null);
  const [saving, setSaving] = useState(false);

  const [formId, setFormId] = useState("");
  const [formName, setFormName] = useState("");
  const [formMembers, setFormMembers] = useState<TeamMember[]>([]);

  async function load() {
    setLoading(true);
    try {
      const ids = await api.get<string[]>("/admin/teams");
      const details = await Promise.all(
        (ids ?? []).map(async (id: string) => {
          try {
            const d = await api.get<{ name: string; members: TeamMember[] }>(`/admin/teams/${id}`);
            return { id, name: d.name, members: d.members ?? [] };
          } catch { return { id, name: id, members: [] }; }
        })
      );
      setTeams(details);
    } catch { setTeams([]); }
    finally { setLoading(false); }
  }

  useEffect(() => {
    load();
    api.get<Record<string, unknown>>("/agents")
      .then(d => setAgentIds(Object.keys(d ?? {})))
      .catch(() => {});
  }, []);

  function openNew() {
    setFormId(""); setFormName(""); setFormMembers([{ agent_id: "", role: "" }]);
    setShowNew(true);
  }

  function openEdit(team: Team) {
    setFormId(team.id); setFormName(team.name);
    setFormMembers(team.members.length ? team.members.map(m => ({ ...m })) : [{ agent_id: "", role: "" }]);
    setEditing(team);
  }

  function addMember() { setFormMembers(ms => [...ms, { agent_id: "", role: "" }]); }
  function removeMember(i: number) { setFormMembers(ms => ms.filter((_, j) => j !== i)); }
  function updateMember(i: number, field: keyof TeamMember, val: string) {
    setFormMembers(ms => ms.map((m, j) => j === i ? { ...m, [field]: val } : m));
  }

  async function doSave() {
    if (!formId.trim() || !formName.trim()) return;
    setSaving(true);
    try {
      const members = formMembers.filter(m => m.agent_id.trim());
      await api.put(`/admin/teams/${formId.trim()}`, { name: formName.trim(), members });
      setShowNew(false); setEditing(null);
      load();
    } catch (e: any) { alert("Speichern fehlgeschlagen: " + (e.message ?? e)); }
    finally { setSaving(false); }
  }

  async function doDelete(teamId: string) {
    setDeleting(teamId);
    try {
      await api.delete(`/admin/teams/${teamId}`);
      setTeams(ts => ts.filter(t => t.id !== teamId));
    } finally { setDeleting(null); setConfirm(null); }
  }

  const dialogOpen = showNew || !!editing;

  return (
    <div className="space-y-6 max-w-3xl">
      {/* Info block */}
      <div className="rounded-xl border bg-muted/30 p-4 space-y-2">
        <div className="flex items-center gap-2">
          <Info className="h-4 w-4 text-muted-foreground shrink-0" />
          <h3 className="text-sm font-semibold">{t("teams.infoTitle")}</h3>
        </div>
        <p className="text-xs text-muted-foreground leading-relaxed">{t("teams.infoText")}</p>
        <div className="rounded-lg bg-muted/50 border border-border/50 px-3 py-2 mt-1">
          <p className="text-xs text-muted-foreground font-mono">
            Beispiel: Team <span className="text-foreground">dev</span> →
            Agent <span className="text-foreground">boss</span> weiß:
            <span className="text-foreground"> coder</span> (Rolle: coder),
            <span className="text-foreground"> reviewer</span> (Rolle: reviewer)
          </p>
        </div>
      </div>

      {/* Toolbar */}
      <div className="flex items-center gap-2">
        <Users className="h-4 w-4 text-muted-foreground" />
        <span className="text-sm font-medium">{t("teams.title")}</span>
        <span className="text-xs text-muted-foreground">— {teams.length} insgesamt</span>
        <div className="flex-1" />
        <button onClick={load} className="rounded-lg border px-3 py-1.5 text-xs hover:bg-muted transition-colors">
          {t("teams.refresh")}
        </button>
        <button onClick={openNew}
          className="flex items-center gap-1.5 rounded-lg bg-primary text-primary-foreground px-3 py-1.5 text-xs hover:bg-primary/90 transition-colors">
          <Plus className="h-3.5 w-3.5" /> {t("teams.newTeam")}
        </button>
      </div>

      {/* List */}
      {loading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="h-6 w-6 text-muted-foreground animate-spin" />
        </div>
      ) : teams.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 text-muted-foreground gap-2">
          <Users className="h-8 w-8 opacity-30" />
          <p className="text-sm">{t("teams.noTeams")}</p>
          <p className="text-xs opacity-60">{t("teams.noTeamsHint")}</p>
        </div>
      ) : (
        <div className="grid gap-3">
          {teams.map(team => (
            <div key={team.id}
              className="rounded-xl border bg-card p-4 hover:border-primary/30 transition-colors">
              <div className="flex items-start justify-between gap-3">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <h3 className="text-sm font-semibold">{team.name}</h3>
                    <span className="text-xs text-muted-foreground font-mono bg-muted px-1.5 py-0.5 rounded">{team.id}</span>
                  </div>
                  <p className="text-xs text-muted-foreground mt-1">
                    {t("teams.membersLabel")}: {team.members.length}
                  </p>
                  {team.members.length > 0 && (
                    <div className="flex flex-wrap gap-1.5 mt-2">
                      {team.members.map((m, i) => (
                        <span key={i} className="rounded-full border border-border bg-muted px-2.5 py-0.5 text-[0.7rem] text-foreground">
                          {m.agent_id} <span className="text-muted-foreground">({m.role})</span>
                        </span>
                      ))}
                    </div>
                  )}
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <button onClick={() => openEdit(team)}
                    className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
                    title={t("teams.editTeam")}>
                    <Pencil className="h-3.5 w-3.5" />
                  </button>
                  <button
                    onClick={() => setConfirm({ teamId: team.id, name: team.name, action: () => doDelete(team.id) })}
                    className="p-1.5 rounded-lg text-destructive/60 hover:text-destructive hover:bg-destructive/10 transition-colors">
                    {deleting === team.id
                      ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      : <Trash2 className="h-3.5 w-3.5" />}
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* New / Edit Dialog */}
      {dialogOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <div className="w-full max-w-lg rounded-2xl border bg-card shadow-2xl p-6 space-y-4 max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between">
              <h2 className="text-base font-semibold">{editing ? t("teams.editTeam") : t("teams.newTeam")}</h2>
              <button onClick={() => { setShowNew(false); setEditing(null); }}
                className="p-1 rounded text-muted-foreground hover:text-foreground"><X className="h-4 w-4" /></button>
            </div>

            <div className="space-y-1">
              <label className="block text-xs text-muted-foreground">{t("teams.teamIdLabel")}</label>
              <input value={formId} onChange={e => setFormId(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "-"))}
                disabled={!!editing}
                placeholder={t("teams.teamIdPlaceholder")}
                className="w-full rounded-lg border bg-background px-3 py-2 text-sm disabled:opacity-40 focus:outline-none focus:ring-1 focus:ring-primary" />
              <p className="text-[0.65rem] text-muted-foreground">{t("teams.teamIdHint")}</p>
            </div>

            <div className="space-y-1">
              <label className="block text-xs text-muted-foreground">{t("teams.teamNameLabel")}</label>
              <input value={formName} onChange={e => setFormName(e.target.value)}
                placeholder={t("teams.teamNamePlaceholder")}
                className="w-full rounded-lg border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary" />
            </div>

            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <label className="block text-xs text-muted-foreground">{t("teams.membersLabel")}</label>
                <button onClick={addMember}
                  className="flex items-center gap-1 text-xs text-primary hover:underline">
                  <Plus className="h-3 w-3" /> {t("teams.addMember")}
                </button>
              </div>
              <p className="text-[0.65rem] text-muted-foreground">{t("teams.membersHint")}</p>
              <datalist id="role-suggestions">
                {ROLE_SUGGESTIONS.map(r => <option key={r} value={r} />)}
              </datalist>
              <div className="space-y-2">
                {formMembers.map((m, i) => (
                  <div key={i} className="flex items-center gap-2">
                    {agentIds.length > 0 ? (
                      <select value={m.agent_id}
                        onChange={e => updateMember(i, "agent_id", e.target.value)}
                        className="flex-1 rounded-lg border bg-background px-2.5 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary">
                        <option value="">— Agent wählen —</option>
                        {agentIds.map(id => <option key={id} value={id}>{id}</option>)}
                      </select>
                    ) : (
                      <input value={m.agent_id}
                        onChange={e => updateMember(i, "agent_id", e.target.value)}
                        placeholder={t("teams.agentIdPlaceholder")}
                        className="flex-1 rounded-lg border bg-background px-2.5 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary" />
                    )}
                    <input value={m.role}
                      onChange={e => updateMember(i, "role", e.target.value)}
                      list="role-suggestions"
                      placeholder={t("teams.rolePlaceholder")}
                      className="w-36 rounded-lg border bg-background px-2.5 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary" />
                    <button onClick={() => removeMember(i)}
                      className="p-1.5 rounded text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors">
                      <X className="h-3.5 w-3.5" />
                    </button>
                  </div>
                ))}
              </div>
            </div>

            <div className="flex justify-end gap-2 pt-2">
              <button onClick={() => { setShowNew(false); setEditing(null); }}
                className="rounded-lg border px-4 py-2 text-sm text-muted-foreground hover:text-foreground transition-colors">
                Abbrechen
              </button>
              <button onClick={doSave} disabled={saving || !formId.trim() || !formName.trim()}
                className="flex items-center gap-1.5 rounded-lg bg-primary text-primary-foreground px-4 py-2 text-sm hover:bg-primary/90 disabled:opacity-40 transition-colors">
                {saving && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                Speichern
              </button>
            </div>
          </div>
        </div>
      )}

      <ConfirmDialog
        open={!!confirm}
        title={t("teams.deleteTitle")}
        message={t("teams.deleteMessage", { name: confirm?.name ?? "" })}
        onConfirm={() => confirm?.action()}
        onCancel={() => setConfirm(null)}
        variant="danger"
      />
    </div>
  );
}
