import { useEffect, useState } from "react";
import { BookOpen, ChevronDown, ChevronRight, Plus, Trash2, Save, X, Pencil } from "lucide-react";
import { api, AgentSkill } from "@/lib/api";

const EMPTY_SKILL = {
  filename: "", skill: "", version: "1.0",
  scope: "on-demand" as "always" | "on-demand",
  triggers: [] as string[],
  priority: 50, content: "",
};

interface Props { agentId: string; }

export function SkillsPanel({ agentId }: Props) {
  const [skills,    setSkills]    = useState<AgentSkill[]>([]);
  const [loading,   setLoading]   = useState(true);
  const [error,     setError]     = useState("");
  const [expanded,  setExpanded]  = useState<string | null>(null);
  const [showForm,  setShowForm]  = useState(false);
  const [editFile,  setEditFile]  = useState<string | null>(null);
  const [form,      setForm]      = useState({ ...EMPTY_SKILL });
  const [triggerInput, setTriggerInput] = useState("");
  const [saving,    setSaving]    = useState(false);
  const [saveErr,   setSaveErr]   = useState("");
  const [deleting,  setDeleting]  = useState<string | null>(null);

  async function load() {
    try {
      const d = await api.agentSkills(agentId);
      setSkills(d.skills);
      setError("");
    } catch(e) { setError(e instanceof Error ? e.message : "Fehler"); }
    finally { setLoading(false); }
  }

  useEffect(() => { load(); }, [agentId]);

  function openNew() {
    setForm({ ...EMPTY_SKILL }); setEditFile(null);
    setTriggerInput(""); setSaveErr(""); setShowForm(true);
  }

  function openEdit(s: AgentSkill) {
    setForm({
      filename: s.filename, skill: s.skill, version: s.version,
      scope: s.scope as "always" | "on-demand",
      triggers: [...s.triggers], priority: s.priority, content: s.content,
    });
    setEditFile(s.filename); setTriggerInput(""); setSaveErr(""); setShowForm(true);
  }

  function closeForm() { setShowForm(false); setEditFile(null); setSaveErr(""); }

  function set(key: string, val: unknown) { setForm(f => ({ ...f, [key]: val })); }

  function addTrigger() {
    const t = triggerInput.trim().toLowerCase();
    if (t && !form.triggers.includes(t)) setForm(f => ({ ...f, triggers: [...f.triggers, t] }));
    setTriggerInput("");
  }

  function removeTrigger(t: string) {
    setForm(f => ({ ...f, triggers: f.triggers.filter(x => x !== t) }));
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault(); setSaving(true); setSaveErr("");
    try {
      const body = { ...form };
      if (editFile) {
        await api.updateSkill(agentId, editFile, body);
      } else {
        await api.createSkill(agentId, body);
      }
      closeForm(); await load();
    } catch(e) { setSaveErr(e instanceof Error ? e.message : "Fehler"); }
    finally { setSaving(false); }
  }

  async function handleDelete(filename: string) {
    if (!confirm(`Skill "${filename}" löschen?`)) return;
    setDeleting(filename);
    try { await api.deleteSkill(agentId, filename); await load(); }
    catch(e) { setError(e instanceof Error ? e.message : "Fehler"); }
    finally { setDeleting(null); }
  }

  return (
    <div className="border-t">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 bg-muted/20">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <BookOpen className="h-3.5 w-3.5" />
          <span>Skills ({skills.length})</span>
        </div>
        <button onClick={openNew}
          className="flex items-center gap-1 text-xs px-2 py-1 rounded border hover:bg-accent transition-colors">
          <Plus className="h-3 w-3" />Neuer Skill
        </button>
      </div>

      {error && <p className="px-4 py-2 text-xs text-destructive">{error}</p>}

      {/* Formular */}
      {showForm && (
        <div className="border-t bg-card px-4 py-4 space-y-4">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium">{editFile ? `Skill bearbeiten: ${editFile}` : "Neuer Skill"}</span>
            <button onClick={closeForm}><X className="h-4 w-4 text-muted-foreground" /></button>
          </div>
          <form onSubmit={handleSave} className="space-y-3">
            <div className="grid grid-cols-3 gap-3">
              <div className="space-y-1">
                <label className="text-xs text-muted-foreground">Dateiname *</label>
                <input value={form.filename} disabled={!!editFile}
                  onChange={e => set("filename", e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, ""))}
                  placeholder="z.B. steuern" required
                  className="w-full px-2.5 py-1.5 text-sm border rounded bg-background focus:outline-none focus:ring-1 focus:ring-primary disabled:opacity-50" />
              </div>
              <div className="space-y-1">
                <label className="text-xs text-muted-foreground">Skill-Name *</label>
                <input value={form.skill} onChange={e => set("skill", e.target.value)}
                  placeholder="z.B. Steuerberatung" required
                  className="w-full px-2.5 py-1.5 text-sm border rounded bg-background focus:outline-none focus:ring-1 focus:ring-primary" />
              </div>
              <div className="space-y-1">
                <label className="text-xs text-muted-foreground">Version</label>
                <input value={form.version} onChange={e => set("version", e.target.value)}
                  placeholder="1.0"
                  className="w-full px-2.5 py-1.5 text-sm border rounded bg-background focus:outline-none focus:ring-1 focus:ring-primary" />
              </div>
              <div className="space-y-1">
                <label className="text-xs text-muted-foreground">Scope</label>
                <select value={form.scope} onChange={e => set("scope", e.target.value)}
                  className="w-full px-2.5 py-1.5 text-sm border rounded bg-background focus:outline-none focus:ring-1 focus:ring-primary">
                  <option value="on-demand">on-demand</option>
                  <option value="always">always</option>
                </select>
              </div>
              <div className="space-y-1">
                <label className="text-xs text-muted-foreground">Priority (1-100)</label>
                <input type="number" value={form.priority} min={1} max={100}
                  onChange={e => set("priority", parseInt(e.target.value))}
                  className="w-full px-2.5 py-1.5 text-sm border rounded bg-background focus:outline-none focus:ring-1 focus:ring-primary" />
              </div>
            </div>

            {form.scope === "on-demand" && (
              <div className="space-y-1.5">
                <label className="text-xs text-muted-foreground">Trigger-Keywords</label>
                <div className="flex flex-wrap gap-1 mb-1">
                  {form.triggers.map(t => (
                    <span key={t} className="inline-flex items-center gap-1 px-2 py-0.5 text-xs bg-primary/10 text-primary rounded">
                      {t}<button type="button" onClick={() => removeTrigger(t)}><X className="h-2.5 w-2.5" /></button>
                    </span>
                  ))}
                </div>
                <div className="flex gap-2">
                  <input value={triggerInput} onChange={e => setTriggerInput(e.target.value)}
                    onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); addTrigger(); }}}
                    placeholder="Keyword eingeben, Enter zum Hinzufügen"
                    className="flex-1 px-2.5 py-1.5 text-sm border rounded bg-background focus:outline-none focus:ring-1 focus:ring-primary" />
                  <button type="button" onClick={addTrigger}
                    className="px-3 py-1.5 text-xs border rounded hover:bg-accent transition-colors">+</button>
                </div>
              </div>
            )}

            <div className="space-y-1">
              <label className="text-xs text-muted-foreground">Inhalt (Markdown)</label>
              <textarea value={form.content} onChange={e => set("content", e.target.value)}
                rows={6} placeholder="Beschreibe hier was der Agent in diesem Skill-Kontext wissen soll..."
                className="w-full px-2.5 py-1.5 text-sm border rounded bg-background focus:outline-none focus:ring-1 focus:ring-primary resize-none font-mono" />
            </div>

            {saveErr && <p className="text-xs text-destructive">{saveErr}</p>}
            <div className="flex justify-end gap-2">
              <button type="button" onClick={closeForm}
                className="px-3 py-1.5 text-sm border rounded hover:bg-accent transition-colors">Abbrechen</button>
              <button type="submit" disabled={saving}
                className="flex items-center gap-1.5 px-3 py-1.5 text-sm bg-primary text-primary-foreground rounded hover:bg-primary/90 disabled:opacity-50 transition-colors">
                <Save className="h-3.5 w-3.5" />{saving ? "Speichern…" : editFile ? "Aktualisieren" : "Skill anlegen"}
              </button>
            </div>
          </form>
        </div>
      )}

      {/* Skill-Liste */}
      {loading
        ? <div className="px-4 py-3 text-xs text-muted-foreground">Lade Skills…</div>
        : skills.length === 0 && !showForm
          ? <div className="px-4 py-3 text-xs text-muted-foreground">Keine Skills — leg den ersten an.</div>
          : skills.map(s => (
            <div key={s.filename} className="border-t">
              <div className="flex items-center gap-2 px-4 py-2 hover:bg-muted/20 cursor-pointer"
                onClick={() => setExpanded(e => e === s.filename ? null : s.filename)}>
                {expanded === s.filename ? <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" /> : <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />}
                <span className="text-sm font-medium flex-1">{s.skill}</span>
                <span className="text-xs text-muted-foreground">{s.filename}.md</span>
                <span className={`text-xs px-1.5 py-0.5 rounded ${s.scope === "always" ? "bg-primary/10 text-primary" : "bg-secondary text-secondary-foreground"}`}>
                  {s.scope}
                </span>
                {s.triggers.length > 0 && (
                  <span className="text-xs text-muted-foreground">{s.triggers.slice(0,3).join(", ")}{s.triggers.length > 3 ? "…" : ""}</span>
                )}
                <button onClick={e => { e.stopPropagation(); openEdit(s); }}
                  className="p-1 rounded hover:bg-accent text-muted-foreground hover:text-foreground transition-colors">
                  <Pencil className="h-3 w-3" />
                </button>
                <button onClick={e => { e.stopPropagation(); handleDelete(s.filename); }}
                  disabled={deleting === s.filename}
                  className="p-1 rounded hover:bg-destructive/10 text-muted-foreground hover:text-destructive disabled:opacity-50 transition-colors">
                  <Trash2 className="h-3 w-3" />
                </button>
              </div>
              {expanded === s.filename && s.content && (
                <pre className="mx-4 mb-3 p-3 text-xs bg-muted rounded font-mono whitespace-pre-wrap break-words text-muted-foreground">{s.content}</pre>
              )}
            </div>
          ))
      }
    </div>
  );
}
