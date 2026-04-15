import { useEffect, useMemo, useState } from "react";
import { Sparkles, Save, RefreshCw, CheckCircle, AlertCircle, Info, AlertTriangle } from "lucide-react";
import ReactMarkdown from "react-markdown";
import { useTranslation } from "react-i18next";
import { api, type ComposerWarning } from "@/lib/api";
import { ConfirmDialog } from "@/components/ConfirmDialog";

interface BlockDef { id: string; label: string; description: string }
interface CategoryDef { id: string; label: string; blocks: BlockDef[] }
interface PresetDef { id: string; label: string; description: string; selected: string[] }

const PRESET_CUSTOM = "__custom__";

export interface ProfileComposerProps {
  /** "me" (default) → /me/agent/composer/*; "admin" → /admin/agents/{agentId}/composer/*; "project" → /projects/{projectId}/composer/* */
  scope?: "me" | "admin" | "project";
  /** Erforderlich wenn scope = "admin". */
  agentId?: string;
  /** Erforderlich wenn scope = "project". */
  projectId?: string;
  /** Zeigt einen dezenten Hinweis, dass AGENT.md Vorrang vor soul.md hat. */
  showSoulHint?: boolean;
}

interface ComposerApi {
  loadBlocks: () => Promise<{categories: CategoryDef[]}>;
  loadPresets: () => Promise<{presets: PresetDef[]}>;
  loadProfile: () => Promise<{selected: string[]; preset: string | null; warnings: ComposerWarning[]; agent_md_exists: boolean; agent_md_mtime_matches: boolean}>;
  preview: (selected: string[], preset: string | null) => Promise<{markdown: string; warnings: ComposerWarning[]; save_blocked: boolean}>;
  save: (selected: string[], preset: string | null) => Promise<{backup_created: boolean; warnings: ComposerWarning[]}>;
}

function buildApi(scope: "me" | "admin" | "project", agentId?: string, projectId?: string): ComposerApi {
  if (scope === "admin") {
    if (!agentId) throw new Error("ProfileComposer: agentId ist bei scope='admin' erforderlich.");
    return {
      loadBlocks: () => api.adminComposerBlocks(agentId),
      loadPresets: () => api.adminComposerPresets(agentId),
      loadProfile: () => api.adminComposerProfile(agentId),
      preview: (sel, p) => api.adminComposerPreview(agentId, sel, p),
      save: (sel, p) => api.adminComposerSave(agentId, sel, p),
    };
  }
  if (scope === "project") {
    if (!projectId) throw new Error("ProfileComposer: projectId ist bei scope='project' erforderlich.");
    return {
      loadBlocks: () => api.projectComposerBlocks(projectId),
      loadPresets: () => api.projectComposerPresets(projectId),
      loadProfile: () => api.projectComposerProfile(projectId),
      preview: (sel, p) => api.projectComposerPreview(projectId, sel, p),
      save: (sel, p) => api.projectComposerSave(projectId, sel, p),
    };
  }
  return {
    loadBlocks: () => api.composerBlocks(),
    loadPresets: () => api.composerPresets(),
    loadProfile: () => api.composerProfile(),
    preview: (sel, p) => api.composerPreview(sel, p),
    save: (sel, p) => api.composerSave(sel, p),
  };
}

export function ProfileComposer({ scope = "me", agentId, projectId, showSoulHint = false }: ProfileComposerProps = {}) {
  const { t } = useTranslation();
  const composerApi = useMemo(() => buildApi(scope, agentId, projectId), [scope, agentId, projectId]);
  const [categories, setCategories] = useState<CategoryDef[]>([]);
  const [presets, setPresets] = useState<PresetDef[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [preset, setPreset] = useState<string>(PRESET_CUSTOM);
  const [preview, setPreview] = useState<string>("");
  const [warnings, setWarnings] = useState<ComposerWarning[]>([]);
  const [saveBlocked, setSaveBlocked] = useState(false);
  const [mtimeMatches, setMtimeMatches] = useState<boolean>(true);
  const [agentMdExists, setAgentMdExists] = useState<boolean>(false);
  const [loading, setLoading] = useState(true);
  const [previewing, setPreviewing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [presetSwitchTarget, setPresetSwitchTarget] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const [blocksR, presetsR, profileR] = await Promise.all([
          composerApi.loadBlocks(),
          composerApi.loadPresets(),
          composerApi.loadProfile(),
        ]);
        setCategories(blocksR.categories);
        setPresets(presetsR.presets);
        setSelected(new Set(profileR.selected));
        setPreset(profileR.preset ?? PRESET_CUSTOM);
        setWarnings(profileR.warnings);
        setSaveBlocked(profileR.warnings.some(w => w.severity === "error"));
        setAgentMdExists(profileR.agent_md_exists);
        setMtimeMatches(profileR.agent_md_mtime_matches);
      } catch (e) {
        setMsg({ kind: "err", text: e instanceof Error ? e.message : String(e) });
      } finally {
        setLoading(false);
      }
    })();
  }, [composerApi]);

  const selectedCount = selected.size;
  const hasSelection = selectedCount > 0;

  function toggle(id: string) {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
    // Manuelle Änderung → Preset auf Custom zurücksetzen
    if (preset !== PRESET_CUSTOM) setPreset(PRESET_CUSTOM);
    setPreview("");
  }

  function requestPresetSwitch(targetId: string) {
    if (targetId === preset) return;
    if (targetId === PRESET_CUSTOM) {
      setPreset(PRESET_CUSTOM);
      return;
    }
    if (selected.size > 0) {
      setPresetSwitchTarget(targetId);
    } else {
      applyPreset(targetId);
    }
  }

  function applyPreset(targetId: string) {
    const p = presets.find(p => p.id === targetId);
    if (!p) return;
    setSelected(new Set(p.selected));
    setPreset(targetId);
    setPreview("");
    setPresetSwitchTarget(null);
  }

  async function refreshPreview() {
    setPreviewing(true); setMsg(null);
    try {
      const r = await composerApi.preview(
        Array.from(selected),
        preset === PRESET_CUSTOM ? null : preset,
      );
      setPreview(r.markdown);
      setWarnings(r.warnings);
      setSaveBlocked(r.save_blocked);
    } catch (e) {
      setMsg({ kind: "err", text: e instanceof Error ? e.message : String(e) });
    } finally {
      setPreviewing(false);
    }
  }

  async function doSave() {
    setConfirmOpen(false);
    setSaving(true); setMsg(null);
    try {
      const r = await composerApi.save(
        Array.from(selected),
        preset === PRESET_CUSTOM ? null : preset,
      );
      setMsg({
        kind: "ok",
        text: t("composer.saveOk", {
          defaultValue: "AGENT.md gespeichert ({{count}} Bausteine).{{backup}}",
          count: selectedCount,
          backup: r.backup_created
            ? " " + t("composer.saveBackup", { defaultValue: "Vorheriger Stand liegt in AGENT.md.backup." })
            : "",
        }),
      });
      setWarnings(r.warnings);
      setSaveBlocked(false);
      setMtimeMatches(true);
      setAgentMdExists(true);
    } catch (e) {
      setMsg({ kind: "err", text: e instanceof Error ? e.message : String(e) });
    } finally {
      setSaving(false);
    }
  }

  const summaryText = useMemo(() => {
    if (!hasSelection) return t("composer.noSelection", { defaultValue: "Keine Bausteine ausgewählt." });
    return t("composer.selectionCount", {
      defaultValue: "{{count}} Baustein(e) ausgewählt",
      count: selectedCount,
    });
  }, [selectedCount, hasSelection, t]);

  if (loading) {
    return <div className="text-xs text-muted-foreground">{t("composer.loading", { defaultValue: "Lade Bausteine..." })}</div>;
  }

  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-foreground flex items-center gap-2">
          <Sparkles className="h-4 w-4" />
          {t("composer.title", { defaultValue: "Profil-Composer" })}
        </h2>
        <span className="text-xs text-muted-foreground">{summaryText}</span>
      </div>

      <p className="text-xs text-muted-foreground leading-relaxed">
        {t("composer.intro", {
          defaultValue:
            "Wähle Bausteine oder ein Preset, um eine AGENT.md-Datei für deinen persönlichen Agent zu erzeugen. Die Auswahl wird in agent_profile.yaml gespeichert. soul.md und agent.yaml bleiben unverändert.",
        })}
      </p>

      {/* Preset-Auswahl */}
      <div className="rounded-md border border-border/70 bg-background p-3 space-y-2">
        <label className="text-xs font-semibold text-foreground">
          {t("composer.presetLabel", { defaultValue: "Preset" })}
        </label>
        <select
          value={preset}
          onChange={e => requestPresetSwitch(e.target.value)}
          className="w-full px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary"
        >
          <option value={PRESET_CUSTOM}>
            {t("composer.presetCustom", { defaultValue: "Custom (eigene Auswahl)" })}
          </option>
          {presets.map(p => (
            <option key={p.id} value={p.id}>{p.label}</option>
          ))}
        </select>
        {preset !== PRESET_CUSTOM && (
          <p className="text-xs text-muted-foreground">
            {presets.find(p => p.id === preset)?.description}
          </p>
        )}
      </div>

      {/* soul.md Hinweis — AGENT.md hat Vorrang */}
      {showSoulHint && (
        <div className="flex items-start gap-2 text-xs rounded-md border border-border/70 bg-muted/30 text-muted-foreground p-2">
          <Info className="h-3.5 w-3.5 mt-0.5 flex-shrink-0" />
          <span>
            {t("composer.soulHint", {
              defaultValue:
                "AGENT.md hat Vorrang vor soul.md. Diese Ansicht bearbeitet nur AGENT.md und agent_profile.yaml — agent.yaml, soul.md, tools[] und execution_modes bleiben unverändert.",
            })}
          </span>
        </div>
      )}

      {/* Externer AGENT.md-Hinweis */}
      {agentMdExists && !mtimeMatches && (
        <div className="flex items-start gap-2 text-xs rounded-md border border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300 p-2">
          <AlertTriangle className="h-3.5 w-3.5 mt-0.5 flex-shrink-0" />
          <span>
            {t("composer.mtimeDrift", {
              defaultValue:
                "AGENT.md wurde seit dem letzten Composer-Save extern geändert. Speichern überschreibt den aktuellen Stand; ein Backup wird als AGENT.md.backup angelegt.",
            })}
          </span>
        </div>
      )}

      {/* Bausteine */}
      <div className="space-y-4">
        {categories.map(cat => (
          <div key={cat.id} className="rounded-md border border-border/70 bg-muted/20 p-3 space-y-2">
            <div className="text-xs font-semibold text-foreground">{cat.label}</div>
            <div className="grid gap-1.5">
              {cat.blocks.map(b => (
                <label
                  key={b.id}
                  className="flex items-start gap-2 rounded px-2 py-1.5 text-xs cursor-pointer hover:bg-muted/40 transition"
                >
                  <input
                    type="checkbox"
                    className="mt-0.5 flex-shrink-0"
                    checked={selected.has(b.id)}
                    onChange={() => toggle(b.id)}
                  />
                  <span className="flex-1 min-w-0">
                    <span className="font-medium block">{b.label}</span>
                    <span className="text-muted-foreground block">{b.description}</span>
                  </span>
                </label>
              ))}
            </div>
          </div>
        ))}
      </div>

      {/* Warnings */}
      {warnings.length > 0 && (
        <div className="space-y-1.5">
          {warnings.map((w, i) => {
            const tone =
              w.severity === "error"
                ? "border-destructive/40 bg-destructive/10 text-destructive"
                : w.severity === "warning"
                ? "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300"
                : "border-border/70 bg-muted/30 text-muted-foreground";
            const Icon = w.severity === "error" ? AlertCircle : w.severity === "warning" ? AlertTriangle : Info;
            return (
              <div key={`${w.rule}-${i}`} className={`flex items-start gap-2 text-xs rounded-md border p-2 ${tone}`}>
                <Icon className="h-3.5 w-3.5 mt-0.5 flex-shrink-0" />
                <span>{w.message}</span>
              </div>
            );
          })}
        </div>
      )}

      {/* Aktionen */}
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={refreshPreview}
          disabled={!hasSelection || previewing}
          className="inline-flex items-center gap-1.5 px-3 py-2 text-xs border rounded-md hover:bg-accent transition disabled:opacity-50"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${previewing ? "animate-spin" : ""}`} />
          {t("composer.refreshPreview", { defaultValue: "Vorschau aktualisieren" })}
        </button>
        <button
          type="button"
          onClick={() => setConfirmOpen(true)}
          disabled={!hasSelection || saving || saveBlocked}
          className="inline-flex items-center gap-1.5 px-3 py-2 text-xs rounded-md bg-primary text-primary-foreground hover:opacity-90 transition disabled:opacity-50"
        >
          <Save className="h-3.5 w-3.5" />
          {t("composer.save", { defaultValue: "In AGENT.md übernehmen" })}
        </button>
      </div>

      {msg && (
        <div
          className={`flex items-start gap-2 text-xs rounded-md border p-2 ${
            msg.kind === "ok"
              ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
              : "border-destructive/40 bg-destructive/10 text-destructive"
          }`}
        >
          {msg.kind === "ok" ? <CheckCircle className="h-3.5 w-3.5 mt-0.5" /> : <AlertCircle className="h-3.5 w-3.5 mt-0.5" />}
          <span>{msg.text}</span>
        </div>
      )}

      {preview && (
        <div className="rounded-md border border-border/70 bg-background p-3">
          <div className="text-xs font-semibold text-muted-foreground mb-2">
            {t("composer.previewTitle", { defaultValue: "Vorschau (Markdown)" })}
          </div>
          <div className="prose prose-sm dark:prose-invert max-w-none text-xs">
            <ReactMarkdown>{preview}</ReactMarkdown>
          </div>
        </div>
      )}

      <ConfirmDialog
        open={confirmOpen}
        variant="danger"
        title={t("composer.confirmTitle", { defaultValue: "AGENT.md überschreiben?" })}
        message={t("composer.confirmMsg", {
          defaultValue:
            "Eine bestehende AGENT.md wird überschrieben. Der vorherige Stand wird automatisch als AGENT.md.backup gesichert. soul.md und agent.yaml bleiben unverändert.",
        })}
        confirmLabel={t("composer.confirmOk", { defaultValue: "Überschreiben" })}
        cancelLabel={t("composer.confirmCancel", { defaultValue: "Abbrechen" })}
        onCancel={() => setConfirmOpen(false)}
        onConfirm={doSave}
      />

      <ConfirmDialog
        open={presetSwitchTarget !== null}
        variant="default"
        title={t("composer.presetSwitchTitle", { defaultValue: "Preset übernehmen?" })}
        message={t("composer.presetSwitchMsg", {
          defaultValue:
            "Preset-Auswahl ersetzt deine aktuelle Bausteinauswahl. Fortfahren?",
        })}
        confirmLabel={t("composer.presetSwitchOk", { defaultValue: "Übernehmen" })}
        cancelLabel={t("composer.presetSwitchCancel", { defaultValue: "Abbrechen" })}
        onCancel={() => setPresetSwitchTarget(null)}
        onConfirm={() => presetSwitchTarget && applyPreset(presetSwitchTarget)}
      />
    </section>
  );
}
