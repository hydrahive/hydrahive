import { useState, useEffect } from "react";
import { api } from "@/lib/api";
import { useTranslation } from "react-i18next";
import {
  Plus, Save, Trash2, Shield, ChevronDown, ChevronRight, Loader2, CheckCircle,
} from "lucide-react";
import { ConfirmDialog } from "@/components/ConfirmDialog";

interface GroupPermissions {
  pages: string[];
  tools: string[];
  plugins: string[];
  agents: string[];
}

interface Group {
  label: string;
  description: string;
  builtin: boolean;
  permissions: GroupPermissions;
}

const ALL_PAGES = [
  { id: "dashboard", label: "Dashboard" },
  { id: "my-agent", label: "Mein Agent" },
  { id: "projects", label: "Projekte" },
  { id: "blueprint", label: "Blueprint" },
  { id: "tools", label: "Tools" },
  { id: "tools/skill-packages", label: "Skill-Pakete" },
  { id: "code-editor", label: "Code Editor" },
  { id: "agents", label: "Agenten (Admin)" },
  { id: "activity", label: "Aktivität" },
  { id: "usage", label: "Usage" },
  { id: "schedules", label: "Schedules" },
  { id: "search", label: "Suche" },
  { id: "extensions", label: "Erweiterungen" },
  { id: "plugins", label: "Plugins" },
  { id: "hub", label: "HydraHub" },
  { id: "brain", label: "HydraBrain" },
  { id: "voice", label: "Voice" },
  { id: "federation", label: "Federation" },
  { id: "config-hub", label: "Setup" },
  { id: "system", label: "System" },
  { id: "audit", label: "Audit" },
  { id: "secrets", label: "Secrets" },
  { id: "settings", label: "Einstellungen" },
];

export function GroupsPage() {
  const { t } = useTranslation();
  const [groups, setGroups] = useState<Record<string, Group>>({});
  const [knownTools, setKnownTools] = useState<string[]>([]);
  const [agents, setAgents] = useState<{ id: string; identity: string }[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);
  const [editData, setEditData] = useState<Group | null>(null);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");
  const [newId, setNewId] = useState("");
  const [newLabel, setNewLabel] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [showNew, setShowNew] = useState(false);
  const [confirmState, setConfirmState] = useState<{action: () => void; title: string; message: string} | null>(null);

  async function load() {
    try {
      const [gRes, tRes, aRes] = await Promise.all([
        api.get<{ groups: Record<string, Group> }>("/admin/groups"),
        api.get<Record<string, unknown>>("/tools"),
        api.get<Record<string, unknown>>("/agents"),
      ]);
      setGroups(gRes.groups || {});
      // /tools returns {tool_id: tool_def, ...} — extract keys
      setKnownTools(Object.keys(tRes).filter(k => k !== "count"));
      // /agents returns {agent_id: agent_obj, ...} — extract id + identity
      setAgents(Object.entries(aRes)
        .filter(([k]) => k !== "count")
        .map(([id, a]: [string, any]) => ({ id, identity: a?.config?.identity || a?.identity || id })));
    } catch { /* ignore */ }
    finally { setLoading(false); }
  }

  useEffect(() => { load(); }, []);

  function selectGroup(id: string) {
    setSelected(id);
    setEditData(JSON.parse(JSON.stringify(groups[id])));
    setMsg("");
  }

  async function saveGroup() {
    if (!selected || !editData) return;
    setSaving(true); setMsg("");
    try {
      await api.put(`/admin/groups/${selected}`, editData);
      await load();
      setMsg(t("common.saved"));
      setTimeout(() => setMsg(""), 3000);
    } catch (e) { setMsg(e instanceof Error ? e.message : "Fehler"); }
    finally { setSaving(false); }
  }

  async function createGroup() {
    const id = newId.trim().toLowerCase().replace(/[^a-z0-9_-]/g, "");
    if (!id) return;
    setSaving(true); setMsg("");
    try {
      await api.post("/admin/groups", { id, label: newLabel.trim() || newId.trim(), description: newDesc.trim(), permissions: { pages: ["dashboard", "my-agent"], tools: [], plugins: [], agents: [] } });
      setNewId(""); setNewLabel(""); setNewDesc(""); setShowNew(false);
      await load();
      selectGroup(id);
    } catch (e) { setMsg(e instanceof Error ? e.message : "Fehler"); }
    finally { setSaving(false); }
  }

  function deleteGroup() {
    if (!selected) return;
    setConfirmState({
      title: t("confirm.titleDelete"),
      message: t("confirm.deleteGroup", { name: selected }),
      action: async () => {
        try {
          await api.delete(`/admin/groups/${selected}`);
          setSelected(null); setEditData(null);
          await load();
        } catch (e) { setMsg(e instanceof Error ? e.message : "Fehler"); }
      },
    });
  }

  function togglePerm(cat: keyof GroupPermissions, item: string) {
    if (!editData) return;
    const list = editData.permissions[cat];
    const isAll = list.includes("*");
    if (isAll) {
      // Switch from wildcard to explicit list minus this item
      const allItems = cat === "pages" ? ALL_PAGES.map(p => p.id)
        : cat === "tools" ? knownTools
        : cat === "agents" ? agents.map(a => a.id) : [];
      editData.permissions[cat] = allItems.filter(i => i !== item);
    } else if (list.includes(item)) {
      editData.permissions[cat] = list.filter(i => i !== item);
    } else {
      editData.permissions[cat] = [...list, item];
    }
    setEditData({ ...editData });
  }

  function selectAll(cat: keyof GroupPermissions) {
    if (!editData) return;
    editData.permissions[cat] = ["*"];
    setEditData({ ...editData });
  }

  function selectNone(cat: keyof GroupPermissions) {
    if (!editData) return;
    editData.permissions[cat] = [];
    setEditData({ ...editData });
  }

  function isChecked(cat: keyof GroupPermissions, item: string): boolean {
    if (!editData) return false;
    return editData.permissions[cat].includes("*") || editData.permissions[cat].includes(item);
  }

  if (loading) return <div className="p-6 text-sm text-muted-foreground">Lade...</div>;

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold flex items-center gap-2"><Shield className="h-5 w-5 text-primary" /> Gruppen & Berechtigungen</h1>
          <p className="text-xs text-muted-foreground">Gruppen definieren welche Seiten, Tools, Plugins und Agenten ein User nutzen darf.</p>
        </div>
        <button onClick={() => setShowNew(!showNew)} className="flex items-center gap-1 rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground hover:bg-primary/90">
          <Plus className="h-4 w-4" /> Neue Gruppe
        </button>
      </div>

      {showNew && (
        <div className="flex flex-wrap gap-2 items-center border rounded-lg p-3 bg-muted/30">
          <input value={newId} onChange={e => setNewId(e.target.value)} placeholder="gruppen-id"
            className="rounded-lg border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring font-mono w-40" />
          <input value={newLabel} onChange={e => setNewLabel(e.target.value)} placeholder="Anzeigename"
            className="rounded-lg border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring w-40" />
          <input value={newDesc} onChange={e => setNewDesc(e.target.value)} placeholder="Beschreibung"
            className="rounded-lg border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring flex-1 min-w-48" />
          <button onClick={createGroup} disabled={!newId.trim() || saving}
            className="rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground hover:bg-primary/90 disabled:opacity-40">
            Erstellen
          </button>
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-[280px_1fr]">
        {/* Group List */}
        <div className="space-y-1">
          {Object.entries(groups).map(([id, g]) => (
            <button key={id} onClick={() => selectGroup(id)}
              className={`w-full text-left rounded-lg px-3 py-2.5 text-sm transition-colors ${
                selected === id ? "bg-primary/10 border border-primary/30" : "hover:bg-muted/50 border border-transparent"
              }`}>
              <div className="flex items-center justify-between">
                <span className="font-medium">{g.label}</span>
                {g.builtin && <span className="text-xs text-muted-foreground">System</span>}
              </div>
              <p className="text-xs text-muted-foreground mt-0.5">{g.description}</p>
            </button>
          ))}
        </div>

        {/* Group Editor */}
        {editData && selected && (
          <div className="border rounded-lg p-5 space-y-5">
            <div className="flex items-center justify-between">
              <div className="space-y-1">
                <input value={editData.label} onChange={e => setEditData({ ...editData, label: e.target.value })}
                  className="text-lg font-semibold bg-transparent border-none focus:outline-none focus:ring-0 p-0" />
                <input value={editData.description} onChange={e => setEditData({ ...editData, description: e.target.value })}
                  placeholder="Beschreibung" className="text-xs text-muted-foreground bg-transparent border-none focus:outline-none p-0 w-full" />
              </div>
              {!editData.builtin && (
                <button onClick={deleteGroup} className="text-destructive hover:bg-destructive/10 rounded-lg p-2">
                  <Trash2 className="h-4 w-4" />
                </button>
              )}
            </div>

            {/* Pages */}
            <PermSection title="Sichtbare Seiten" items={ALL_PAGES.map(p => ({ id: p.id, label: p.label }))}
              cat="pages" isChecked={isChecked} toggle={togglePerm} selectAll={selectAll} selectNone={selectNone} />

            {/* Tools */}
            <PermSection title="Erlaubte Tools" items={knownTools.map(t => ({ id: t, label: t }))}
              cat="tools" isChecked={isChecked} toggle={togglePerm} selectAll={selectAll} selectNone={selectNone} />

            {/* Agents */}
            <PermSection title="Erlaubte Agenten" items={agents.map(a => ({ id: a.id, label: a.identity }))}
              cat="agents" isChecked={isChecked} toggle={togglePerm} selectAll={selectAll} selectNone={selectNone} />

            <div className="flex items-center gap-3 pt-2 border-t">
              <button onClick={saveGroup} disabled={saving}
                className="flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-40">
                {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
                Speichern
              </button>
              {msg && <span className={`text-sm ${msg === "Gespeichert" ? "text-green-500" : "text-destructive"}`}>{msg}</span>}
            </div>
          </div>
        )}

        {!selected && (
          <div className="border rounded-lg p-8 text-center text-muted-foreground text-sm">
            Wähle eine Gruppe aus der Liste um Berechtigungen zu bearbeiten.
          </div>
        )}
      </div>
    <ConfirmDialog
      open={!!confirmState}
      title={confirmState?.title || ""}
      message={confirmState?.message || ""}
      onConfirm={() => { confirmState?.action(); setConfirmState(null); }}
      onCancel={() => setConfirmState(null)}
      variant="danger"
    />
    </div>
  );
}

/* ── Permission Section ──────────────────────────────────────── */

function PermSection({
  title, items, cat, isChecked, toggle, selectAll, selectNone,
}: {
  title: string;
  items: { id: string; label: string }[];
  cat: keyof GroupPermissions;
  isChecked: (cat: keyof GroupPermissions, id: string) => boolean;
  toggle: (cat: keyof GroupPermissions, id: string) => void;
  selectAll: (cat: keyof GroupPermissions) => void;
  selectNone: (cat: keyof GroupPermissions) => void;
}) {
  const [open, setOpen] = useState(false);
  const Chevron = open ? ChevronDown : ChevronRight;
  const checkedCount = items.filter(i => isChecked(cat, i.id)).length;

  return (
    <div>
      <button onClick={() => setOpen(o => !o)} className="flex items-center gap-2 w-full text-left text-sm font-medium py-1">
        <Chevron className="h-4 w-4 text-muted-foreground" />
        {title}
        <span className="text-xs text-muted-foreground ml-auto">{checkedCount}/{items.length}</span>
      </button>
      {open && (
        <div className="ml-6 mt-2 space-y-2">
          <div className="flex gap-2 mb-2">
            <button onClick={() => selectAll(cat)} className="text-xs text-muted-foreground hover:text-foreground">Alle</button>
            <button onClick={() => selectNone(cat)} className="text-xs text-muted-foreground hover:text-foreground">Keine</button>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-1.5">
            {items.map(item => (
              <label key={item.id} className="flex items-center gap-2 text-xs cursor-pointer select-none">
                <input type="checkbox" checked={isChecked(cat, item.id)} onChange={() => toggle(cat, item.id)} className="rounded" />
                <span className="truncate">{item.label}</span>
              </label>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
