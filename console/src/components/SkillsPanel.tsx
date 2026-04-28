import { useEffect, useState } from "react";
import { BookOpen, ChevronDown, ChevronRight, Plus, Trash2, Save, X, Pencil, Radar, Bot, Download } from "lucide-react";
import { api, AgentSkill } from "@/lib/api";
import { useTranslation } from "react-i18next";
import { ConfirmDialog } from "@/components/ConfirmDialog";

const EMPTY_SKILL = {
  filename: "",
  skill: "",
  version: "1.0",
  scope: "on-demand" as "always" | "on-demand",
  triggers: [] as string[],
  priority: 50,
  content: "",
};

interface CatalogEntry { name: string; skill: string; scope: string; triggers: string[]; }
interface Props { agentId: string; }

export function SkillsPanel({ agentId }: Props) {
  const { t } = useTranslation();
  const [skills, setSkills] = useState<AgentSkill[]>([]);
  const [catalog, setCatalog] = useState<CatalogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [installing, setInstalling] = useState<string | null>(null);
  const [installErr, setInstallErr] = useState("");
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [editFile, setEditFile] = useState<string | null>(null);
  const [form, setForm] = useState({ ...EMPTY_SKILL });
  const [triggerInput, setTriggerInput] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveErr, setSaveErr] = useState("");
  const [deleting, setDeleting] = useState<string | null>(null);
  const [confirmState, setConfirmState] = useState<{action: () => void; title: string; message: string} | null>(null);

  async function load() {
    try {
      const d = await api.agentSkillsCatalog(agentId);
      setSkills(d.skills || []);
      setCatalog((d.available || []).filter((c) => c.name));
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : t("common.error"));
    } finally {
      setLoading(false);
    }
  }

  async function handleInstall(name: string) {
    setInstalling(name);
    setInstallErr("");
    try {
      await api.installSkillFromCatalog(agentId, name);
      await load();
    } catch (e) {
      setInstallErr(e instanceof Error ? e.message : t("common.error"));
    } finally {
      setInstalling(null);
    }
  }

  useEffect(() => { load(); }, [agentId]);

  function openNew() {
    setForm({ ...EMPTY_SKILL });
    setEditFile(null);
    setTriggerInput("");
    setSaveErr("");
    setShowForm(true);
  }

  function openEdit(s: AgentSkill) {
    setForm({
      filename: s.filename,
      skill: s.skill,
      version: s.version,
      scope: s.scope as "always" | "on-demand",
      triggers: [...s.triggers],
      priority: s.priority,
      content: s.content,
    });
    setEditFile(s.filename);
    setTriggerInput("");
    setSaveErr("");
    setShowForm(true);
  }

  function closeForm() {
    setShowForm(false);
    setEditFile(null);
    setSaveErr("");
  }

  function set(key: string, val: unknown) {
    setForm((f) => ({ ...f, [key]: val }));
  }

  function addTrigger() {
    const trig = triggerInput.trim().toLowerCase();
    if (trig && !form.triggers.includes(trig)) setForm((f) => ({ ...f, triggers: [...f.triggers, trig] }));
    setTriggerInput("");
  }

  function removeTrigger(trig: string) {
    setForm((f) => ({ ...f, triggers: f.triggers.filter((x) => x !== trig) }));
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setSaveErr("");
    try {
      const body = { ...form };
      if (editFile) {
        await api.updateSkill(agentId, editFile, body);
      } else {
        await api.createSkill(agentId, body);
      }
      closeForm();
      await load();
    } catch (e) {
      setSaveErr(e instanceof Error ? e.message : t("common.error"));
    } finally {
      setSaving(false);
    }
  }

  function handleDelete(filename: string) {
    setConfirmState({
      title: t("confirm.titleDelete"),
      message: t("skills.deleteConfirm", { file: filename }),
      action: async () => {
        setDeleting(filename);
        try {
          await api.deleteSkill(agentId, filename);
          await load();
        } catch (e) {
          setError(e instanceof Error ? e.message : t("common.error"));
        } finally {
          setDeleting(null);
        }
      },
    });
  }

  return (
    <>
    <div className="border-t bg-muted/10 px-5 py-5">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <BookOpen className="h-4 w-4 text-primary" />
            <h3 className="text-base font-semibold tracking-tight">{t("skills.title")}</h3>
            <span className="status-pill">{skills.length}</span>
          </div>
          <p className="mt-2 text-sm text-muted-foreground">{t("skills.subtitle")}</p>
        </div>
        <button onClick={openNew} className="inline-flex items-center gap-2 rounded-2xl border px-3 py-2 text-sm transition hover:bg-accent">
          <Plus className="h-4 w-4" />
          {t("skills.newSkill")}
        </button>
      </div>

      {error && <p className="mt-4 text-sm text-destructive">{error}</p>}

      {showForm && (
        <div className="app-panel mt-5 p-5">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="metric-kicker">{t("skills.title")}</p>
              <h4 className="mt-2 text-lg font-semibold tracking-tight">
                {editFile ? t("skills.editSkill", { file: editFile }) : t("skills.newSkillTitle")}
              </h4>
            </div>
            <button onClick={closeForm} className="rounded-xl p-2 text-muted-foreground transition hover:bg-accent hover:text-foreground"><X className="h-4 w-4" /></button>
          </div>
          <form onSubmit={handleSave} className="mt-5 space-y-4">
            <div className="grid gap-4 md:grid-cols-3">
              <div className="space-y-1.5">
                <label className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">{t("skills.filename")}</label>
                <input value={form.filename} disabled={!!editFile} onChange={(e) => set("filename", e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, ""))} placeholder={t("skills.filenamePlaceholder")} required className="w-full rounded-2xl border bg-background px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary disabled:opacity-50" />
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">{t("skills.skillName")}</label>
                <input value={form.skill} onChange={(e) => set("skill", e.target.value)} placeholder={t("skills.skillNamePlaceholder")} required className="w-full rounded-2xl border bg-background px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary" />
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">{t("skills.version")}</label>
                <input value={form.version} onChange={(e) => set("version", e.target.value)} placeholder="1.0" className="w-full rounded-2xl border bg-background px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary" />
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">{t("skills.scope")}</label>
                <select value={form.scope} onChange={(e) => set("scope", e.target.value)} className="w-full rounded-2xl border bg-background px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary">
                  <option value="on-demand">on-demand</option>
                  <option value="always">always</option>
                </select>
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">{t("skills.priority")}</label>
                <input type="number" value={form.priority} min={1} max={100} onChange={(e) => set("priority", parseInt(e.target.value))} className="w-full rounded-2xl border bg-background px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary" />
              </div>
            </div>

            {form.scope === "on-demand" && (
              <div className="space-y-2">
                <label className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">{t("skills.triggerKeywords")}</label>
                <div className="flex flex-wrap gap-2">
                  {form.triggers.map((trig) => (
                    <span key={trig} className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-2 py-1 text-xs text-primary">
                      {trig}
                      <button type="button" onClick={() => removeTrigger(trig)}><X className="h-3 w-3" /></button>
                    </span>
                  ))}
                </div>
                <div className="flex gap-2">
                  <input value={triggerInput} onChange={(e) => setTriggerInput(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addTrigger(); } }} placeholder={t("skills.triggerPlaceholder")} className="flex-1 rounded-2xl border bg-background px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary" />
                  <button type="button" onClick={addTrigger} className="rounded-2xl border px-3 py-2 text-sm transition hover:bg-accent">+</button>
                </div>
              </div>
            )}

            <div className="space-y-1.5">
              <label className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">{t("skills.content")}</label>
              <textarea value={form.content} onChange={(e) => set("content", e.target.value)} rows={6} placeholder={t("skills.contentPlaceholder")} className="w-full resize-none rounded-2xl border bg-background px-3 py-2.5 font-mono text-sm focus:outline-none focus:ring-2 focus:ring-primary" />
            </div>

            {saveErr && <p className="text-sm text-destructive">{saveErr}</p>}
            <div className="flex justify-end gap-2">
              <button type="button" onClick={closeForm} className="rounded-2xl border px-4 py-2 text-sm transition hover:bg-accent">{t("skills.cancel")}</button>
              <button type="submit" disabled={saving} className="inline-flex items-center gap-2 rounded-2xl bg-primary px-4 py-2 text-sm text-primary-foreground transition hover:bg-primary/90 disabled:opacity-50">
                <Save className="h-4 w-4" />
                {saving ? t("skills.saving") : editFile ? t("skills.update") : t("skills.create")}
              </button>
            </div>
          </form>
        </div>
      )}

      {catalog.length > 0 && (
        <div className="mt-6">
          <p className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground mb-3">Catalog — verfügbar</p>
          {installErr && <p className="mb-2 text-xs text-destructive">{installErr}</p>}
          <div className="space-y-2">
            {catalog.map((c) => (
              <div key={c.name} className="flex items-center justify-between gap-3 rounded-2xl border bg-muted/10 px-4 py-2.5">
                <div className="min-w-0">
                  <span className="text-sm font-medium">{c.skill || c.name}</span>
                  {(c.triggers?.length ?? 0) > 0 && <p className="text-xs text-muted-foreground mt-0.5">{c.triggers.slice(0, 4).join(", ")}</p>}
                </div>
                <button
                  onClick={() => handleInstall(c.name)}
                  disabled={installing === c.name}
                  className="inline-flex items-center gap-1.5 rounded-xl border px-3 py-1.5 text-xs transition hover:bg-accent disabled:opacity-50"
                >
                  <Download className="h-3 w-3" />
                  {installing === c.name ? "..." : "Installieren"}
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="mt-5 space-y-3">
        {loading ? (
          <div className="space-y-3">{[1, 2].map((i) => <div key={i} className="metric-card h-24 animate-pulse" />)}</div>
        ) : skills.length === 0 && !showForm ? (
          <div className="section-card py-10 text-center text-sm text-muted-foreground">
            <Radar className="mx-auto h-8 w-8 text-muted-foreground" />
            <p className="mt-3">{t("skills.noSkills")}</p>
          </div>
        ) : (
          skills.map((s) => (
            <div key={s.filename} className="app-panel overflow-hidden">
              <div className="flex cursor-pointer items-center gap-3 px-4 py-3 transition hover:bg-muted/10" onClick={() => setExpanded((e) => e === s.filename ? null : s.filename)}>
                {expanded === s.filename ? <ChevronDown className="h-4 w-4 text-muted-foreground" /> : <ChevronRight className="h-4 w-4 text-muted-foreground" />}
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-semibold">{s.skill}</span>
                    <span className={`rounded-full px-2 py-1 text-xs ${s.scope === "always" ? "bg-primary/10 text-primary" : "bg-secondary text-secondary-foreground"}`}>{s.scope}</span>
                    <span className="rounded-full bg-secondary px-2 py-1 text-xs text-secondary-foreground">{s.filename}.md</span>
                    {s.author === "agent" && (
                      <span className="inline-flex items-center gap-1 rounded-full bg-accent/30 px-2 py-1 text-xs text-accent-foreground" title={t("skills.createdByAgent")}>
                        <Bot className="h-3 w-3" />
                        Agent
                      </span>
                    )}
                  </div>
                  {s.triggers.length > 0 && <p className="mt-1 text-xs text-muted-foreground">{s.triggers.slice(0, 3).join(", ")}{s.triggers.length > 3 ? "..." : ""}</p>}
                </div>
                <button onClick={(e) => { e.stopPropagation(); openEdit(s); }} className="rounded-xl p-2 text-muted-foreground transition hover:bg-accent hover:text-foreground"><Pencil className="h-4 w-4" /></button>
                <button onClick={(e) => { e.stopPropagation(); handleDelete(s.filename); }} disabled={deleting === s.filename} className="rounded-xl p-2 text-muted-foreground transition hover:bg-destructive/10 hover:text-destructive disabled:opacity-50"><Trash2 className="h-4 w-4" /></button>
              </div>
              {expanded === s.filename && s.content && <pre className="border-t bg-muted/10 mx-4 mb-4 rounded-2xl p-4 text-xs text-muted-foreground whitespace-pre-wrap break-words">{s.content}</pre>}
            </div>
          ))
        )}
      </div>
    </div>
    <ConfirmDialog
      open={!!confirmState}
      title={confirmState?.title || ""}
      message={confirmState?.message || ""}
      onConfirm={() => { confirmState?.action(); setConfirmState(null); }}
      onCancel={() => setConfirmState(null)}
      variant="danger"
    />
    </>
  );
}
